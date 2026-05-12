# Techjay backend

FastAPI service that parses HVAC plan PDFs: extracts every vector element inside a crop region, OCRs duct-diameter callouts, and infers the drawing's points-per-inch scale.

For algorithm details (per-box OCR, band-mean scale aggregation, ASSOCIATED-rect geometry inference), see [`../../docs/backend.md`](../../docs/backend.md).

## Stack

- Python 3.11+
- FastAPI + Pydantic v2
- pdfplumber (vector geometry) · pypdfium2 (raster rendering) · pytesseract (OCR) · Pillow · PyMuPDF (`fitz`)
- Tesseract OCR system binary

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pymupdf            # currently missing from requirements.txt
brew install tesseract         # macOS; apt install tesseract-ocr on Debian
```

## Run

```bash
uvicorn main:app --reload --port 8000
```

Health check:

```bash
curl http://localhost:8000/api/health
# {"ok":true}
```

CORS is locked to `http://localhost:3000` in `main.py`. Change `allow_origins` to deploy elsewhere.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`  | `/api/health` | Liveness probe. |
| `POST` | `/api/extract` | Element extraction; optional per-page crop. |
| `POST` | `/api/detect-scale` | Per-region callout OCR and scale inference. |
| `POST` | `/api/preprocess` | Legacy — black-only SVG. Not called by the current frontend. |

Full request/response schemas, error codes, and per-stage algorithm explanations are in [`../../docs/backend.md`](../../docs/backend.md).

## Limits

- 25 MB upload cap, enforced server-side.
- PDF magic-bytes check (`%PDF`) overrides the client-supplied content-type.
- One page per `/api/detect-scale` call — the frontend parallelises across regions.

## Files

| File | What it does |
| --- | --- |
| `main.py` | Routes, Pydantic models, multipart parsing. |
| `extractor.py` | pdfplumber → element list with rect/curve classification. |
| `scale_detector.py` | Vector candidate boxes → per-box 1200-DPI OCR → geometry → band-mean scale. Also exports `_is_rectlike_curve`, `_is_rect_partial`, `rect_corners_from_curve` consumed by `extractor.py`. |
| `preprocess.py` | Legacy SVG renderer with non-black elements stripped. |
| `requirements.txt` | Python deps. PyMuPDF is missing — see Setup. |
