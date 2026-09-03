
import os
import json
from pathlib import Path
from typing import Optional, List

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

# ============================================================
# 1. LOAD ENVIRONMENT / VERTEX AI CONFIG
# ============================================================

load_dotenv()

# OLD / FREE API KEY VERSION (COMMENTED OUT)
# api_key = os.getenv("GEMINI_API_KEY")
#
# if not api_key:
#     raise ValueError(
#         "GEMINI_API_KEY tidak ditemukan di file .env"
#     )

# NEW / VERTEX AI VERSION
project_id = os.getenv("GCP_PROJECT_ID", "sinarmas-app-computer-use")
location = os.getenv("GEMINI_LOCATION", "global")


# ============================================================
# 2. CONNECT TO GEMINI (VERTEX AI)
# ============================================================

client = genai.Client(
    http_options={"timeout": 300000},
    vertexai=True,
    project=project_id,
    location=location,
)


# ============================================================
# 3. LOKASI GAMBAR
# ============================================================

image_folder = Path("Images")

image_paths = [
    image_folder / "ppbj_1.jpeg",
    image_folder / "ppbj_2.jpeg",
    image_folder / "ppbj_3.jpeg"
]


# ============================================================
# 4. CEK SEMUA GAMBAR
# ============================================================

for image_path in image_paths:

    if not image_path.exists():
        raise FileNotFoundError(
            f"Gambar tidak ditemukan: {image_path}"
        )

    print(f"Gambar ditemukan: {image_path}")


# ============================================================
# 5. UPLOAD 3 GAMBAR
# ============================================================

uploaded_images = []

for image_path in image_paths:

    print(f"Mengupload {image_path.name}...")

    uploaded_image = client.files.upload(
        file=str(image_path)
    )

    uploaded_images.append(uploaded_image)

print("Semua gambar berhasil diupload.")


# ============================================================
# 6. PROMPT COMPUTER VISION
# ============================================================

prompt = """
Anda adalah sistem Computer Vision untuk memahami dokumen PPBJ
(Pemberitahuan Perolehan atau Pengeluaran Barang Kena Pajak atau
Jasa Kena Pajak).

Tiga gambar yang diberikan merupakan tiga halaman dari SATU
dokumen PPBJ yang sama.

Analisis seluruh halaman.

Tugas:

1. Baca teks dari seluruh halaman.
2. Identifikasi struktur dokumen.
3. Identifikasi informasi pada bagian A sampai K.
4. Identifikasi pengusaha di KPBPB.
5. Identifikasi lawan transaksi.
6. Identifikasi kontrak.
7. Identifikasi seluruh barang pada tabel.
8. Identifikasi nilai transaksi.
9. Identifikasi informasi rekening.
10. Simpan informasi berdasarkan halaman.

ATURAN:

- Jangan mengarang informasi.
- Jangan menebak informasi yang tidak terlihat.
- Jika informasi tidak ditemukan, gunakan null.
- Pertahankan teks sedekat mungkin dengan dokumen asli.
- NPWP, NIK, nomor rekening, nomor kontrak dan HS Code harus
  disimpan sebagai STRING.
- Nilai transaksi harus berupa NUMBER.
- Jangan menggunakan tanda titik atau koma pada nilai NUMBER.
- Setiap barang harus menjadi satu object tersendiri.
- Jangan menggabungkan beberapa barang.
- Ketiga gambar merupakan satu dokumen.
- Output harus mengikuti JSON schema yang diberikan.
"""


# ============================================================
# 7. JSON SCHEMA
# ============================================================

# schema = {
#     "type": "object",

#     "properties": {

#         "document": {
#             "type": "object",
#             "properties": {

#                 "document_type": {
#                     "type": "string"
#                 },

#                 "ppbj_number": {
#                     "type": ["string", "null"]
#                 },

#                 "document_date": {
#                     "type": ["string", "null"]
#                 }

#             },

#             "required": [
#                 "document_type",
#                 "ppbj_number",
#                 "document_date"
#             ]
#         },


#         "transaction": {
#             "type": "object",
#             "properties": {

#                 "object": {
#                     "type": ["string", "null"]
#                 },

#                 "type": {
#                     "type": ["string", "null"]
#                 },

#                 "origin_destination": {
#                     "type": ["string", "null"]
#                 }

#             },

#             "required": [
#                 "object",
#                 "type",
#                 "origin_destination"
#             ]
#         },


#         "company": {
#             "type": "object",
#             "properties": {

#                 "name": {
#                     "type": ["string", "null"]
#                 },

#                 "npwp": {
#                     "type": ["string", "null"]
#                 },

#                 "address": {
#                     "type": ["string", "null"]
#                 },

#                 "registered_kpp": {
#                     "type": ["string", "null"]
#                 },

#                 "kpbpb": {
#                     "type": ["string", "null"]
#                 }

#             },

#             "required": [
#                 "name",
#                 "npwp",
#                 "address",
#                 "registered_kpp",
#                 "kpbpb"
#             ]
#         },


#         "counterparty": {
#             "type": "object",
#             "properties": {

#                 "name": {
#                     "type": ["string", "null"]
#                 },

#                 "npwp_nik": {
#                     "type": ["string", "null"]
#                 },

#                 "address": {
#                     "type": ["string", "null"]
#                 },

#                 "registered_kpp": {
#                     "type": ["string", "null"]
#                 }

#             },

#             "required": [
#                 "name",
#                 "npwp_nik",
#                 "address",
#                 "registered_kpp"
#             ]
#         },


#         "contract": {
#             "type": "object",
#             "properties": {

#                 "number": {
#                     "type": ["string", "null"]
#                 },

#                 "date": {
#                     "type": ["string", "null"]
#                 }

#             },

#             "required": [
#                 "number",
#                 "date"
#             ]
#         },


#         "items": {
#             "type": "array",

#             "items": {
#                 "type": "object",

#                 "properties": {

#                     "page": {
#                         "type": "integer"
#                     },

#                     "item_number": {
#                         "type": "integer"
#                     },

#                     "type": {
#                         "type": ["string", "null"]
#                     },

#                     "hs_code": {
#                         "type": ["string", "null"]
#                     },

#                     "description": {
#                         "type": ["string", "null"]
#                     },

#                     "transaction_value": {
#                         "type": ["number", "null"]
#                     }

#                 },

#                 "required": [
#                     "page",
#                     "item_number",
#                     "type",
#                     "hs_code",
#                     "description",
#                     "transaction_value"
#                 ]
#             }
#         },


#         "values": {
#             "type": "object",

#             "properties": {

#                 "total_transaction_value": {
#                     "type": ["number", "null"]
#                 },

#                 "tax_base": {
#                     "type": ["number", "null"]
#                 },

#                 "down_payment": {
#                     "type": ["number", "null"]
#                 },

#                 "vat_or_luxury_tax": {
#                     "type": ["number", "null"]
#                 },

#                 "vat_or_luxury_tax_facility": {
#                     "type": ["number", "null"]
#                 }

#             },

#             "required": [
#                 "total_transaction_value",
#                 "tax_base",
#                 "down_payment",
#                 "vat_or_luxury_tax",
#                 "vat_or_luxury_tax_facility"
#             ]
#         },


#         "payment": {
#             "type": "object",

#             "properties": {

#                 "account_number": {
#                     "type": ["string", "null"]
#                 },

#                 "account_name": {
#                     "type": ["string", "null"]
#                 },

#                 "bank_name": {
#                     "type": ["string", "null"]
#                 }

#             },

#             "required": [
#                 "account_number",
#                 "account_name",
#                 "bank_name"
#             ]
#         },


#         "pages": {
#             "type": "array",

#             "items": {
#                 "type": "object",

#                 "properties": {

#                     "page": {
#                         "type": "integer"
#                     },

#                     "sections": {
#                         "type": "array",
#                         "items": {
#                             "type": "string"
#                         }
#                     }

#                 },

#                 "required": [
#                     "page",
#                     "sections"
#                 ]
#             }
#         }
#     },

#     "required": [
#         "document",
#         "transaction",
#         "company",
#         "counterparty",
#         "contract",
#         "items",
#         "values",
#         "payment",
#         "pages"
#     ]
# }

# ============================================================
# JSON STRUCTURED OUTPUT MODEL
# ============================================================

class DocumentInfo(BaseModel):
    document_type: str
    ppbj_number: Optional[str]
    document_date: Optional[str]


class TransactionInfo(BaseModel):
    object: Optional[str]
    type: Optional[str]
    origin_destination: Optional[str]


class CompanyInfo(BaseModel):
    name: Optional[str]
    npwp: Optional[str]
    address: Optional[str]
    registered_kpp: Optional[str]
    kpbpb: Optional[str]


class CounterpartyInfo(BaseModel):
    name: Optional[str]
    npwp_nik: Optional[str]
    address: Optional[str]
    registered_kpp: Optional[str]


class ContractInfo(BaseModel):
    number: Optional[str]
    date: Optional[str]


class ItemInfo(BaseModel):
    page: int
    item_number: int
    type: Optional[str]
    hs_code: Optional[str]
    description: Optional[str]
    transaction_value: Optional[float]


class ValuesInfo(BaseModel):
    total_transaction_value: Optional[float]
    tax_base: Optional[float]
    down_payment: Optional[float]
    vat_or_luxury_tax: Optional[float]
    vat_or_luxury_tax_facility: Optional[float]


class PaymentInfo(BaseModel):
    account_number: Optional[str]
    account_name: Optional[str]
    bank_name: Optional[str]


class PageInfo(BaseModel):
    page: int
    sections: List[str]


class PPBJResult(BaseModel):
    document: DocumentInfo
    transaction: TransactionInfo
    company: CompanyInfo
    counterparty: CounterpartyInfo
    contract: ContractInfo
    items: List[ItemInfo]
    values: ValuesInfo
    payment: PaymentInfo
    pages: List[PageInfo]
# ============================================================
# 8. KIRIM KE GEMINI
# ============================================================

print("\nMengirim 3 halaman ke Gemini Vision...")
print("Mohon tunggu...\n")


response = client.models.generate_content(
    model="gemini-3.6-flash",

    contents=[
        prompt,
        *uploaded_images
    ],

    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=PPBJResult
    )
)


# ============================================================
# 9. PARSE JSON
# ============================================================

try:

    result = json.loads(response.text)

except json.JSONDecodeError:

    print("ERROR: Response Gemini bukan JSON valid.")

    print(response.text)

    raise


# ============================================================
# 10. BUAT FOLDER OUTPUT
# ============================================================

output_folder = Path("Output")

output_folder.mkdir(
    exist_ok=True
)


# ============================================================
# 11. SIMPAN JSON
# ============================================================

output_file = output_folder / "ppbj_result.json"

with open(
    output_file,
    "w",
    encoding="utf-8"
) as file:

    json.dump(
        result,
        file,
        indent=4,
        ensure_ascii=False
    )


# ============================================================
# 12. TAMPILKAN HASIL
# ============================================================

print("=" * 70)
print("JSON BERHASIL DIBUAT")
print("=" * 70)

print(
    json.dumps(
        result,
        indent=4,
        ensure_ascii=False
    )
)

print("\nFile JSON:")
print(output_file)