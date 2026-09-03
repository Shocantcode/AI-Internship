import json
import logging
import asyncio
from datetime import datetime
from types import SimpleNamespace
from mcp.server.mcpserver import Context
from google import genai
from google.genai import types
import os
from pathlib import Path
import time
from collections import OrderedDict

from tools.news_tools import _collect
from tools.rag_tools import _load_rag, _source_url, normalize_tool_result
from tools.forecasting_tools import _read_parquet, _s3_client, _prefix
from tools.dag_tools import _client as _airflow_client
from tools.nexus_prompt import LENGTH_INSTRUCTIONS, SYSTEM_PROMPT

LOG = logging.getLogger(__name__)
_EXECUTIONS = OrderedDict()
_EXECUTIONS_LIMIT = 100


def _execution_stats(executions):
    completed = sum(item.get("status") == "completed" for item in executions)
    failed = sum(item.get("status") == "failed" for item in executions)
    durations = [item.get("duration_ms") for item in executions if isinstance(item.get("duration_ms"), (int, float))]
    return {
        "total_sessions_today": len(executions),
        "successful_sessions": completed,
        "failed_sessions": failed,
        "tools_used": sum(len(item.get("tools", [])) for item in executions),
        "average_response_time_ms": round(sum(durations) / len(durations)) if durations else 0,
        "success_rate": round(completed / len(executions) * 100, 1) if executions else 0,
        "error_count": sum(1 for item in executions for activity in item.get("activities", []) if activity.get("status") == "failed"),
        "active_executions": sum(item.get("status") == "running" for item in executions),
    }


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _json_safe(value.dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return _json_safe(vars(value))
        except Exception:
            pass
    return str(value)


def _activity_error(error, stage=None):
    if not error:
        return None
    if isinstance(error, dict):
        normalized = dict(error)
    else:
        normalized = {"message": str(error)}
    normalized.setdefault("code", "TOOL_ERROR")
    normalized.setdefault("stage", stage)
    normalized.setdefault("message", str(error))
    normalized.setdefault("retryable", normalized.get("http_status") in (429, 502, 503, 504))
    return normalized


async def _call_gemini_with_retry(chat, message, max_attempts=5):
    """Call Gemini API with retry logic for 503 errors.
    
    Args:
        chat: Google Generative AI chat session
        message: The message to send
        max_attempts: Maximum number of retry attempts
        
    Returns:
        The response from Gemini API
        
    Raises:
        Exception: If all retries fail
    """
    for attempt in range(1, max_attempts + 1):
        try:
            LOG.info("[GEMINI] Attempt %d/%d to send message to Gemini API", attempt, max_attempts)
            response = await asyncio.to_thread(chat.send_message, message)
            LOG.info("[GEMINI] Attempt %d succeeded", attempt)
            return response
        except Exception as e:
            error_str = str(e)
            # Check if it's a 503 error (service unavailable)
            is_503 = "503" in error_str or "UNAVAILABLE" in error_str
            
            if is_503 and attempt < max_attempts:
                # Exponential backoff: 1, 2, 4, 8, 16 seconds
                wait_time = 2 ** (attempt - 1)
                LOG.warning("[GEMINI] Attempt %d received 503 error, retrying in %d seconds: %s", 
                           attempt, wait_time, error_str[:100])
                await asyncio.sleep(wait_time)
            else:
                # Not a 503 or this was the last attempt
                LOG.error("[GEMINI] Attempt %d failed: %s", attempt, error_str)
                if attempt == max_attempts:
                    raise
                # For other errors, just fail immediately
                raise



def register_orchestrator_tools(mcp):
    @mcp.tool()
    def get_execution_history(execution_id: str | None = None, limit: int = 50) -> dict:
        """Return real Copilot execution history and derived diagnostic statistics."""
        executions = list(_EXECUTIONS.values())
        if execution_id:
            executions = [item for item in executions if item.get("execution_id") == execution_id]
        executions = executions[-max(1, min(limit, 100)):]
        return {"status": "success", "executions": executions, "stats": _execution_stats(list(_EXECUTIONS.values()))}

    @mcp.tool()
    async def ask_nexus(message: str, conversation_id: str | None = None, response_length: str = "medium", debug: bool = False, request_id: str | None = None, ctx: Context = None, images: list[dict] | None = None, documents: list[dict] | None = None) -> dict:
        """Centralized Orchestrator for Nexus AI."""
        if not request_id:
            request_id = f"req_{int(datetime.now().timestamp())}"
        response_length = response_length if response_length in LENGTH_INSTRUCTIONS else "medium"
        activity_log = []
        execution = {
            "execution_id": request_id,
            "query": message,
            "started_at": datetime.now().astimezone().isoformat(),
            "completed_at": None,
            "duration_ms": None,
            "status": "running",
            "activities": activity_log,
            "tools": [],
        }
        _EXECUTIONS[request_id] = execution
        while len(_EXECUTIONS) > _EXECUTIONS_LIMIT:
            _EXECUTIONS.popitem(last=False)
        activity_by_id = {}
        activity_attempts = {}
        seen_event_ids = set()
        LOG.info("[CHAT BACKEND] request received request_id=%s question=%s", request_id, message)
        computer_use_root = Path(os.getenv("COMPUTER_USE_ROOT", str(Path(__file__).parents[2] / "Computer Use")))
        debug_folder = Path(os.getenv("CHAT_DEBUG_FOLDER", str(computer_use_root / "DebuggingFolder")))
        request_debug_folder = debug_folder / request_id
        request_debug_folder.mkdir(parents=True, exist_ok=True)
        (request_debug_folder / "request.json").write_text(json.dumps({"request_id": request_id, "question": message, "started_at": datetime.now().astimezone().isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")

        def finish_execution(final_status):
            """Mark this execution as done and compute wall-clock duration."""
            execution["status"] = final_status
            execution["completed_at"] = datetime.now().astimezone().isoformat()
            execution["duration_ms"] = int(
                (datetime.now().astimezone() - datetime.fromisoformat(execution["started_at"])).total_seconds() * 1000
            )

        async def emit(stage, status, msg, metadata=None, result_count=None, duration_ms=None, error=None, activity_id=None, event_id=None):
            metadata = metadata or {}
            base_id = str(activity_id or metadata.get("activity_id") or stage)
            existing = activity_by_id.get(base_id)
            if status == "running" and existing and existing.get("status") in ("completed", "failed"):
                activity_attempts[base_id] = activity_attempts.get(base_id, 1) + 1
                base_id = f"{base_id}#{activity_attempts[base_id]}"
            elif base_id not in activity_attempts:
                activity_attempts[base_id] = 1
            if event_id and event_id in seen_event_ids:
                return
            if event_id:
                seen_event_ids.add(event_id)
            existing = activity_by_id.get(base_id)
            if existing and existing.get("status") in ("completed", "failed") and status != existing.get("status"):
                LOG.warning("[CHAT][%s][activity:%s] ignoring invalid transition %s -> %s", request_id, base_id, existing.get("status"), status)
                return
            now = datetime.now().astimezone().isoformat()
            payload = {
                "type": "activity",
                "request_id": request_id,
                "execution_id": request_id,
                "id": base_id,
                "activity_id": base_id,
                "event_id": event_id,
                "stage": stage,
                "action": stage,
                "status": status,
                "label": msg,
                "timestamp": now,
                "started_at": existing.get("started_at", now) if existing else now,
                "completed_at": now if status in ("completed", "failed", "skipped") else None,
                "tool": metadata.get("tool") if metadata else None,
            }
            if result_count is not None:
                payload["result_count"] = result_count
            if duration_ms is not None:
                payload["duration_ms"] = duration_ms
            if metadata:
                payload["metadata"] = metadata
            normalized_error = _activity_error(error or metadata.get("error"), stage)
            if normalized_error:
                payload["error"] = normalized_error
            if existing:
                activity_log[activity_log.index(existing)] = payload
            else:
                activity_log.append(payload)
            activity_by_id[base_id] = payload
            if payload.get("tool") and payload["tool"] not in execution["tools"]:
                execution["tools"].append(payload["tool"])
            execution["activities"] = activity_log
            LOG.info("[exec:%s][activity:%s] %s", request_id, base_id, status)
            (request_debug_folder / "activity.json").write_text(json.dumps(activity_log, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            if ctx:
                if hasattr(ctx, "log"):
                    await ctx.log("info", payload, logger_name="activity")
                else:
                    await ctx.session.send_notification("activity", payload)

        await emit("understanding", "running", "Understanding the question")

        # Diagnostic logging for image/document detection
        LOG.info("[IMAGE_DOCUMENT_DETECTION] images_present=%s images_value=%s images_type=%s images_len=%s documents_present=%s documents_value=%s documents_type=%s documents_len=%s", bool(images), images, type(images).__name__, len(images) if isinstance(images, (list, tuple)) else "N/A", bool(documents), documents, type(documents).__name__, len(documents) if isinstance(documents, (list, tuple)) else "N/A")

        if images:
            await emit("routing", "completed", "Image attachment detected; routing to Vision Processing", metadata={"tool": "process_image", "route": "vision", "image_count": len(images)})
            (request_debug_folder / "routing.json").write_text(json.dumps({"route": "vision", "reason": "image attachment present", "image_count": len(images), "images": images}, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                from tools.vision_tools import process_image
                result = process_image(
                    images[0].get("filename") or "uploaded_image.png",
                    content_base64=images[0].get("content_base64"),
                    question=message,
                    images=images,
                )
                LOG.info("[PREPARE_ANSWER] vision_result_present=%s vision_result_length=%s answer_length=%s", isinstance(result, dict) and bool(result.get("result")), len(str(result.get("result") or "")), len(str(result.get("result") or "")))
            except Exception as exc:
                result = {"status": "error", "service": "vision", "message": str(exc), "filename": images[0].get("filename") if images else "uploaded_image.png"}
                LOG.exception("[VISION] process_image_exception request_id=%s", request_id)

            if isinstance(result, dict) and result.get("status") == "success":
                answer = str(result.get("result") or "Informasi tersebut tidak ditemukan pada gambar.")
                finish_execution("completed")
                return {
                    "ok": True,
                    "success": True,
                    "answer": answer,
                    "sources": [],
                    "response_length": response_length,
                    "status": "completed",
                    "activity": activity_log,
                    "activities": activity_log,
                    "request_id": request_id,
                    "metadata": {"route": "vision", "image_count": len(images), "evidence_count": 0},
                    "evidence": [{"tool": "process_image", "result": result}],
                }

            error_message = result.get("message") if isinstance(result, dict) else str(result)
            await emit("vision_processing", "failed", "Vision Processing failed", metadata={"tool": "process_image", "error": {"code": "VISION_PROCESSING_FAILED", "message": error_message}})
            finish_execution("failed")
            return {"ok": False, "success": False, "answer": error_message or "Informasi tersebut tidak ditemukan pada gambar.", "status": "failed", "activity": activity_log, "activities": activity_log, "request_id": request_id, "error": {"stage": "vision_processing", "code": "VISION_PROCESSING_FAILED", "message": error_message}, "metadata": {"route": "vision", "image_count": len(images)}}
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            await emit("understanding", "failed", "Understanding the question")
            finish_execution("failed")
            return {"ok": False, "success": False, "answer": None, "status": "failed", "activities": activity_log, "activity": activity_log, "error": {"stage": "understanding", "code": "CONFIGURATION_ERROR", "message": "GEMINI_API_KEY missing."}, "request_id": request_id}

        client = genai.Client(
            api_key=api_key,
            http_options={"timeout": 300000},
        )
        
        # Tools schema for LLM
        tool_schema = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="collect_detik_finance_news",
                    description="Perform autonomous external web research for current news or market information on Detik Finance.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={"query": types.Schema(type=types.Type.STRING, description="The search query.")},
                        required=["query"]
                    )
                ),
                types.FunctionDeclaration(
                    name="search_rag",
                    description="Semantic search across internal indexed knowledge (e.g. documents, internal reports).",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={"query": types.Schema(type=types.Type.STRING, description="The search query.")},
                        required=["query"]
                    )
                ),
                types.FunctionDeclaration(
                    name="get_forecasting_preview",
                    description="Retrieve business forecasts generated from structured business data (e.g. 14 day revenue forecast).",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={"limit": types.Schema(type=types.Type.INTEGER, description="Days to forecast.")},
                    )
                ),
                types.FunctionDeclaration(
                    name="run_dag",
                    description="Trigger a data pipeline / ETL process.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={"dag_id": types.Schema(type=types.Type.STRING, description="The DAG ID to run.")},
                        required=["dag_id"]
                    )
                )
            ]
        )
        
        start_time = datetime.now()
        
        try:
            # 1. Understanding and tool selection are performed by the model.
            chat = client.chats.create(model=os.getenv("GEMINI_MODEL_NAME", "gemini-3.5-flash"), config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=[tool_schema],
                temperature=0.1
            ))
            
            LOG.info("[CHAT][%s] Calling Gemini API for tool selection", request_id)
            response = await _call_gemini_with_retry(chat, message, max_attempts=5)
            LOG.info("[CHAT][%s] Gemini response received: %s", request_id, type(response))
            
            try:
                selected_tools = [call.name for call in (response.function_calls or [])]
                LOG.info("[CHAT][%s] Selected tools: %s", request_id, selected_tools)
            except Exception as e:
                LOG.exception("[CHAT][%s] Error extracting tool names from response", request_id)
                selected_tools = []
            
            try:
                duration_ms = int((datetime.now()-start_time).total_seconds()*1000)
                metadata = {"selected_tools": selected_tools} if debug else None
                LOG.info("[CHAT][%s] About to emit understanding:completed with duration=%d, metadata=%s", request_id, duration_ms, metadata)
                await emit("understanding", "completed", "Understanding the question", metadata=metadata, duration_ms=duration_ms)
                LOG.info("[CHAT][%s] Successfully emitted understanding:completed", request_id)
            except Exception as emit_error:
                LOG.exception("[CHAT][%s] Error emitting understanding:completed: %s", request_id, emit_error)
                # Try to emit failed instead
                try:
                    await emit("understanding", "failed", f"Error emitting completion: {emit_error}")
                except:
                    LOG.exception("[CHAT][%s] Also failed to emit understanding:failed", request_id)
            
            evidence = []
            sources = []

            if not response.function_calls:
                response.function_calls = [SimpleNamespace(name="search_rag", args={"query": message})]

            # 2. Execute Tools if requested
            if response.function_calls:
                LOG.info("[CHAT][%s] Executing %d tools", request_id, len(response.function_calls))
                for call in response.function_calls:
                    tool_name = call.name
                    args = call.args or {}
                    
                    if tool_name == "collect_detik_finance_news":
                        query = args.get("query", message)
                        await emit("web_research", "running", "Searching current information", metadata={"tool": tool_name, "query": query} if debug else {"tool": tool_name})
                        t_start = datetime.now()
                        
                        def report_progress(progress, total, msg, payload=None):
                            if payload and ctx:
                                asyncio.run_coroutine_threadsafe(ctx.log("info", payload, logger_name="activity"), asyncio.get_running_loop())
                                
                        try:
                            result = await asyncio.to_thread(_collect, query, 5, 120, report_progress, request_id)
                            dur = int((datetime.now()-t_start).total_seconds()*1000)
                            safe_articles = _json_safe(result.get("articles", [])) if isinstance(result, dict) else []
                            if result.get("status") in ("success", "partial") and safe_articles:
                                evidence.append({"tool": tool_name, "result": safe_articles})
                                await emit("web_research", "completed", "Searching current information", duration_ms=dur, result_count=len(safe_articles))
                                for article in safe_articles:
                                    if not isinstance(article, dict):
                                        continue
                                    sources.append({
                                        "title": article.get("title"),
                                        "url": article.get("url"),
                                        "domain": article.get("domain", "finance.detik.com"),
                                        "source_type": "external_news",
                                        "storage": "minio",
                                        "bucket": result.get("storage", {}).get("bucket"),
                                        "object_key": next((item.get("object_key") for item in result.get("storage", {}).get("objects", []) if item.get("url") == article.get("url")), None),
                                        "published_at": article.get("published_at"),
                                    })
                            else:
                                await emit("web_research", "failed", "Searching current information", duration_ms=dur, error=result.get("message"))
                        except Exception as e:
                            await emit("web_research", "failed", "Searching current information", duration_ms=int((datetime.now()-t_start).total_seconds()*1000), error=e)
                            
                    elif tool_name == "search_rag":
                        query = args.get("query", message)
                        await emit("search_rag", "running", "Searching documents")
                        t_start = datetime.now()
                        try:
                            from tools.rag_tools import search_rag as search_rag_tool
                            rag_res = await search_rag_tool(query, top_k=5, request_id=request_id, ctx=ctx)
                            dur = int((datetime.now()-t_start).total_seconds()*1000)
                            if rag_res.get("success") and rag_res.get("results"):
                                evidence.append({"tool": tool_name, "result": rag_res["results"]})
                                await emit("search_rag", "completed", "Searching documents", duration_ms=dur, result_count=len(rag_res["results"]))
                                for res in rag_res["results"]:
                                    metadata = res.get("metadata") or {}
                                    key = res.get("object_key") or metadata.get("object_key")
                                    url = res.get("url") or metadata.get("url")
                                    sources.append({
                                        "title": res.get("document_name") or metadata.get("title") or metadata.get("filename") or "Internal Document",
                                        "source_type": "internal_document",
                                        "url": url,
                                        "object_key": key,
                                        "bucket": res.get("bucket") or metadata.get("bucket"),
                                        "page": res.get("page") or metadata.get("page"),
                                        "relevance": res.get("relevance") or metadata.get("relevance") or metadata.get("relevance_score"),
                                        "document_name": res.get("document_name") or metadata.get("filename")
                                    })
                            else:
                                await emit("search_rag", "failed", "Searching documents", metadata={"tool": tool_name, "query": query, "error": rag_res.get("error", {})}, duration_ms=dur)
                        except Exception as e:
                            await emit("search_rag", "failed", "Searching documents", metadata={"tool": tool_name, "query": query, "error": {"code": type(e).__name__, "message": str(e)}})
                            
                    elif tool_name == "get_forecasting_preview":
                        limit = args.get("limit", 14)
                        await emit("forecast", "running", "Loading business forecast")
                        t_start = datetime.now()
                        try:
                            client_s3, bucket = await asyncio.to_thread(_s3_client)
                            prefix = await asyncio.to_thread(_prefix)
                            response_s3 = await asyncio.to_thread(client_s3.list_objects_v2, Bucket=bucket, Prefix=prefix)
                            if "Contents" in response_s3:
                                parquet_keys = [obj["Key"] for obj in response_s3["Contents"] if obj["Key"].endswith(".parquet")]
                                if parquet_keys:
                                    latest_key = max(parquet_keys)
                                    import pandas as pd
                                    df = await asyncio.to_thread(_read_parquet, client_s3, bucket, latest_key, limit)
                                    evidence.append({"tool": tool_name, "result": df.to_dict(orient="records")})
                                    await emit("forecast", "completed", "Loading business forecast", duration_ms=int((datetime.now()-t_start).total_seconds()*1000))
                            else:
                                await emit("forecast", "failed", "Loading business forecast", error="No forecast parquet was found")
                        except Exception as e:
                            await emit("forecast", "failed", "Loading business forecast", error=e)
                            
                    elif tool_name == "run_dag":
                        dag_id = args.get("dag_id")
                        await emit("pipeline", "running", f"Running pipeline {dag_id}")
                        t_start = datetime.now()
                        try:
                            http = await asyncio.to_thread(_airflow_client)
                            resp = await asyncio.to_thread(http.post, f"/api/v1/dags/{dag_id}/dagRuns", json={"conf": {}})
                            if resp.status_code == 200:
                                evidence.append({"tool": tool_name, "result": "DAG triggered successfully."})
                                await emit("pipeline", "completed", f"Running pipeline {dag_id}", duration_ms=int((datetime.now()-t_start).total_seconds()*1000))
                            else:
                                await emit("pipeline", "failed", f"Running pipeline {dag_id}", error=f"Airflow returned HTTP {resp.status_code}")
                        except Exception as e:
                            await emit("pipeline", "failed", f"Running pipeline {dag_id}", error=e)

            # 3. Final Answer Synthesis
            if not evidence:
                await emit("search_rag", "running", "Searching documents")
                try:
                    from tools.rag_tools import search_rag as search_rag_tool, ask_rag
                    search_res = await search_rag_tool(message, top_k=5, request_id=request_id, ctx=ctx)
                    if search_res.get("success") and search_res.get("results"):
                        evidence = [{"tool": "search_rag", "result": search_res["results"]}]
                        sources = [{
                            "title": item.get("document_name") or item.get("metadata", {}).get("filename") or "Internal Document",
                            "source_type": "internal_document",
                            "url": item.get("url") or (item.get("metadata") or {}).get("url"),
                            "object_key": item.get("object_key") or (item.get("metadata") or {}).get("object_key"),
                            "bucket": item.get("bucket") or (item.get("metadata") or {}).get("bucket"),
                            "page": item.get("page") or (item.get("metadata") or {}).get("page"),
                            "relevance": item.get("relevance") or (item.get("metadata") or {}).get("relevance") or (item.get("metadata") or {}).get("relevance_score"),
                        } for item in search_res["results"]]
                        await emit("search_rag", "completed", "Searching documents", result_count=len(search_res["results"]))
                        await emit("prepare_answer", "running", "Preparing answer")
                        t_prepare_start = datetime.now()
                        answer_payload = await ask_rag(message, evidence=search_res["results"], request_id=request_id, ctx=ctx)
                        t_prepare_dur = int((datetime.now() - t_prepare_start).total_seconds() * 1000)
                        if answer_payload.get("success") and answer_payload.get("answer"):
                            await emit("prepare_answer", "completed", "Preparing answer", duration_ms=t_prepare_dur)
                            finish_execution("completed")
                            return {
                                "ok": True,
                                "success": True,
                                "answer": answer_payload["answer"],
                                "sources": sources,
                                "response_length": response_length,
                                "status": "completed",
                                "activity": activity_log,
                                "activities": activity_log,
                                "request_id": request_id,
                                "evidence": evidence,
                                "metadata": {"rag_used": True, "external_search_used": False, "evidence_count": len(search_res["results"])},
                            }
                        answer_error = answer_payload.get("error", {"code": "NO_RELEVANT_INFORMATION", "message": "No relevant information found in Documents"})
                        await emit("prepare_answer", "failed", "Preparing answer", duration_ms=t_prepare_dur, error=answer_error)
                        finish_execution("failed")
                        return {"ok": False, "success": False, "answer": answer_payload.get("answer") or "No relevant information found in Documents", "error": answer_error, "status": "failed", "sources": sources, "activity": activity_log, "activities": activity_log, "request_id": request_id}
                    await emit("search_rag", "failed", "Searching documents", metadata={"tool": "search_rag", "query": message, "error": search_res.get("error", {})})
                except Exception as exc:
                    await emit("search_rag", "failed", "Searching documents", metadata={"tool": "search_rag", "query": message, "error": {"code": type(exc).__name__, "message": str(exc)}})
                    LOG.warning("[CHAT][%s] fallback RAG search failed; continuing to final synthesis: %s", request_id, exc)

            await emit("prepare_answer", "running", "Preparing answer")
            t_start = datetime.now()
            
            length_instruction = LENGTH_INSTRUCTIONS[response_length]
            synthesis_prompt = f"Question: {message}\n\nRetrieved Evidence:\n{json.dumps(evidence, indent=2, default=str)}\n\nFormat Requirement: {length_instruction}\n\nRespond to the user naturally based only on the evidence."
            
            try:
                final_response = await _call_gemini_with_retry(chat, synthesis_prompt, max_attempts=5)
                answer = getattr(final_response, "text", "") or ""
                if not answer.strip():
                    raise ValueError("LLM returned an empty answer")
                prepare_dur = int((datetime.now()-t_start).total_seconds()*1000)
                await emit("prepare_answer", "completed", "Preparing answer", duration_ms=prepare_dur)
                finish_execution("completed")
                return {
                    "ok": True,
                    "success": True,
                    "answer": answer,
                    "sources": sources,
                    "response_length": response_length,
                    "status": "completed",
                    "activity": activity_log,
                    "activities": activity_log,
                    "request_id": request_id,
                    "evidence": evidence,
                    "metadata": {"rag_used": any(item.get("tool") == "search_rag" for item in evidence), "external_search_used": any(item.get("tool") == "collect_detik_finance_news" for item in evidence), "evidence_count": sum(len(item.get("result", [])) if isinstance(item.get("result"), list) else 1 for item in evidence)},
                }
            except Exception as e:
                error_str = str(e)
                prepare_dur = int((datetime.now()-t_start).total_seconds()*1000)
                # Classify Gemini API errors into structured objects
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    gemini_error = {"code": "GEMINI_429", "stage": "prepare_answer", "provider": "gemini", "http_status": 429, "message": "AI service quota exhausted — rate limit reached.", "retryable": True}
                elif "503" in error_str or "UNAVAILABLE" in error_str:
                    gemini_error = {"code": "GEMINI_503", "stage": "prepare_answer", "provider": "gemini", "http_status": 503, "message": error_str[:300], "retryable": True}
                elif "500" in error_str or "INTERNAL" in error_str:
                    gemini_error = {"code": "GEMINI_500", "stage": "prepare_answer", "provider": "gemini", "http_status": 500, "message": error_str[:300], "retryable": False}
                elif "deadline" in error_str.lower() or "timeout" in error_str.lower():
                    gemini_error = {"code": "GEMINI_TIMEOUT", "stage": "prepare_answer", "provider": "gemini", "http_status": None, "message": "Request timed out.", "retryable": True}
                else:
                    gemini_error = {"code": "ANSWER_GENERATION_FAILED", "stage": "prepare_answer", "message": error_str[:300], "type": type(e).__name__, "retryable": False}
                await emit("prepare_answer", "failed", "Preparing answer", duration_ms=prepare_dur, error=gemini_error)
                finish_execution("failed")
                return {"ok": False, "success": False, "answer": None, "error": gemini_error, "status": "failed", "activity": activity_log, "activities": activity_log, "request_id": request_id, "sources": sources}

        except Exception as e:
            await emit("understanding", "failed", "Understanding the question")
            LOG.exception("[CHAT][%s] ERROR understanding_question", request_id)
            finish_execution("failed")
            return {"ok": False, "success": False, "answer": None, "status": "error", "activity": activity_log, "activities": activity_log, "error": {"stage": "understanding", "code": type(e).__name__, "message": str(e)}, "request_id": request_id}
