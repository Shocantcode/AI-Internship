# Detik Finance Computer-Use Agent

## Project Overview
MVP agent browser untuk membuka Detik Finance, mencari topik, membaca beberapa artikel, lalu menghasilkan teks terstruktur. Browser control, agent loop, extraction, model, prompt, dan output formatter dipisahkan agar mudah dikembangkan.

## Architecture

```text
User -> ComputerUseAgent -> BrowserController -> Detik Finance
                                      -> NewsExtractor -> JSON/TXT
```

## Installation
Jalankan dari root project dengan Python 3.11+ dan dependency root:

```powershell
pip install -r requirements.txt
playwright install chromium
Set-Location 'Computer Use'
```

Tidak ada `.env`, `Dockerfile`, atau `requirements.txt` baru di folder ini. Agent membaca `.env` root melalui `python-dotenv`.

## Environment
`HEADLESS=true`, `BROWSER_TIMEOUT=30000`, `TIMEOUT=120`, `MAX_STEPS=30`, dan `MAX_ARTICLES=20` dapat diatur di `.env` root. `OPENAI_API_KEY` tidak diperlukan untuk MVP ini dan credential tidak dicetak ke log.

## Run
Dari folder `Computer Use`:

```powershell
python -m app.main --query "ekonomi Indonesia" --limit 5
```

Atau dari root:

```powershell
python -m app.main --query "IHSG" --limit 5
```

Perintah terakhir membutuhkan `PYTHONPATH='Computer Use'` pada shell yang tidak otomatis memakai folder kerja modul.

Hasil ditulis ke `Computer Use/output/news.json` dan `Computer Use/output/news.txt`.

## MCP
Service MCP mendaftarkan dua tool yang memakai agent ini:

- `collect_detik_finance_news(query, limit)` untuk pemanggilan baru.
- `get_news(website_url, keyword, jumlah_berita, timeout_seconds)` sebagai kompatibilitas client lama.

Contoh argumen tool baru:

```json
{"query": "ekonomi Indonesia", "limit": 5}
```

Pastikan service `mcp` dijalankan dari root agar volume `./Computer Use:/app/Computer Use` tersedia.

## Docker
Build image root yang sudah ada, lalu jalankan container Airflow. Folder `Computer Use` dimount ke `/opt/airflow/Computer Use`:

```powershell
docker compose -f docker-compose.yaml build
# setelah service Airflow hidup:
docker compose -f docker-compose.yaml exec airflow-scheduler python "/opt/airflow/Computer Use/app/main.py" --query "ekonomi Indonesia" --limit 5
```

Untuk module mode di container, gunakan `Set-Location '/opt/airflow/Computer Use'` terlebih dahulu.

## Example Output

```text
==================================================
DETIK FINANCE NEWS
==================================================

1. JUDUL:
[Judul berita]

KATEGORI:
Finance

TANGGAL:
[ tanggal ]

AUTHOR:
[ author jika tersedia ]

SOURCE:
Detik Finance

URL:
[ URL artikel ]

RINGKASAN:
[ ringkasan singkat ]

ISI:
[ teks artikel yang dibersihkan ]
```

## Limitations
Website dapat mengubah markup, menampilkan consent/captcha, atau membatasi akses. Artikel yang gagal dicatat di `failed_articles` dan tidak menghentikan artikel lain. `MAX_STEPS` dan `TIMEOUT` menghentikan loop agar tidak berjalan tanpa batas.
