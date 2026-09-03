import pymupdf
from pathlib import Path


PDF_DIR = Path("Data/pdf")
OUTPUT_DIR = Path("Data/extracted")


def extract_all_pdfs():

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    pdf_files = list(PDF_DIR.glob("*.pdf"))

    print(f"Ditemukan {len(pdf_files)} PDF\n")

    for pdf_path in pdf_files:

        print(f"Mengekstrak: {pdf_path.name}")

        all_text = []

        with pymupdf.open(pdf_path) as doc:

            print(f"Jumlah halaman: {len(doc)}")

            for page_number, page in enumerate(doc, start=1):

                text = page.get_text(
                    "text",
                    sort=True
                )

                all_text.append(
                    f"\n===== PAGE {page_number} =====\n"
                )

                all_text.append(text)

        final_text = "\n".join(all_text)

        output_path = (
            OUTPUT_DIR /
            f"{pdf_path.stem}.txt"
        )

        output_path.write_text(
            final_text,
            encoding="utf-8"
        )

        print(
            f"Berhasil → {output_path.name}"
        )

        print(
            f"Karakter: {len(final_text):,}"
        )

        print("-" * 60)


if __name__ == "__main__":
    extract_all_pdfs()