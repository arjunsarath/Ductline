# Ductline — Implementation

> **What this is:** the running implementation. Two pipelines are live:
> - **V4.5** (active dual-branch — rectangles → ducts, circles → air terminals,
>   plus length + CFM-aware pressure attribution). Same endpoint as V4
>   (`POST /v4/sessions`), additional fields on the response. Architectural
>   rationale: [`../adr/0017-v4.5-dual-branch-and-cfm-aware-pressure.md`](../adr/0017-v4.5-dual-branch-and-cfm-aware-pressure.md).
>   Inner CV primitives still come from V4 ([`../SOLUTION-DESIGN-V4.md`](../SOLUTION-DESIGN-V4.md)).
> - **V3** (colour-driven fallback) — `POST /v3/render` and `POST /v3/detect`,
>   design at [`../SOLUTION-DESIGN-V3.md`](../SOLUTION-DESIGN-V3.md) (superseded
>   header but retained as fallback).
>
> The frontend exposes a V3 / V4 tab toggle on the upload page. V3 is the
> default to avoid disturbing the existing colour-pick flow.
> **History:** the `app/pipeline/` modules outside `v3/` and `v4/` are V1 stages
> (reused for ingest + probe-OCR + raster orientation; their VLM-driven
> `detect.py` and `review.py` are no longer on the live path). Pre-V2 baseline
> screenshots have been archived under `archive/v1-v3/` — see
> [`archive/v1-v3/MANIFEST.md`](./archive/v1-v3/MANIFEST.md).

---

## 1. What's running

```
POST /v3/render   ── PDF/image in
                     ├─ adaptive-DPI render
                     ├─ rotation auto-correct
                     └─ rendered PNG + dominant-color swatches out

POST /v3/detect   ── PDF/image + user picks (HSV bands per system) in
                     ├─ render at adaptive DPI
                     ├─ horizontal OCR + 90°-rotated OCR (text-mask only)
                     ├─ HSV inRange + flood-fill (Pattern B) or dilate (centerline)
                     ├─ component filters (area, blob-shape, text-overlap)
                     ├─ skeleton + distance transform
                     ├─ token attribution (in-mask + proximity fallback)
                     ├─ histogram-ppu calibration
                     ├─ visible-side disambiguation
                     ├─ SMACNA pressure class derivation
                     └─ result + page PNG + transparent overlay PNG
```

The legacy V1 pipeline (`POST /agent/detect`) is still mounted at `/agent` for completeness but is not the live product surface.

### V3 stage table

| # | Stage | Module |
|---|---|---|
| 1 | Ingest (reused from V1) | `app/pipeline/ingest.py` |
| 2 | Probe OCR + rotation auto-correct (reused from V1) | `app/pipeline/probe_ocr.py` |
| 3 | Render at adaptive DPI | `app/pipeline/v3/runner.py` `_render_for_ocr` |
| 4 | Full-page OCR (horizontal + 90° rotated) | `app/pipeline/v3/ocr_classify.py` `ocr_full_page` + `app/pipeline/v3/runner.py` `_remap_bboxes_from_cw90` |
| 5 | Color mask + Pattern B fill / centerline dilate + filters | `app/pipeline/v3/color_mask.py` |
| 6 | OCR token classification (`dim_rect`, `dim_round`, `flow`) | `app/pipeline/v3/ocr_classify.py` |
| 7 | Token attribution (`in_mask` + `proximity` fallback) | `app/pipeline/v3/attribute.py` |
| 8 | Histogram-of-candidates ppu calibration | `app/pipeline/v3/calibrate.py` |
| 9 | Visible-side disambiguation | `app/pipeline/v3/calibrate.py` `resolve_visible_sides` |
| 10 | SMACNA pressure class | `app/pipeline/v3/pressure.py` |
| 11 | Overlay render | `app/pipeline/v3/render.py` |

Frontend (`frontend/src/components/v3/`):

```
V3Upload          → /v3/render
V3PickerView      → cursor-following magnifier, click-rejection feedback,
                    HSV band per pick (incl. dark-line band for V<60 picks),
                    pattern dropdown (outline | centerline)
V3CanvasViewer    → PDF.js dynamic re-rasterization on zoom + SVG marker
                    layer outside the transform stack (constant marker size)
V3ResultView      → results sidebar, segment cards, popover with reasoning
                    trace (chosen ppu + attribution rule + pressure source)
```

---

## 2. Prerequisites

- **Python 3.11** (the `.venv` is built against `>=3.11,<3.12`; 3.12+ has not been validated)
- **Node 18+** (tested on 22)
- **uv** for Python dependency management (`pip install uv` or `brew install uv`)
- ~4 GB free disk for the OCR ONNX models (RapidOCR caches them on first request)

V1 / V2 required a host-side Ollama instance for `llama3.2-vision`. **V3 has no VLM dependency** — it's pure deterministic CV + OCR. Ollama is only needed if you exercise the legacy `/agent/*` routes.

> **Docker is parked.** `docker-compose.yml` and the per-service Dockerfiles still exist but reflect the V1 era (Ollama env, "PaddleOCR" comments) and have not been validated since the V3 pivot. The active dev workflow uses native processes (uvicorn + Vite). Reviving docker is a deliberate piece of work — see §10 below.

---

## 3. Run (dev)

Two terminals, native processes — this is the workflow used throughout V3 development:

```bash
# Terminal 1 — backend
cd implementation/backend
uv sync                                                          # first run only
.venv/bin/uvicorn --app-dir . app.main:app --host 127.0.0.1 \
    --port 8000 --log-level info
```

```bash
# Terminal 2 — frontend (Vite proxy forwards /api/* → backend)
cd implementation/frontend
npm install                                                      # first run only
npm run dev                                                      # serves on 5173
```

- Backend: http://localhost:8000 (health: `/health`, OpenAPI docs: `/docs`)
- Frontend: http://localhost:5173

The backend looks for sample drawings at `../sample-HVAC/` by default (override with `V3_SAMPLES_DIR`). The Vite proxy reads `BACKEND_URL` (default `http://localhost:8000`).

### Restarting the backend during dev

Uvicorn is run **without** `--reload` because the OCR/CV pipeline has expensive import-time setup (RapidOCR ONNX session, OpenCV's heavy bindings) that makes auto-reload painful. To pick up backend code changes:

```bash
lsof -ti :8000 | xargs kill -9 2>&1 || true
sleep 2
.venv/bin/uvicorn --app-dir . app.main:app --host 127.0.0.1 --port 8000 --log-level info
```

Frontend changes hot-reload through Vite's HMR without manual restart.

### Verifying the install

```bash
# basic smoke check
curl -s http://localhost:8000/health
# → {"status":"ok"}

# regression test (backend)
cd implementation/backend
.venv/bin/python -m pytest tests/test_v3_runner.py -q
# → 1 passed in ~25s (drawing 03 segment count + ppu band)

# typecheck (frontend)
cd implementation/frontend
npx tsc -b --pretty
# → exit 0
```

### Running V4

V4 is the outline-based pipeline (no colour pick). It targets `testset2.pdf`
and any single-page PDF that follows the conventions A1–A15 documented in
[`../SOLUTION-DESIGN-V4.md`](../SOLUTION-DESIGN-V4.md) §2 and surfaced in the
frontend's V4 upload page banner.

```bash
# CLI (acceptance run on testset2.pdf)
cd implementation/backend
.venv/bin/python scripts/run_v4.py ../drawings/testset2.pdf

# HTTP (multipart upload, single-page PDF)
curl -X POST http://localhost:8000/v4/sessions \
     -F "file=@../drawings/testset2.pdf"
```

The frontend's V4 tab calls the same endpoint and renders the annotated
overlay, segment list (length_ft, dimension, CFM at endpoints, velocity,
pressure at endpoints, SMACNA class), terminal list (CFM, type letter), and a
Calculation Settings drawer where operational variables (air density, friction
factor, fitting K-values, flex equivalent length, threshold table) can be
edited; changes recompute live.

### V4 MVP assumptions (A1–A15)

V4 ships with 15 explicit drawing-convention assumptions locked in
`SOLUTION-DESIGN-V4.md` §2 and surfaced on the V4 upload page. The product
README has the full list verbatim — see [`../README.md` §2.4](../README.md).
The short version: dimension labels live inside the duct fill; rectangular
labels are `WxH`; terminals are circles with a horizontal divider; segments are
bounded by perpendicular cross-cut bars; transitions / elbows / tees /
equipment boxes are connectors, not segments; drawings are to scale; grey
architectural fill is stripped; all CFM is read from terminal symbols.

---

## 4. Observed accuracy (V3 5-drawing sweep)

Run on the benchmark set with the production V3 pipeline:

| Drawing | Convention | Picked color | Pattern | Segments | High conf | Trustworthy widths |
|---|---|---|---|---|---|---|
| 01-afdb-clean-cad | Cyan saturated outlines | RGB(0,255,255) | outline | 25 | 14 | ✓ |
| 02-newwest-mixed-trades | Black closed outlines + callout boxes | RGB(0,0,0) — dark band | outline | 20 | 7 | ✓ (2 with extracted CFM) |
| 03-caddsultants-shop | Blue saturated outlines | RGB(0,91,184) | outline | 35–60 | ≥25 | ✓ (regression-tested) |
| 04-asc2018-bid-set | Black parallel walls (no closed outline) | RGB(0,0,0) — dark band | centerline | 10 | 7 | ✗ (centerline-mode width is dilation thickness, not duct width) |
| 05-federal-attachment | Black parallel walls, dense plan | RGB(0,0,0) — dark band | centerline | 58 | 7 | ✗ (same as 04) |

Drawing 03's regression test (`backend/tests/test_v3_runner.py`) locks the numbers in: 35–60 segments, ≥18 rectangular, ≥10 round, ≥25 high-confidence, ppu in [4.0, 4.8]. CI runs on every change to mask filters, regex grammar, or attribution logic.

**Latency** (vector-PDF input, 600-DPI render, on a 2024 MacBook Pro):

- `/v3/render`: ~7s (full-page render at adaptive DPI dominates)
- `/v3/detect`: ~18–28s, dominated by:
  - Full-page OCR pass (~10s)
  - 90°-rotated OCR pass for text-mask (~10s)
  - Mask building, attribution, calibration (~5s)
- The rotated OCR pass is the marginal cost of catching vertical text labels (drawings often place labels rotated along vertical ducts). It can be made conditional in a future iteration.

---

## 5. V3 layered architecture (current)

```
┌────────────────────────────────────────────────────────────────┐
│                         V3PickerView                           │
│  ┌──────────────────┐    ┌────────────────────────────────┐    │
│  │ Cursor magnifier │    │ Pick cards: label, kind, band  │    │
│  └──────────────────┘    └────────────────────────────────┘    │
│   ↓ POST /v3/detect with picks_json                            │
└────┬───────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                      V3DetectionPipeline                        │
│                                                                 │
│  IngestStage  ──►  ProbeOCRStage  ──►  _render_for_ocr          │
│                                              │                  │
│                                              ▼                  │
│                          OCR full-page (horizontal + rotated)   │
│                                              │                  │
│                                              ▼                  │
│                          classify_all → dim_rect, dim_round,    │
│                                          flow                   │
│                                              │                  │
│                                              ▼                  │
│                          build_all_system_masks                 │
│                            • hsv_inrange                        │
│                            • fill_outline OR thicken_centerline │
│                            • drop_small_components              │
│                            • drop_blob_components               │
│                            • drop_text_components               │
│                            • skeletonize + distance transform   │
│                                              │                  │
│                                              ▼                  │
│                          attribute_in_mask + proximity fallback │
│                                              │                  │
│                                              ▼                  │
│                          calibrate (histogram of candidates)    │
│                                              │                  │
│                                              ▼                  │
│                          resolve_visible_sides                  │
│                                              │                  │
│                                              ▼                  │
│                          pressure: from_flow or from_size_only  │
│                                              │                  │
│                                              ▼                  │
│                          render_overlay → page_png + overlay    │
└─────────────────────────────────────────────────────────────────┘
```

### Filter pipeline inside `build_system_mask`

Order matters; each filter's tuning depends on the previous filters' output:

```
hsv_inrange(raw)                                   raw black mask
    ↓
fill_outline(raw, close_k=11)                      flood-filled interiors
    ↓
drop_small_components(min=1500 px²)                kills text-glyph noise
    ↓
drop_blob_components(area_floor=500k, fr_max=0.5)  kills room-sized fills + title blocks
    ↓
drop_text_components(overlap_threshold=0.30)       kills callout boxes
    ↓
skeletonize + distance transform                   centerline + half-width
```

### Why each filter exists

- **`drop_small_components`** — without it, text glyphs that share the picked color (e.g., maroon "ROYE" text in a drawing where return ducts are maroon) flood into tiny "pseudo-duct" components.
- **`drop_blob_components`** — discriminator is `bbox_fill_ratio`. A duct *tree* on a dense plan fills only 10–30% of its bounding box (interconnected branches with empty space between); a room or title block fills 70–90% (compact rectangle). Drop only when *both* `area > 500k` *and* `fill_ratio > 0.5`. This preserves drawing 03's giant interconnected supply tree (1.4M px, ~15% bbox fill) while killing drawing 02's bathroom flood-fills and title blocks.
- **`drop_text_components`** — discriminator is text/component-area overlap ratio. A drawing-02-style callout box (`TG | 24x12`) has ~30% text/area; a real duct interior has <5%. Threshold 0.30.

### OCR rotation pass

PaddleOCR's detection head is trained primarily on horizontal text and routinely misses vertical/rotated labels. We run OCR a second time on the page rotated 90° CW and remap those bboxes back to original coords. The rotated matches are used **only** for the text-exclusion mask, never for downstream classification (rotated text shouldn't drive the dim grammar — the rotated reading is often noisy).

### Regex grammar (real-world variants)

```
_DIM_RECT  = (?<!\d)(\d{2,4})\s*[\"”]?\s*[xX×]\s*[\"”]?\s*(\d{1,4})(?!\d)
   └─ matches  15x13   (drawing 03 — bare)
              28"x18"  (drawing 02 — inch-marked)
              12"x10"  (drawing 05 — Federal/SmithGroup)

_DIM_ROUND = (?<!\d)(\d{1,4})\s*(?:[\"”]?\s*[øØ⌀∅] | [\"”]\s*[0OQD](?!\d))
   └─ catches both native diameter symbols and OCR misreads ("0" / "O" / "Q" / "D")

_CFM       = (?<!\d)(\d{1,5})\s*CFM
_LPS       = (?<!\d)(\d{1,5})\s*L/?S
```

### Attribution rules

1. **In-mask (preferred)**: token's bbox-row intersects the system's filled mask. Anchor = first in-mask pixel along the bbox row, snapped to nearest skeleton pixel within `nearest_skel_search_px=80`.
2. **Proximity (fallback)**: token's bbox doesn't intersect any mask. Snap to the closest skeleton pixel of any system within `proximity_attr_search_px=50`. Tag the rule as `"proximity"` so downstream confidence can be more skeptical.

The proximity radius was tuned to **50 px at 600 DPI ≈ 0.08 in** of page-space. At 150 px (initial value) drawing 03 over-attributed equipment labels to nearby ducts and the regression test broke; 50 px is tight enough that only labels deliberately placed beside the duct survive.

### Picker UX (frontend `V3PickerView`)

Three iterations led to the current design:

1. **Side-panel swatch grid (deprecated)**: showed the dominant colors as a clickable list. Users couldn't tell which swatch corresponded to which on-page duct.
2. **On-canvas swatch markers (deprecated)**: each dominant color got a marker dropped at one of its representative pixels. Better, but still a pre-canned list — couldn't handle drawings with non-quantized colors.
3. **Cursor-following magnifier (current)**: the user moves the cursor over the page; a 140 px circular magnifier shows the pixels under the crosshair at 6× zoom (`image-rendering: pixelated` for crisp visual). Click samples that exact pixel. The magnifier sits *outside* the transformed wrap so it's constant size + sharp regardless of page zoom.

The picker accepts dark picks (V<60) by detecting the click target is a dark line, building a permissive HSV band (any hue, any saturation, V≤60), and labeling the pick "Marked duct (dark)". For non-dark picks it builds a hue-centred band. This handles both color-coded (drawings 01, 03) and dark-line (drawings 02, 04, 05) conventions through the same picker.

---

## 6. Known limitations

### 6.1 V4.5 (dual-branch, current)

What works on `testset2.pdf` end-to-end:

- Rectangle contour detection + duct-grammar OCR (`22"x14"`, `14"ø`) via Tesseract→VLM ladder, 8 workers, image-hash cached.
- Circle contour detection + horizontal-divider check + 3-digit CFM OCR (same ladder, same cache).
- Median px-per-inch scale → length in feet per duct.
- Direct-adjacency CFM (≤6 px duct↔terminal bbox edge) → terminal CFM exact.
- Neighborhood-weighted CFM proxy across a scale-derived 4 ft radius for ducts without a touching terminal.
- Velocity → Darcy ΔP → SMACNA class (Low/Medium/High) per duct.
- Frontend: full-pipeline-on-confirm, 7-stage progress UI with per-bbox progress bars, PDF underlay with adjustable opacity, "shade by pressure class" overlay, click-to-highlight linked terminal, inspector with length/CFM/velocity/ΔP/class + `est.` flag for fallback values, stat strip with counts and class breakdown.

Known limitations (see [§5.1 of the product README](../README.md) for the production roadmap addressing each):

1. **Network airflow is not summed.** A trunk duct with several downstream terminals gets the *neighborhood-weighted* proxy, not the sum. Direct simulation is the §5.1 first item.
2. **VLM still hallucinates** on hard crops. The 3-digit regex predicate and `standardize_duct_label` catch most, but a digit OCR'd as a similar glyph can pass and inflate the median scale. Multi-pass voting + cross-check against pixel-short-side is queued in §5.1.
3. **Image preprocessing is grey-strip + binarise only.** Scanned PDFs, skewed exports, low-DPI sources fail. Probe-OCR rotation auto-correct and adaptive DPI both exist in V1's `app/pipeline/probe_ocr.py` and need re-wiring.
4. **Underlying ducts (A7 dashed crossings) fuse or break.** `app/cv/crossings.py` has prototype logic but isn't on the V4.5 path.
5. **Ducts without dimension labels drop out** of the duct branch entirely. A9 pixel-measurement fallback was wired in V4 design (`_synthesize_missing_labels` in `runner_v4.py`) but is currently dead code on the dual-branch path.
6. **Bends, elbows, tees, transitions are not classified as connectors.** They appear as small or oddly-shaped rectangles; the duct branch keeps or drops them based on ink density alone. `app/cv/connectors.py` has prototype detectors. Fitting K-values already live in `OperationalVars.fitting_k_table`, so the wiring is the missing piece, not the math.
7. **Single-page PDFs only.** `read_page_rotation` raises if `doc.page_count != 1`.
8. **Equipment nodes** (VAV / FPB / AHU) treated as generic rectangles. No equipment-type semantics.
9. **Cross-sheet continuations** (`see M3.0`) are dead-ends.

### 6.2 V3 (colour-driven fallback)

1. **Parallel-wall ducts (drawings 04, 05) — bogus widths.** Centerline mode attributes labels to duct centerlines correctly but the pixel width measurement is the dilation thickness, not the duct gap. `dim_confidence: high` from centerline mode is therefore not a real signal. Fix: OCR-anchored Pattern A — see roadmap in [`../README.md` §5.1](../README.md). Estimated 2–3 weeks.
2. **Manual color pick.** Every drawing requires the user to identify each duct system's color (one click + optional pattern toggle per system). On drawings with 1–2 systems this is fast; on drawings with 6+ trades sharing a sheet it's tedious. Auto-detection roadmap is in [`../README.md` §5.2 + §5.3](../README.md).
3. **Single page only.** Multi-sheet PDFs are processed page-by-page with no cross-sheet stitching. Real estimating sets are 8–40 sheets with cross-references. M3–M6 in the production timeline.
4. **No schedule extraction.** Equipment lists, room schedules, and material specs are visible on the rendered page but not structured in the API output. Reasonable extraction would add 30–40% to the structured output value.
5. **No flow-attribution → real CFM tracing.** When a duct has a CFM label inside its mask, we extract pressure class from the flow. When CFM labels live at diffuser positions outside the mask (drawings 03, 05), the segment falls back to size-only pressure-class estimation. A duct-topology pass that aggregates downstream CFMs would close this — V3 §10 phase-2.
6. **Adaptive DPI cap of 600.** A 1200-DPI experiment showed it doesn't help structurally on dense plans (the flood-fill leaks regardless of resolution), so the cap stays at 600. Documented in [`../adr/0011-v3-pivot-rationale.md`](../adr/0011-v3-pivot-rationale.md) (added with this round of docs).

---

## 7. Layout

```
implementation/
├── README.md             (this file)
├── docker-compose.yml    (backend + frontend; no Ollama needed for V3)
├── .env.example
├── backend/
│   ├── pyproject.toml    (FastAPI, pdf2image, opencv, rapidocr, pymupdf)
│   ├── Dockerfile        (python:3.11-slim + poppler + libgl)
│   ├── tests/
│   │   └── test_v3_runner.py   (drawing 03 regression — 35–60 segments,
│   │                            ppu in [4.0, 4.8], ≥25 high-confidence)
│   └── app/
│       ├── api/
│       │   ├── v3_routes.py    (LIVE — POST /v3/render, /v3/detect, /v3/samples)
│       │   ├── routes.py       (LEGACY — V1 /agent/* routes, parked)
│       │   └── deps.py
│       ├── pipeline/
│       │   ├── v3/             (LIVE — color_mask, runner, attribute,
│       │   │                    calibrate, ocr_classify, pressure, render)
│       │   ├── ingest.py       (reused from V1)
│       │   ├── probe_ocr.py    (reused from V1)
│       │   └── …               (other V1 stages, parked)
│       ├── ocr/                (RapidOCR ONNX wrapper)
│       ├── source/             (vector-PDF + raster source abstractions)
│       ├── vlm/                (parked — VLMClient seam preserved for
│       │                        future hybrid + V1 retrospective)
│       └── main.py
├── frontend/
│   ├── package.json    (Vite + React 18 + TS 5.6 + pdfjs-dist)
│   └── src/
│       ├── components/v3/   (V3Upload, V3PickerView, V3PageCanvas,
│       │                     V3CanvasViewer, V3ResultView, V3Popover,
│       │                     colorMath)
│       ├── components/      (V1 components — parked, no longer rendered)
│       ├── api/v3Client.ts  (LIVE)
│       ├── api/             (V1 client — parked)
│       ├── types/v3.ts      (TS mirrors of V3 Pydantic schemas)
│       └── styles/v3.css
└── drawings/                (5 benchmark PDFs from ../sample-HVAC/)
```

---

## 8. What's next (M0–M2 implementation list)

Tactical steps that turn the partial drawings 04/05 result into trustworthy detection. See [`../README.md` §4`](../README.md) for the broader timeline.

| Item | Effort | Owner-blocking? |
|---|---|---|
| OCR-anchored Pattern A (Hough + parallel-pair clustering around each dim label) | 2–3 weeks | unblocks 04/05 confidence |
| Pattern A auto-detection (no manual pattern toggle) | 1 week | follows |
| Confidence calibration with 200-drawing labeled corpus | 6 weeks | parallel with above |
| Schedule + legend extraction stage | 3–4 weeks | independent |
| Multi-page support (frontend + backend) | 3–4 weeks | independent |

The drawing-03 regression test should remain green through every change. Anything that moves drawing 03's segment count outside [35, 60] or the ppu outside [4.0, 4.8] is either an explicit re-tune (with the test bounds updated and the rationale in the diff) or a regression to investigate.

---

## 9. Parked: docker compose, V1 routes, VLM seam

Three pieces of the codebase exist but aren't on the V3 live path. Documented here so future contributors know what to expect:

**`docker-compose.yml` + `backend/Dockerfile` + `frontend/Dockerfile`** — written for V1 (Ollama env, PaddleOCR comment), untested since the V3 pivot. Almost certainly does not `up --build` cleanly today. To revive:

1. Drop `VLM_PROVIDER`/`OLLAMA_HOST_URL`/`OLLAMA_MODEL` from `docker-compose.yml` (V3 doesn't read them).
2. Delete the `extra_hosts: host.docker.internal:host-gateway` block (no Ollama to reach).
3. Update `backend/Dockerfile`'s "PaddleOCR" comment — actual dep is `rapidocr-onnxruntime` (per ADR-0006).
4. Mount `../sample-HVAC/` as `/drawings:ro` and confirm `V3_SAMPLES_DIR` resolves correctly inside the container (currently the default `/drawings` works because the docker-compose binds it; the host-dev fallback to `../sample-HVAC` only matters outside docker).
5. Verify `uv` install path inside the slim image — current Dockerfile uses `pip install .` which works but is slower than `uv sync`.

About 1 hour of work + a smoke test against the regression suite.

**Legacy `POST /agent/*` routes (`app/api/routes.py` + V1 pipeline stages outside `app/pipeline/v3/`)** — the V1 hybrid pipeline is still mounted but not exercised. The frontend has no UI surface for it. Kept for retrospective + as the place where `app/pipeline/ingest.py` and `app/pipeline/probe_ocr.py` (which V3 reuses) live.

**`app/vlm/` module** — the `VLMClient` Protocol + Ollama implementation is preserved per ADR-0011. Not imported by any V3 module. It's a 5-minute integration the day a hybrid path becomes desired.

---

## 10. Pointers within the repo

- **API contract:** `backend/app/api/v3_routes.py` (Pydantic schemas inline)
- **Pipeline entrypoint:** `backend/app/pipeline/v3/runner.py` `V3DetectionPipeline.run`
- **Filter knobs:** `backend/app/pipeline/v3/config.py` (every threshold lives here)
- **Frontend entrypoint:** `frontend/src/App.tsx`
- **Sample drawings + their conventions:** `../sample-HVAC/` and §4 above

For decision-level context (why each filter, why each threshold), [`../SOLUTION-DESIGN-V3.md`](../SOLUTION-DESIGN-V3.md) is the source of truth and is more thorough than this file.
