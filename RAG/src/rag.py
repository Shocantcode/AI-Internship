import os
import logging
from pathlib import Path

from dotenv import load_dotenv
from google import genai

from retriever import retrieve
from response_normalizer import normalize_response

LOG = logging.getLogger(__name__)


# =========================================================
# PROJECT
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent


# =========================================================
# CONFIG
# =========================================================

MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-3.5-flash")


# =========================================================
# ENV
# =========================================================

load_dotenv(
    BASE_DIR / ".env"
)

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError(
        "GEMINI_API_KEY tidak ditemukan"
    )

client = genai.Client(
    api_key=api_key,
    http_options={"timeout": 300000},
)


# =========================================================
# CONTEXT BUILDER
# =========================================================

def build_context(results):

    context_parts = []

    for i, result in enumerate(
        results,
        start=1
    ):

        metadata = result.get("metadata", {})
        context_parts.append(
            f"""
[SOURCE {i}]
File: {metadata.get('filename', metadata.get('title', result.get('source', 'unknown')))}
Bucket: {metadata.get('bucket', 'unknown')}
Object key: {metadata.get('object_key', 'unknown')}
Page: {result.get('page', metadata.get('page', 0))}
Chunk: {result.get('chunk', metadata.get('chunk', 0))}
Relevance: {result.get('relevance_score', result.get('gaussian_score', 0)):.6f}

Content:
{result['text']}
"""
        )

    return "\n".join(
        context_parts
    )


# =========================================================
# GEMINI GENERATION
# =========================================================

def generate_answer(question, additional_results=None, include_internal=True):

    # -------------------------------------
    # RETRIEVAL
    # -------------------------------------

    results = retrieve(question) if include_internal else []
    LOG.info("[EVIDENCE] query=%r initial_rag_results=%d additional_results=%d", question, len(results), len(additional_results or []))
    for result in additional_results or []:
        result = dict(result)
        result.setdefault("metadata", {})
        result.setdefault("text", result.get("content", ""))
        result.setdefault("source", result["metadata"].get("title", "External research"))
        if result.get("text"):
            results.append(result)

    LOG.info(
        "[ANSWER SYNTHESIS INPUT] query=%r model=%s source_count=%d content_items=%d context_chars=%d",
        question,
        MODEL_NAME,
        len(results),
        sum(1 for item in results if str(item.get("text", "")).strip()),
        sum(len(str(item.get("text", ""))) for item in results),
    )

    if not results:
        return normalize_response("", [])

    LOG.info("[EVIDENCE] final_evidence=%d content_items=%d", len(results), sum(1 for item in results if item.get("text")))

    # -------------------------------------
    # BUILD CONTEXT
    # -------------------------------------

    context = build_context(
        results
    )

    # -------------------------------------
    # PROMPT
    # -------------------------------------

    prompt = f"""
Anda adalah Stock Research Assistant.

Jawab pertanyaan pengguna berdasarkan
dokumen yang diberikan pada CONTEXT.

ATURAN:

1. Gunakan hanya informasi yang terdapat
   pada CONTEXT.

2. Jangan menggunakan pengetahuan eksternal.

3. Jangan mengarang angka, harga,
   target price, rasio, atau informasi
   perusahaan.

5. Jika evidence tersedia tetapi tidak
    menjelaskan penyebab secara pasti,
    jelaskan fakta yang didukung evidence
    dan katakan bagian mana yang belum dapat
    dipastikan. Jangan menggunakan kalimat
    "Informasi tidak cukup berdasarkan dokumen
    yang tersedia" jika ada evidence yang valid.

5. Jika terdapat beberapa sumber,
   gabungkan informasi tersebut secara
   logis.

    Evidence external terbaru yang diberikan
    langsung oleh agent harus diprioritaskan
    di atas dokumen lama yang kurang relevan.

6. Jika ada perbedaan informasi antar
   dokumen, jelaskan perbedaannya.

7. Jawab dalam Bahasa Indonesia.

8. Gunakan format yang mudah dibaca.

9. Jangan membuat bagian SOURCES,
    marker sumber, JSON, atau metadata
    teknis. Sumber akan ditampilkan oleh
    aplikasi dari metadata terstruktur.

========================================
CONTEXT
========================================

{context}

========================================
PERTANYAAN USER
========================================

{question}

========================================
JAWABAN
========================================
"""

    # -------------------------------------
    # GEMINI
    # -------------------------------------

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt
        )
    except Exception:
        LOG.exception(
            "[ANSWER SYNTHESIS FAILED] model=%s query=%r source_count=%d",
            MODEL_NAME,
            question,
            len(results),
        )
        raise

    answer_text = getattr(response, "text", None)
    if not isinstance(answer_text, str) or not answer_text.strip():
        LOG.error(
            "[ANSWER SYNTHESIS FAILED] model=%s query=%r response_type=%s empty_response=true",
            MODEL_NAME,
            question,
            type(response).__name__,
        )
        raise RuntimeError("LLM returned an empty answer")

    normalized = normalize_response(answer_text, results)
    if not normalized.get("answer", "").strip():
        raise RuntimeError("LLM answer could not be normalized")
    if additional_results and normalized["answer"].lower().startswith("informasi tidak cukup berdasarkan dokumen yang tersedia"):
        normalized["answer"] = normalized["answer"].split(".", 1)[-1].strip() or "Evidence tersedia, tetapi belum menjelaskan penyebab secara pasti."
        normalized["status"] = "partial"
    normalized["confidence"] = min(0.98, 0.45 + len(results) * 0.1)
    return normalized


# =========================================================
# MAIN
# =========================================================

def main():

    print()
    print("=" * 75)
    print("STOCK RAG - GEMINI LLM")
    print("=" * 75)

    while True:

        question = input(
            "\nPertanyaan saham "
            "(ketik 'exit' untuk keluar): "
        )

        if question.lower() == "exit":

            print(
                "\nProgram selesai."
            )

            break

        print(
            "\n🔎 Searching..."
        )

        answer, results = generate_answer(
            question
        )

        print()
        print("=" * 75)
        print("🤖 GEMINI ANSWER")
        print("=" * 75)

        print(
            answer
        )

        print()
        print("=" * 75)
        print("📚 RETRIEVED SOURCES")
        print("=" * 75)

        for i, result in enumerate(
            results,
            start=1
        ):

            print(
                f"[{i}] "
                f"{result['source']} "
                f"| Page {result['page']} "
                f"| Gaussian "
                f"{result['gaussian_score']:.6f}"
            )


if __name__ == "__main__":

    main()