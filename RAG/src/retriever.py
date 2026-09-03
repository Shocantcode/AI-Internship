import os
import re
from pathlib import Path

import chromadb
import numpy as np
from dotenv import load_dotenv
from google import genai
from google.genai import types
from query_understanding import expanded_query, understand_query


# =========================================================
# PATH
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

CHROMA_DIR = (
    BASE_DIR
    / "Data"
    / "chroma_db"
)


# =========================================================
# CONFIG
# =========================================================

MODEL_NAME = "gemini-embedding-001"

EMBEDDING_DIMENSION = 768

COLLECTION_NAME = "stock_research"

# Ambil lebih banyak kandidat dulu
CANDIDATE_K = 20

# Setelah Gaussian ranking
FINAL_K = 5

# Gaussian bandwidth
SIGMA = 0.25


# =========================================================
# ENV
# =========================================================

load_dotenv(BASE_DIR.parent / ".env")

api_key = os.getenv(
    "GEMINI_API_KEY"
)

if not api_key:
    raise ValueError(
        "GEMINI_API_KEY tidak ditemukan."
    )


client = genai.Client(
    api_key=api_key
)


# =========================================================
# CHROMADB
# =========================================================

chroma_client = chromadb.PersistentClient(
    path=str(CHROMA_DIR)
)

collection = chroma_client.get_or_create_collection(
    name=COLLECTION_NAME
)


# =========================================================
# QUERY EMBEDDING
# =========================================================

def create_query_embedding(question):

    result = client.models.embed_content(
        model=MODEL_NAME,
        contents=question,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=EMBEDDING_DIMENSION
        )
    )

    return result.embeddings[0].values


# =========================================================
# GAUSSIAN SCORE
# =========================================================

def gaussian_score(distance, sigma=SIGMA):

    return np.exp(
        -(distance ** 2)
        /
        (2 * sigma ** 2)
    )


# =========================================================
# RETRIEVE
# =========================================================

def retrieve(
    question,
    final_k=FINAL_K,
    source_type=None,
):

    # -----------------------------------------------------
    # 1. Query embedding
    # -----------------------------------------------------

    profile = understand_query(question)
    query_embedding = create_query_embedding(expanded_query(profile))

    # -----------------------------------------------------
    # 2. ChromaDB candidate retrieval
    # -----------------------------------------------------

    if collection.count() == 0:
        return []

    where = {"source_type": source_type} if source_type else None
    query_args = {
        "query_embeddings": [query_embedding],
        "n_results": CANDIDATE_K,
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        query_args["where"] = where
    results = collection.query(**query_args)

    documents = results["documents"][0]

    metadatas = results["metadatas"][0]

    distances = results["distances"][0]

    # -----------------------------------------------------
    # 3. Gaussian re-ranking
    # -----------------------------------------------------

    candidates = []

    for document, metadata, distance in zip(
        documents,
        metadatas,
        distances
    ):

        score = gaussian_score(
            distance
        )

        document_tokens = set(re.findall(r"[a-z0-9][a-z0-9-]*", document.lower()))
        keyword_hits = len(set(profile.keywords) & document_tokens)
        lexical_score = min(1.0, keyword_hits / max(1, len(profile.keywords)))
        hybrid_score = 0.7 * float(score) + 0.3 * lexical_score
        candidates.append({
            "text": document,

            "source": metadata.get("source", metadata.get("object_key", "unknown")),

            "content": document,

            "metadata": metadata,

            "page": metadata.get("page", 0),

            "chunk": metadata.get("chunk", 0),

            "distance": float(distance),

            "gaussian_score": float(score),
            "keyword_score": float(lexical_score),
            "relevance_score": float(hybrid_score),
        })

    # -----------------------------------------------------
    # 4. Sort berdasarkan Gaussian score
    # -----------------------------------------------------

    candidates.sort(
        key=lambda x: x["relevance_score"],
        reverse=True
    )

    # -----------------------------------------------------
    # 5. Ambil Top K
    # -----------------------------------------------------

    threshold = float(os.getenv("RAG_RELEVANCE_THRESHOLD", "0.20"))
    if len(profile.keywords) <= 3:
        short_threshold = float(os.getenv("RAG_SHORT_QUERY_THRESHOLD", "0.75"))
        candidates = [candidate for candidate in candidates if candidate["keyword_score"] > 0 and candidate["relevance_score"] >= short_threshold]
    else:
        candidates = [candidate for candidate in candidates if candidate["relevance_score"] >= threshold]
    return candidates[:final_k]


# =========================================================
# TEST
# =========================================================

if __name__ == "__main__":

    question = input(
        "\nPertanyaan saham: "
    )

    results = retrieve(
        question
    )

    print()
    print("=" * 75)
    print("GAUSSIAN RETRIEVER")
    print("=" * 75)

    for i, result in enumerate(
        results,
        start=1
    ):

        print(
            f"\n[{i}]"
        )

        print(
            f"Gaussian Score : "
            f"{result['gaussian_score']:.6f}"
        )

        print(
            f"Distance       : "
            f"{result['distance']:.6f}"
        )

        print(
            f"Source         : "
            f"{result['source']}"
        )

        print(
            f"Page           : "
            f"{result['page']}"
        )

        print(
            f"Chunk          : "
            f"{result['chunk']}"
        )

        print(
            "\nText:"
        )

        print(
            result["text"][:700]
        )

        print(
            "-" * 75
        )