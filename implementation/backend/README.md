# Techjay Backend — PDF Extractor (Step 1)

FastAPI service that extracts geometric and text elements from a PDF using `pdfplumber`.

## Setup

Tesseract is required for `/api/detect-scale` (HVAC duct callout OCR):

```bash
brew install tesseract            # macOS; apt install tesseract-ocr on Debian/Ubuntu
```

```bash
cd implementation/backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn main:app --reload --port 8000
```

## Endpoints

- `GET /api/health` → `{"ok": true}`
- `POST /api/extract` (multipart, field `file`) → JSON with `filename`, `page_count`, `pages[].elements[]`
- `POST /api/detect-scale` (multipart: `file`, `page_number`, `crop`) → diameter callouts (e.g. `14"ø`) detected via OCR with per-callout duct geometry and an aggregate drawing scale in PDF points per inch.

Element types: `line`, `rect`, `curve`, `char`, `word`. Coordinates use pdfplumber's top-left origin in PDF points.

Limits: 25 MB max, PDF magic-bytes enforced.
