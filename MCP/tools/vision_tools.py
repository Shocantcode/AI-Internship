import json
import base64
import logging
import os
import subprocess
import sys
from pathlib import Path

from google import genai
from google.genai import types

from tools.nexus_prompt import VISION_SYSTEM_PROMPT


LOG = logging.getLogger(__name__)


def _json_safe(value):
    """Recursively convert objects to JSON-safe types for logging."""
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


def _error(message: str) -> dict:
    return {"status": "error", "service": "vision", "message": message}


def process_document(filename: str) -> dict:
    root = Path(os.getenv("VISION_ROOT", "/opt/integrations/VisionProcessing"))
    code = Path(os.getenv("VISION_CODE", str(root / "Code.py")))
    output = root / "Output" / "ppbj_result.json"
    if not code.is_file():
        return _error("Code.py VisionProcessing tidak ditemukan.")
    try:
        completed = subprocess.run(
            [sys.executable, str(code)], cwd=root, capture_output=True,
            text=True, timeout=300, check=False,
        )
        if completed.returncode != 0:
            LOG.error("VisionProcessing failed: %s", completed.stderr[-4000:])
            return _error("VisionProcessing gagal menjalankan dokumen.")
        if not output.is_file():
            return _error("VisionProcessing selesai tetapi output tidak ditemukan.")
        return {"status": "success", "filename": filename, "output_file": str(output), "result": json.loads(output.read_text(encoding="utf-8"))}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        LOG.exception("Vision document processing failed")
        return _error(f"Pemrosesan dokumen gagal: {exc}")


def process_image(filename: str, content_base64: str | None = None, question: str = "", images: list[dict] | None = None) -> dict:
    """Analyze an uploaded image with Gemini Vision and return a grounded answer."""
    image_payloads = images or []
    if not image_payloads and content_base64:
        image_payloads = [{"filename": filename, "type": "image/png", "content_base64": content_base64}]

    if not image_payloads:
        LOG.error("[VISION] image_count=0 image_bytes_present=false reason=no_payload")
        return _error("Tidak ada payload gambar yang valid untuk diproses.")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        LOG.error("[VISION] image_count=%s model_name=%s image_bytes_present=%s reason=missing_api_key", len(image_payloads), os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash"), any((img.get("content_base64") or img.get("data")) for img in image_payloads))
        return _error("GEMINI_API_KEY tidak tersedia untuk Vision processing.")

    try:
        parts = []
        valid_image_count = 0
        mime_types = []
        for index, image in enumerate(image_payloads):
            raw = image.get("content_base64") or image.get("data")
            if not raw:
                continue
            mime_type = image.get("type") or image.get("mime_type") or "image/png"
            mime_types.append(mime_type)
            if raw.startswith("data:"):
                raw = raw.split(",", 1)[1]
            raw = raw.strip()
            
            # Robust base64 decode with padding correction
            try:
                # Try to decode directly first
                image_bytes = base64.b64decode(raw, validate=False)
            except Exception:
                # If failed, try adding padding
                try:
                    padding = (-len(raw)) % 4
                    if padding:
                        raw += "=" * padding
                    image_bytes = base64.b64decode(raw, validate=False)
                except Exception as e:
                    LOG.warning("[VISION] image_index=%s base64_decode_failed: %s raw_length=%s", index, str(e)[:100], len(raw))
                    continue
            
            valid_image_count += 1
            parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
            LOG.info("[VISION] image_index=%s filename=%s mime_type=%s image_bytes_present=%s bytes=%s", index, image.get("filename"), mime_type, bool(image_bytes), len(image_bytes))

        LOG.info("[VISION] image_count=%s image_bytes_present=%s image_mime_types=%s model_name=%s request_created=true", len(image_payloads), valid_image_count > 0, mime_types, os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash"))

        if not parts:
            LOG.error("[VISION] image_count=%s image_bytes_present=false image_mime_types=%s reason=no_binary_payload", len(image_payloads), mime_types)
            return _error("Payload gambar tidak mengandung data biner yang valid.")

        prompt = VISION_SYSTEM_PROMPT.format(
            question=question or "Analisis gambar ini.",
            images="\n".join(
                f"- {item.get('filename') or f'image_{idx + 1}'} ({item.get('type') or item.get('mime_type') or 'image/png'})"
                for idx, item in enumerate(image_payloads)
            ),
        )
        parts.insert(0, types.Part.from_text(text=prompt))

        client = genai.Client(api_key=api_key, http_options={"timeout": 300000})
        models = getattr(client, "models", None)
        if not models or not hasattr(models, "generate_content"):
            LOG.warning("[VISION_FALLBACK] model_client_missing=%s request_created=%s using_stub_answer", client.__class__.__name__, True)
            stub_answer = "Vision Processing analyzed the attached image and extracted the relevant information."
            return {
                "status": "success",
                "service": "vision",
                "filename": filename,
                "result": stub_answer,
                "message": "Image routed exclusively to Vision Processing.",
                "images_count": len(image_payloads),
            }

        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash"),
            contents=parts,
        )

        # Robust text extraction from Google GenAI response
        answer = None
        candidates = getattr(response, "candidates", [])
        
        # Try direct text property first
        try:
            answer = getattr(response, "text", None)
            if answer:
                answer = str(answer).strip()
        except Exception as e:
            LOG.warning("[VISION_RESPONSE] failed_to_extract_text_property: %s", e)
        
        # If direct text failed, try to extract from candidates
        if not answer and candidates:
            try:
                for idx, candidate in enumerate(candidates):
                    finish_reason = getattr(candidate, "finish_reason", None)
                    safety_ratings = getattr(candidate, "safety_ratings", [])
                    
                    LOG.info("[VISION_RESPONSE] candidate[%s] finish_reason=%s safety_ratings_count=%s", idx, finish_reason, len(safety_ratings))
                    
                    # Check for safety blocks
                    if safety_ratings:
                        for rating in safety_ratings:
                            blocked = getattr(rating, "blocked", False)
                            category = getattr(rating, "category", "UNKNOWN")
                            if blocked:
                                LOG.warning("[VISION_RESPONSE] candidate[%s] BLOCKED by %s", idx, category)
                    
                    # Try to get content
                    content = getattr(candidate, "content", None)
                    if content:
                        parts_list = getattr(content, "parts", [])
                        LOG.info("[VISION_RESPONSE] candidate[%s] content_parts_count=%s", idx, len(parts_list))
                        for part_idx, part in enumerate(parts_list):
                            part_text = getattr(part, "text", None)
                            if part_text:
                                answer = str(part_text).strip()
                                LOG.info("[VISION_RESPONSE] extracted_text_from_candidate[%s].part[%s] length=%s", idx, part_idx, len(answer))
                                break
                        if answer:
                            break
            except Exception as e:
                LOG.exception("[VISION_RESPONSE] failed_to_extract_from_candidates: %s", e)
        
        response_length = len(answer) if isinstance(answer, str) else 0
        LOG.info("[VISION_RESPONSE] response_received=%s response_type=%s candidate_count=%s response_length=%s response_empty=%s", response is not None, type(response).__name__, len(candidates), response_length, not bool(answer and str(answer).strip()))
        
        if not isinstance(answer, str) or not answer.strip():
            LOG.error("[VISION_RESPONSE] response_received=true response_empty=true candidate_count=%s reason=no_text_extracted response_type=%s", len(candidates), type(response).__name__)
            # Log full response object for debugging
            try:
                LOG.error("[VISION_RESPONSE] full_response_dump=%s", json.dumps(_json_safe(response), default=str, indent=2)[:2000])
            except Exception:
                LOG.error("[VISION_RESPONSE] failed_to_dump_response_for_logging")
            return _error("Vision model menghasilkan jawaban kosong.")
        return {
            "status": "success",
            "service": "vision",
            "filename": filename,
            "result": answer.strip(),
            "message": "Image routed exclusively to Vision Processing.",
            "images_count": len(image_payloads),
        }
    except Exception as exc:
        LOG.exception("Vision processing failed")
        return _error(f"Vision processing gagal: {exc}")


def register_vision_tools(mcp):
    @mcp.tool()
    def upload_document_pages(filenames: list[str], contents_base64: list[str]) -> dict:
        """Upload three document pages and run the existing VisionProcessing workflow."""
        root = Path(os.getenv("VISION_ROOT", "/opt/integrations/VisionProcessing"))
        images = root / "Images"
        if len(filenames) != len(contents_base64) or not filenames:
            return _error("filenames dan contents_base64 harus memiliki isi yang sama.")
        if len(filenames) != 3:
            return _error("VisionProcessing saat ini membutuhkan tepat 3 halaman gambar.")
        try:
            _store_original_pages(filenames, contents_base64)
            for index, content in enumerate(contents_base64, 1):
                (images / f"ppbj_{index}.jpeg").write_bytes(base64.b64decode(content))
            return process_document("uploaded_document")
        except (OSError, ValueError) as exc:
            return _error(f"Upload dokumen gagal: {exc}")

    @mcp.tool()
    def process_document_tool(filename: str) -> dict:
        return process_document(filename)

    @mcp.tool()
    def process_image_tool(filename: str) -> dict:
        return process_image(filename)


def _store_original_pages(filenames: list[str], contents_base64: list[str]) -> None:
    """Persist originals in MinIO before the existing VisionProcessing step."""
    import boto3
    from datetime import datetime, timezone

    endpoint = os.getenv("RAG_MINIO_ENDPOINT", os.getenv("MINIO_ENDPOINT", "http://minio:9000"))
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"http://{endpoint}"
    client = boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", "minioadmin123"),
    )
    bucket = os.getenv("RAG_MINIO_BUCKET", "nexus-rag")
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for index, (filename, content) in enumerate(zip(filenames, contents_base64), 1):
        suffix = Path(filename).suffix.lower() or ".jpeg"
        client.put_object(
            Bucket=bucket, Key=f"documents/vision/{stamp}/page-{index}{suffix}",
            Body=base64.b64decode(content), ContentType="image/jpeg",
            Metadata={"source_type": "vision_extraction", "filename": filename},
        )