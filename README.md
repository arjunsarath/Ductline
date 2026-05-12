# Ductline

An interactive viewer for HVAC plan PDFs. The user uploads a vector PDF, crops the drawing region, and the tool extracts every vector element inside the crop, finds the `NN"Ø` duct-diameter callouts, infers the drawing's scale (PDF points per real-world inch), and overlays the surviving plausible-duct rectangles on top of the original page.

The codebase splits into a Python FastAPI backend (PDF parsing, OCR, scale inference) and a Next.js frontend (upload, crop, pipeline orchestration, viewer).

## What the tool does

1. **Upload** a single PDF (≤25 MB, vector, magic-bytes checked).
2. **Crop** one or more regions on one or more pages. The crop is the analysis boundary, not a display crop — the viewer renders the full original page.
3. The pipeline runs three stages:
   - **Extract** — pdfplumber pulls lines, rects, curves (axis-aligned and rotated), `rect_partial` U-shapes, chars, and words inside the crop.
   - **Detect scale** — for each crop region the backend finds callout-shaped rectangles, OCRs each at 1200 DPI, and infers `drawing_scale_pts_per_inch` from a band-mean across callouts. One HTTP call per region, run in parallel.
   - **Cleanup** — client-side filters keep only plausible duct rectangles (black ink, aspect, min-side, max-area).
4. **Viewer** — full original PDF rendered by react-pdf, with overlays for the crop outline, surviving rectangles, callouts, and wall-pair measurements. Pan/zoom, opacity slider, virtualised element list.

If any pipeline stage fails (HTTP error, zero callouts decoded, or `drawing_scale_pts_per_inch === null`), the pipeline stops at the failing stage and shows an error card with "Back to crop" and "Start over".

## Quickstart

You need two terminals: one for the backend, one for the frontend.

### Prerequisites

- Python 3.11+ (uses PEP 604 union syntax via `from __future__ import annotations`)
- Node.js 20+ (Next.js 16 / React 19)
- Tesseract OCR (`brew install tesseract` on macOS, `apt install tesseract-ocr` on Debian)

### Backend

```bash
cd implementation/backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Health check: `curl http://localhost:8000/api/health` → `{"ok":true}`.

### Frontend

```bash
cd implementation/frontend
npm install
npm run dev
```

Open http://localhost:3000.

The frontend reads two optional env vars:

| Variable | Default | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000/api/extract` | Element-extraction endpoint |
| `NEXT_PUBLIC_SCALE_API_URL` | `http://localhost:8000/api/detect-scale` | Scale-detection endpoint |

CORS on the backend is locked to `http://localhost:3000` — if you serve the frontend from another origin, edit `allow_origins` in `implementation/backend/main.py`.

## Architecture

```
implementation/
├── backend/                  FastAPI service
│   ├── main.py               Routes: /api/health, /api/extract, /api/preprocess, /api/detect-scale
│   ├── extractor.py          pdfplumber-based element extraction + rect-curve classification
│   ├── scale_detector.py     Vector candidate boxes → per-box OCR → geometry → band-mean scale
│   ├── preprocess.py         Legacy black-only SVG (no longer called by the frontend)
│   └── requirements.txt
└── frontend/                 Next.js 16 client (App Router, all "use client")
    ├── src/app/page.tsx      State machine: upload | crop | pipeline | viewer
    ├── src/components/
    │   ├── upload-screen.tsx
    │   ├── cropper.tsx       Multi-page, multi-region crop selection
    │   ├── pipeline-progress.tsx
    │   ├── viewer.tsx        Full-page render + overlays + filters + element list
    │   ├── pdf-page.tsx      react-pdf wrapper with adaptive DPI
    │   └── element-overlay.tsx   Canvas paint for all visible elements
    └── src/lib/extract.ts    Types + cleanup helpers (aspect / min-side / max-area)
```

Single-session, no persistence. The PDF is held as an in-memory `File` and `URL.createObjectURL`'d blob throughout the session; quit the tab and everything is gone.

## More docs

| Document | Read it when |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | You want the data flow, coordinate systems, and pipeline-stage contract. |
| [`docs/backend.md`](docs/backend.md) | You're changing the OCR, geometry inference, or filtering thresholds. |
| [`docs/frontend.md`](docs/frontend.md) | You're touching the screens, viewer overlays, or client-side filters. |
| [`docs/development.md`](docs/development.md) | You're setting up a new machine, hitting an error, or adding a new filter rule. |
| [`implementation/backend/README.md`](implementation/backend/README.md) | You want the bare backend-only setup. |

## Known limitations

- Single-PDF, single-session. No queueing, no jobs, no auth.
- 25 MB upload cap (enforced server-side).
- `inferred_rect` (synthetic rectangles from two `rect_partial` U-shapes) is disabled — see `extractor.py` for the reason. The type stays in the schema so it can be re-enabled without API changes.
- `rect_curve.corners` are double-Y-flipped vs the rest of the response. The viewer works around it by rendering bboxes; side lengths (the values used for filtering and measurement labels) are unaffected. Details in [`docs/architecture.md`](docs/architecture.md#coordinate-systems).
