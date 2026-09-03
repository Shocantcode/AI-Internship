import re
import json
from pathlib import Path


EXTRACTED_DIR = Path("Data/extracted")
OUTPUT_FILE = Path("Data/extracted/chunks.json")


CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


def clean_text(text):
    """
    Membersihkan text hasil ekstraksi PDF.
    """

    # Hapus spasi berlebihan
    text = re.sub(r"[ \t]+", " ", text)

    # Rapikan terlalu banyak newline
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def split_text(text, chunk_size=1000, overlap=150):

    chunks = []

    start = 0

    while start < len(text):

        end = start + chunk_size

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        start += chunk_size - overlap

    return chunks


def process_file(file_path):

    text = file_path.read_text(
        encoding="utf-8"
    )

    # Pecah berdasarkan PAGE marker
    pages = re.split(
        r"===== PAGE (\d+) =====",
        text
    )

    documents = []

    # pages bentuk:
    # ["", "1", "text page 1", "2", "text page 2", ...]

    for i in range(1, len(pages), 2):

        page_number = int(pages[i])
        page_text = pages[i + 1]

        page_text = clean_text(page_text)

        if not page_text:
            continue

        chunks = split_text(
            page_text,
            CHUNK_SIZE,
            CHUNK_OVERLAP
        )

        for chunk_number, chunk in enumerate(chunks):

            documents.append({
                "source": file_path.name,
                "page": page_number,
                "chunk": chunk_number,
                "text": chunk
            })

    return documents


def main():

    all_chunks = []

    txt_files = list(
        EXTRACTED_DIR.glob("*.txt")
    )

    print(f"Ditemukan {len(txt_files)} file TXT\n")

    global_chunk_id = 0

    for file_path in txt_files:

        print(f"Processing: {file_path.name}")

        documents = process_file(
            file_path
        )

        for document in documents:

            document["chunk_id"] = global_chunk_id

            all_chunks.append(document)

            global_chunk_id += 1

        print(
            f"Chunks: {len(documents)}"
        )

        print("-" * 60)

    OUTPUT_FILE.write_text(
        json.dumps(
            all_chunks,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    print()
    print("=" * 60)
    print(f"TOTAL CHUNKS: {len(all_chunks)}")
    print(f"Saved: {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
    