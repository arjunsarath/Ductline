# Architecture

This document describes the runtime data flow, the coordinate systems in play, and where each pipeline stage runs (backend vs. client). For implementation details, see [`backend.md`](backend.md) and [`frontend.md`](frontend.md).

## Data flow

```mermaid
flowchart LR
    U[User] -->|drag/select PDF| UP[upload-screen.tsx]
    UP -->|File| CR[cropper.tsx]
    CR -->|CropRegion[] in PDF points| PG[page.tsx runPipeline]
    PG -->|POST /api/extract<br/>file + crop=JSON| EX[extractor.py]
    EX -->|ExtractResponse| PG
    PG -->|POST /api/detect-scale<br/>one per region, parallel| SC[scale_detector.py]
    SC -->|ScaleResponse| PG
    PG -->|cleanup is client-side<br/>350ms cosmetic delay| VW[viewer.tsx]
    VW -->|render full PDF<br/>+ overlays| BR[Browser]

    subgraph Backend FastAPI
        EX
        SC
    end
    subgraph Frontend Next.js
        UP
        CR
        PG
        VW
    end
```

## Pipeline stages

The frontend orchestrates three named stages. Stage state is `idle | running | done` for each stage; failures keep the stage in its current state and surface a non-null `error` on the pipeline state.

### 1. Extract (`POST /api/extract`)

- **Request**: multipart `file` + `crop` (JSON array of `{page, x0, top, x1, bottom}` regions in PDF points).
- **Response**: `ExtractResponse` containing one `Page` per region — `page_number`, `width`, `height`, `elements[]`.
- **Element types emitted**: `line`, `rect`, `rect_curve`, `rect_partial`, `curve`, `char`, `word`. `inferred_rect` is in the schema but disabled (see `extractor.py`).
- **What the crop does**: elements outside the per-page crop are filtered out by bbox intersection inside `_extract_page`. Only pages that have a crop entry are returned.

### 2. Detect scale (`POST /api/detect-scale`)

- **Request**: multipart `file` + `page_number` + `crop` (JSON object for one region) + `black_threshold` (frontend sends `0.02`; backend default is `0.05`).
- **Response**: `ScaleResponse` — `page_number`, `dpi`, `callouts[]`, `drawing_scale_pts_per_inch` (nullable), `callout_count`.
- **Per-region**: one HTTP call per crop region. The frontend runs all calls in parallel via `Promise.all`.
- **Failure**: the pipeline fails if any region returns a non-2xx, or if any region returns `drawing_scale_pts_per_inch === null` (i.e. no callout had a matching duct rectangle).

### 3. Cleanup (client-side)

There is no HTTP call. After scale detection finishes, `runPipeline` sleeps 350 ms and transitions to the viewer. The viewer applies these filters inside its `visibleElements` memo:

| Filter | Threshold | Where it lives |
| --- | --- | --- |
| Element-type checkbox | user-controlled (`rect`, `rect_curve`, `rect_partial` default on) | `Viewer.enabled` state |
| Black-ink colour | `hexLuma(color) ≤ 0.02` | `BLACK_THRESHOLD` in `viewer.tsx` |
| Rectangle aspect | `≥ 1.2:1` long-side / short-side | `MIN_RECT_ASPECT` in `lib/extract.ts` |
| Min side | `≥ 3.0 in` (both sides) | `MIN_DUCT_SIDE_INCHES` |
| Max area | `≤ 8000 sq in` | `MAX_DUCT_AREA_SQ_IN` |
| Search box | substring on `id` or text | `Viewer.search` state |

The min-side and max-area filters apply only when a scale was detected. The aspect filter applies only to `rect` and `rect_curve` (not `rect_partial`).

## Coordinate systems

There are three coordinate spaces in play and a documented bug at the boundary between two of them.

| Space | Origin | Used by |
| --- | --- | --- |
| **PDF points, top-left origin** | `(0,0)` top-left, `y` grows downward | The transport contract. Backend responses, frontend props, all element bboxes. |
| **PDF user space, bottom-left origin** | `(0,0)` bottom-left, `y` grows upward | pdfplumber internals; `page.curves[*]['pts']` come back in this space. The extractor flips them: `[x, page_h - y]`. |
| **CSS pixels** | `(0,0)` top-left | DOM. The viewer multiplies PDF points by `baseScale = render.width / displayPageW` to render overlays. |

### The `rect_curve.corners` double-Y-flip

`extractor.py` calls `rect_corners_from_curve(c)` (from `scale_detector.py`). That helper reads `c["pts"]` — which pdfplumber has **already converted to top-left coords** when it returns them via `page.curves` — but the function operates as if the points are still in bottom-left space. The extractor then applies `[x, page_h - y]` to the returned corners a second time.

Result: `corners` ship to the frontend mirrored vertically about the page mid-line. The axis-aligned `x0/top/x1/bottom` come from a different pdfplumber attribute and are not affected; they're correct.

**Workaround in the viewer**: `MeasurementsOverlay.MeasurementItem` and `ElementOverlay` render the axis-aligned bbox for every rect-family element, never the polygon from `corners`. Side-length calculations in `rectSideLengthsPts` use `Math.hypot` on consecutive corner pairs — those are distances, translation- and reflection-invariant, so the reported `W"×H"` is correct for rotated rectangles even with mirrored corners.

If you fix the bug, also remove the workaround comment in `viewer.tsx` near `hitShape`.

## How cropping interacts with extraction and display

The crop has two distinct jobs that are easy to conflate:

1. **As an analysis boundary** — `/api/extract` filters elements to those intersecting the crop, and `/api/detect-scale` only looks for callouts inside the crop. The user can isolate a single drawing area on a multi-drawing sheet.
2. **As an outline on display** — the viewer renders the **full original page** (not the crop) via react-pdf and draws the crop as a dashed indigo rectangle so the user knows which area produced the detected items. The previous `/api/preprocess` SVG-debug-view is no longer wired up; the endpoint still exists in `main.py` (see `preprocess.py`).

Because the display is the full page, no coordinate translation is needed between backend element bboxes and display: elements are already in original-PDF coordinates. `shiftElement` and `shiftScaleResponse` in `lib/extract.ts` are legacy and unused by the viewer.

## File layout

```
implementation/backend/        Python, FastAPI
  main.py                      Routes, request models, multipart parsing
  extractor.py                 _extract_page, _intersects, color hex conversion
  scale_detector.py            All scale-detection helpers; also exports rect classifiers used by extractor
  preprocess.py                Legacy SVG black-only renderer
  requirements.txt             (PyMuPDF missing — see development.md)

implementation/frontend/       Next.js 16, React 19, all client components
  src/app/
    layout.tsx                 Geist fonts + Toaster (single root)
    page.tsx                   State machine + runPipeline orchestrator
  src/components/
    app-header.tsx             Shared header bar
    upload-screen.tsx          Drag/drop, health check
    cropper.tsx                Multi-page region drawing with handles + ESC clear
    pipeline-progress.tsx      Three-row status card
    viewer.tsx                 The "result" view; owns zoom/pan/filters/overlays
    pdf-page.tsx               react-pdf wrapper, adaptive DPI, opacity
    element-overlay.tsx        Canvas paint of every visible element
    ui/                        shadcn primitives (button, badge, input, etc.)
  src/lib/
    extract.ts                 Types + filter helpers
    utils.ts                   cn() — clsx + tailwind-merge
```

The `App Router` configuration uses `"use client"` on every page and component. There is no server-side rendering and no use of cache-components or RSC: the viewer mounts via `next/dynamic` with `ssr: false` because `pdfjs-dist` reads `window`/`Worker` at module load.
