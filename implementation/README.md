# HVAC Duct Detection — Implementation

Build artifact for the system specified in [`../PRD.md`](../PRD.md), [`../SOLUTION-DESIGN.md`](../SOLUTION-DESIGN.md), and [`../UI-SPEC.md`](../UI-SPEC.md). All design decisions are pinned in [`../adr/`](../adr/).

## What's running

Full 7-stage pipeline (SOLUTION-DESIGN §4) wired end-to-end behind `POST /detect`:

| # | Stage | Type | Module |
|---|---|---|---|
| 1 | Ingest | ALG | `backend/app/pipeline/ingest.py` |
| 2 | Quality check | ALG | `backend/app/pipeline/quality.py` + `cv/quality.py` |
| 3 | Region detect | ALG + VLM fallback | `backend/app/pipeline/regions.py` + `cv/regions.py` |
| 4 | Duct detection | AGT + ALG | `backend/app/pipeline/detect.py` + `cv/ducts.py` |
| 5 | Text extraction | ALG | `backend/app/pipeline/extract.py` + `ocr/grammar.py` + `ocr/rapid.py` |
| 6 | Pressure-class | WF | `backend/app/pipeline/classify.py` |
| 7 | Assemble | ALG | `backend/app/pipeline/assemble.py` |

Frontend (`frontend/src/`) per UI-SPEC.md: Upload → Processing → Result, no router; canvas + SVG overlay with PC-keyed colors and dashed strokes for low-confidence detections; click-to-popover with reasoning trace; sortable sidebar + aggregate stats.

## Prerequisites

- Docker + Docker Compose
- Ollama running on the host with `llama3.2-vision` pulled:
  ```bash
  ollama serve                       # if not already running
  ollama pull llama3.2-vision        # ~7.9 GB
  ```

## Run

```bash
cp .env.example .env
docker compose up --build
```

- Backend: http://localhost:8000 (health: `/health`)
- Frontend: http://localhost:5180 (host port 5180 → container 5173, set in `docker-compose.yml` to avoid colliding with other local Vite servers)
- 5-drawing benchmark sweep: `python3 scripts/smoke_run.py` (after backend is up)

First request takes a few minutes — RapidOCR downloads its ONNX models (~50 MB) on first use, and Ollama warms up `llama3.2-vision` on its first vision call.

## Observed accuracy (5-drawing sweep)

Run on 2026-05-02 with `llama3.2-vision:latest` (11B) on Apple Silicon:

| Drawing | Segments | Latency | VLM result | Notes |
|---|---|---|---|---|
| 01-afdb-clean-cad | 60 (CV cap) | 87s | malformed JSON → CV fallback | Clean CAD baseline |
| 02-newwest-mixed-trades | 60 | 183s | Ollama timeout → CV fallback | Mixed trades, slowest VLM call |
| 03-caddsultants-shop | 60 | 70s | malformed JSON → CV fallback | Shop drawing |
| 04-asc2018-bid-set | 60 | 64s | invalid JSON → CV fallback | Bid-set drawing |
| 05-federal-attachment | 60 | 87s | malformed JSON → CV fallback | Federal-spec attachment |

**Latency vs. PRD §9.1 targets** — P50 target ≤30s, observed P50 ~87s. The 30s target was aspirational on a host-Ollama setup; the bottleneck is the vision model, not the pipeline. Anthropic Claude vision (parked behind the seam in `vlm/factory.py`) would meet target.

**Detection quality** — all 5 drawings hit the CV-detection cap of 60 candidates, indicating the filtered HoughLinesP+parallel-pair sweep over-recalls. Expect false positives from columns, walls, and grid lines that survive filtering. The reasoning-trace popover is honest about which detections came from CV-only fallback (`[cv_fallback]` stage marker).

## Known limitations (v1)

1. **`llama3.2-vision` 11B doesn't reliably do bbox detection on engineering drawings.** Three failure modes observed: parroting prompt examples, hallucinating regular grids of bboxes, producing JSON missing required fields. The ADR-0002 `VLMClient` seam lets you swap to a stronger model — `llama3.2-vision:90b`, Anthropic Claude vision, or an open-vocab detector (Grounding DINO) — without touching the pipeline. The Anthropic provider is provisioned in `vlm/factory.py` but not implemented in v1.
2. **CV-only detection over-recalls.** HoughLinesP fires on every parallel-line pair — including walls, columns, drawing borders, and grid lines. Current filters (border distance, aspect ratio, NMS) cap output at 60 candidates per drawing but don't distinguish a duct from a column. A v1.1 improvement would require OCR-proximity gating (only keep candidates with a callout within 30 px) which would also serve as the dimension extractor for stage 5.
3. **OCR engine swap from PaddleOCR to RapidOCR.** PaddlePaddle has no reliable arm64 Linux wheels (paddlepaddle 2.6.1 is x86_64-only). RapidOCR runs the same models via ONNX Runtime — same quality, no paddlepaddle dependency. Documented as the escape hatch in ADR-0006 + SOLUTION-DESIGN §11.
4. **No `/raster/{drawing_id}` endpoint.** Display image is sent inline as a base64 data URL on the response (downsampled to 2000 px long edge, ~1 MB JSON per drawing). Trade-off: keeps the backend stateless (ADR-0005) at the cost of payload size.

## Layout

```
implementation/
├── README.md               (this file)
├── docker-compose.yml      (backend + frontend; Ollama on host)
├── .env.example
├── backend/
│   ├── pyproject.toml      (FastAPI, pdf2image, opencv, rapidocr, httpx)
│   ├── Dockerfile          (python:3.11-slim + poppler + libgl)
│   └── app/
│       ├── api/            (REST routes, deps wiring)
│       ├── pipeline/       (7-stage orchestration + per-stage modules)
│       ├── vlm/            (VLMClient Protocol + Ollama impl + tool schema + prompts)
│       ├── ocr/            (OCRExtractor Protocol + RapidOCR impl + dimension grammar)
│       ├── cv/             (OpenCV utilities: ducts, regions, quality)
│       ├── schemas.py      (Pydantic API contract)
│       ├── config.py       (settings)
│       └── main.py
├── frontend/
│   ├── package.json        (Vite + React 18 + TS 5.6)
│   └── src/
│       ├── api/            (fetch client)
│       ├── components/     (UploadView, ProcessingView, ResultView, Viewer, Sidebar, Popover, ...)
│       ├── types/          (TS mirrors of Pydantic schemas)
│       └── styles/global.css
├── drawings/               (5 benchmark PDFs from ../Sample-HVAC/)
└── scripts/
    └── smoke_run.py        (5-drawing benchmark sweep)
```

## Benchmark drawings

Sourced from `../Sample-HVAC/` (provided with the take-home):

1. `01-afdb-clean-cad.pdf`
2. `02-newwest-mixed-trades.pdf`
3. `03-caddsultants-shop.pdf`
4. `04-asc2018-bid-set.pdf`
5. `05-federal-attachment.pdf`

## What's next

Following the SOLUTION-DESIGN §10 day-2 plan, the highest-signal v1.1 improvements are:

- **Swap to a stronger VLM** (Anthropic Claude vision or `llama3.2-vision:90b`). The seam is ready — just implement `claude.py` against the `VLMClient` Protocol and flip `VLM_PROVIDER=claude` in `.env`.
- **OCR-proximity filtering** in stage 4 to weed out CV false positives (columns, walls).
- **Schedule-region heuristics** that don't rely solely on grid-line detection — many schedules have no grid (just text rows).
