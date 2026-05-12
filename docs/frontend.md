# Frontend

Next.js 16 App Router project at `implementation/frontend/`. Every page and component carries `"use client"`. The viewer mounts via `next/dynamic` with `ssr: false` because `pdfjs-dist` accesses `window` / `Worker` at module load.

A reminder from `implementation/frontend/AGENTS.md` (echoed by `CLAUDE.md`):

> This is NOT the Next.js you know — APIs and conventions may differ from your training data. Read `node_modules/next/dist/docs/` before writing new code. RSC and cache-component patterns do not apply here.

This is a single-session interactive tool. No server-side data fetching, no caching, no persistence between reloads.

## Screen flow

`src/app/page.tsx` is a discriminated-union state machine.

```
upload  ──Continue──▶  crop  ──Run extraction──▶  pipeline  ──success──▶  viewer
   ▲                    │                              │
   │                    │                              │── failure ──▶ error card
   │                    │                                                  │
   └────────────────────┴── New file (header), Start over, Back to crop ──┘
```

States:
- `upload` — `{ file: File | null }`. The user has not yet committed to a file.
- `crop` — `{ file, pdfUrl }`. `pdfUrl` is `URL.createObjectURL(file)` and is revoked when the state changes.
- `pipeline` — `{ file, pdfUrl, regions, progress, error }`. `progress` is `{ extract, scale, cleanup } as PipelineState`.
- `viewer` — `{ file, pdfUrl, data, regions, scaleByPage }`.

### Cancel-safety with `runIdRef`

`runPipeline` increments `runIdRef.current` and captures the value before each async call. Each `setState` checks `if (runIdRef.current !== runId) return;` before writing. When the user re-crops (or returns to upload) mid-flight, the in-flight pipeline's writes are abandoned. There is no `AbortController` on the `fetch` calls — they complete in the background and their results are dropped.

`useEffect` on mount returns a cleanup that bumps `runIdRef.current` so a final unmount also abandons any in-flight run.

## Screens

### `upload-screen.tsx`

Drag/drop or click-to-browse. Validates by extension OR MIME (`application/pdf`) — relaxed because some browsers report blank MIME on drag-drop. The `POST` URL is shown in the footer for transparency; clicking "Continue" calls `onContinue(file)` and the parent transitions to `crop`.

### `cropper.tsx`

Multi-page region selection.

- `pageNumber` (1-based) is the currently displayed page.
- `regions: Map<number, Region>` where `Region = { rect: Bbox, scale: number }`. Each region stores its rectangle in **screen pixels** plus the `scale` (CSS px per PDF point) used when it was drawn, so each page converts to PDF points independently — pages can have different native sizes.
- `pageWidth` is the rendered width in CSS px. Capped at `Math.min(900, max(window.innerWidth - 360, 480))` to keep the rasterised canvas small; pdfjs OOMs Chrome on dense A1/A0 drawings above ~1000 px wide.

Drag modes (`DragMode` discriminated union):

| Kind | When | Behaviour |
| --- | --- | --- |
| `draw` | empty space, anywhere | Drag from origin to current pointer; rejected if either side < 4 px on release. |
| `move` | inside an existing selection (`data-role="move"`) | Translate the rect within page bounds. |
| `resize` | on a corner/edge handle (`data-handle`) | Resize from the opposite anchor; `n`/`s` lock the x-axis, `e`/`w` lock the y-axis. |

`ESC` clears the current page's region. The "Run extraction" button is disabled when no regions exist; clicking it converts every page's pixel-space rectangle to PDF points using its stored `scale` and emits `CropRegion[]` sorted by page number.

The PDF page is rendered by `PdfPage`. Render info is stored as `{ page, info }` and only used when `info.page === pageNumber`, so a stale page's render info is naturally ignored without an explicit reset effect when the user pages forward.

### `pipeline-progress.tsx`

Three-row card listing the stages. Status per row:

- `idle` — bullet, grey border.
- `running` — spinner, primary-coloured border.
- `done` — check, emerald-coloured border.
- error — the row that was `running` when the failure arrived gets a triangle icon and destructive colours, computed by `hasError = error !== null && state[stage] === "running"`.

The error message is rendered below the rows along with two buttons: **Start over** (back to upload) and **Back to crop** (preserves the file and pdfUrl).

### `viewer.tsx`

The full-page result viewer. Renders the **original PDF** (not a crop) via `PdfPage` and lays overlays on top.

#### State

| State | Purpose |
| --- | --- |
| `pageIdx` | 0-based index into `data.pages`. |
| `enabled: Record<ElementType, boolean>` | Type filter. Default: `rect`, `rect_curve`, `rect_partial` on; everything else off. `inferred_rect` stays off (backend doesn't emit it). |
| `showLabels` | Show element IDs over the canvas overlay. |
| `search` | Substring match on `id` or text. |
| `highlightedId`, `hoveredId` | Selected vs. hovered element. The active overlay highlight is `highlightedId ?? hoveredId`. |
| `transform: { scale, tx, ty }` | Zoom (anchored at pointer) and pan. Clamped `[MIN_SCALE=0.1, MAX_SCALE=20]`. |
| `pageWidthCss` | CSS width the PDF is laid out at. Capped 900 px. |
| `rasterWidth` | Width pdfjs rasterises at — tracks `pageWidthCss * transform.scale`, capped at `MAX_RASTER_WIDTH=3000`, debounced 220 ms. |
| `animating` | Triggers a 220 ms CSS transition during `focusElement`. |
| `renderInfo` | Last `PdfRenderInfo` from `PdfPage.onRender`. |
| `pdfOpacity` | 0–1 slider value applied only to the PDF, not to overlays. |

#### Filters (the cleanup stage)

`visibleElements` and `counts` apply the same chain:

1. `enabled[el.type]` must be true.
2. `elementColor(el)` if present must satisfy `hexLuma(color) ≤ 0.02`. (`hexLuma` is max-channel, mirroring backend `_is_black`.)
3. `passesRectAspect(el)` — only applies to `rect` / `rect_curve`; requires `max(w/h, h/w) ≥ 1.2`.
4. If `drawing_scale_pts_per_inch` is known:
   - `passesMinSideInches(el, sp)` — both sides ≥ 3.0 in.
   - `passesMaxAreaInches(el, sp)` — area ≤ 8000 sq in.
5. If `search` is non-empty, match `id` substring, or `text` substring for chars/words.

`passesRectAspect`, `passesMinSideInches`, `passesMaxAreaInches` all return `true` for non-rectangle types — lines/chars/words/curves are dropped earlier by the type filter, so this is just a defensive default.

`rectSideLengthsPts` returns the true sides of rotated `rect_curve` elements via `Math.hypot` on consecutive `corners` pairs. Because the bug double-flips `corners` vertically, distances are preserved (reflection is a rigid transform), so the reported dimensions are correct.

#### Transform / raster width

`visualScale = (pageWidthCss * transform.scale) / rasterWidth`. When the rasterised canvas matches the user's zoom request, `visualScale == transform.scale`. When the user zooms beyond `MAX_RASTER_WIDTH`, the raster stays at 3000 px and `visualScale` keeps growing — the canvas is then CSS-upscaled and looks pixelated, but the browser doesn't OOM trying to rasterise an 8000 px engineering plan.

The 220 ms debounce on `rasterWidth` keeps the wheel smooth: while the user is actively zooming, the canvas stretches via CSS; only when motion settles does pdfjs re-render at the new resolution.

#### Pan / zoom controls

- **Wheel** — non-passive listener, `preventDefault`'d. `factor = exp(-deltaY * 0.0015)` for smooth exponential zoom, anchored at the pointer.
- **Drag** — empty stage background, or spacebar + drag. Inputs/buttons/`data-role="no-pan"` regions don't initiate pans.
- **Keyboard** — `+`/`=` zooms in, `-`/`_` zooms out, `0` and `F` fit to view, arrow keys pan in 40 px steps. All shortcuts skip when the target is an input/textarea/contentEditable.
- **`focusElement(id)`** — pans + zooms so the element sits in the centre at ~30% of viewport size, clamped to `[1.5, 12]`. Triggered by clicking an element in the right-pane element list.

#### Overlays inside the transform layer

Rendered in z-order over the rasterised PDF:

| Overlay | Purpose |
| --- | --- |
| `CropOutline` | Dashed indigo rectangle showing which area the pipeline processed. Drawn only on pages that had a crop. |
| `ElementOverlay` | Canvas paint of every element in `visibleElements`. SVG would melt above ~5k items; canvas does the heavy paint in one pass. Per-element fill alpha is `0.10` so stacked rectangles compound visibly. |
| `CalloutOverlay` | One SVG group per callout: orange dashed `enclosing_rect`, orange solid callout bbox with `text` label + drawn-diameter pts + confidence %, cyan wall-pair edges with a dashed midpoint segment carrying the `distance_pts` value. Callouts that failed geometry inference render at 0.45 opacity. |
| `MeasurementsOverlay` | One green translucent rectangle per visible rect-family element, with a `W"×H"` label centred in inches (when there's room — `minDim > 16`). SVG root is `pointer-events: none`; each shape sets `pointer-events: all` so empty space falls through to the stage's pan handler. Click toggles `highlightedId`; selection card pops up above (or below if no room) showing `id`, dimensions, and raw pt sides. |

The label fills use `paintOrder: stroke` with a white outline so text stays readable against any background.

#### Right pane — `ElementList`

`@tanstack/react-virtual` for virtualisation (essential — engineering PDFs routinely return 1–10k rectangles). Each row renders the element id, a coloured type badge, and a one-line description (the `(x0, top) → (x1, bottom)` bbox or the char/word text). Click selects + focuses the element on stage; hover sets `hoveredId` so the corresponding overlay rect highlights.

#### Read-only badges in the header

- `ScaleBadge` — shows the inferred scale as a friendly `1:N` ratio (snapped to the closest common architectural scale when within 8% relative error) and the callout count.
- `OpacityControl` — slider that wires to `pdfOpacity` and only dims the PDF layer.

### `pdf-page.tsx`

Wraps `react-pdf`'s `Document` + `Page`.

- Worker source: `pdfjs-dist/build/pdf.worker.min.mjs`, set once at module load via `new URL(...).toString()`.
- `width` prop sets `<Page width={containerWidth}>`. If `width` is omitted, a `ResizeObserver` on the container reports its content-width to `observedWidth`.
- `devicePixelRatio={1}` is intentional — DPR ≥ 2 OOM-crashed Chrome on retina screens with dense vector drawings. The viewer compensates by passing a `width` that already includes the zoom factor.
- The PDF dims via an opacity wrapper sitting **inside** the relative container but **before** `children`. Overlays stay at full opacity because they render outside that wrapper.
- `onRender` fires with `{ width, height, pointWidth, pointHeight }` — the actual rendered dimensions plus the native PDF point size, which the viewer uses to compute `baseScale = render.width / displayPageW`.

The viewer uses `key={`${page_number}-${rasterWidth}`}` so the component remounts on zoom changes, forcing pdfjs to re-rasterise at the new resolution rather than CSS-scaling its old canvas.

### `element-overlay.tsx`

Renders the entire visible-elements list to one `<canvas>` per render pass, with a separate SVG overlay for the active (hovered or selected) element so highlighting doesn't trigger a full canvas redraw.

Canvas is `pageWidth * 2` by `pageHeight * 2` in device px with `ctx.setTransform(2, 0, 0, 2, 0, 0)` for crisp output when the user CSS-zooms in.

Per-type rendering:
- `line` — `moveTo` / `lineTo` with `el.linewidth * scale` (min 1 px).
- `rect_partial` — polyline through `el.points` (the actual U-shape), **not** the bbox. The bbox over-claims the visible area.
- `inferred_rect` — dashed bbox to flag "synthetic, not in the PDF". Currently never emitted.
- `curve` — bbox `fillRect`/`strokeRect` plus a polyline through `el.points`.
- everything else — solid bbox `fillRect`/`strokeRect`.

`globalAlpha = 1` is intentional: the per-type fill colours already carry a `0.10` alpha, so overlapping rectangles compound darker on canvas.

## Library: `lib/extract.ts`

All filter helpers live here.

### Element types

```ts
export type Element =
  | LineElement
  | RectElement
  | RectCurveElement   // axis-aligned or rotated; `corners` + `points`
  | RectPartialElement // 3-segment U-shape
  | InferredRectElement
  | CurveElement
  | CharElement
  | WordElement;
```

`ELEMENT_TYPES` only exports the rect family (`rect`, `rect_curve`, `rect_partial`) — the user-facing filter pane and element list don't surface lines/chars/words/curves. They're still in the response and accessible via `Element`, just hidden.

### Filter constants

```ts
export const MIN_RECT_ASPECT      = 1.2;
export const MIN_DUCT_SIDE_INCHES = 3.0;
export const MAX_DUCT_AREA_SQ_IN  = 8000;
```

- **Aspect ≥ 1.2** drops squarer shapes — title-block cells, scale-bar boxes, equipment glyphs.
- **Min side ≥ 3 in** drops dimension stubs, arrowheads, callout cells.
- **Max area ≤ 8000 sq in** drops title blocks, page borders, full-room equipment outlines.

### `passesRectAspect(el, minRatio?)`

Only applies to `rect` and `rect_curve`. `rect_partial` passes through unchanged (its bbox over-claims, so an aspect check would be misleading).

### `passesMinSideInches(el, ptsPerInch, minInches?)` / `passesMaxAreaInches(el, ptsPerInch, maxSqIn?)`

Only apply to `rect` and `rect_curve`. Use `rectSideLengthsPts` which uses corner distances for rotated rect_curves — so rotated ducts are measured correctly.

### `elementColor(el)`

Returns the stored hex colour or `null`:
- `line` → stroke
- `rect` / `rect_curve` / `rect_partial` → stroke ?? fill
- `char` → fill
- `curve` / `word` / `inferred_rect` → `null`

The viewer treats `null` as "no colour info, keep" — only an explicitly-known non-black colour gets dropped.

### `hexLuma(hex)`

Max-channel approximation in `[0, 1]`. Matches backend `_is_black` (which also uses max-channel for RGB), so the panel filter agrees with the backend's vector-pass filter.

### `formatScale(ptsPerInch)`

Snaps `72 / ptsPerInch` (real-inches per paper-inch) to the closest of `[192, 96, 64, 48, 32, 24, 16, 12]` if within 8% relative error; returns the architectural label (e.g. `1/4" = 1'`). Otherwise returns the raw `1:N` ratio.

### `shiftElement`, `shiftScaleResponse`, `shiftBBox`

Translate coordinates by `(dx, dy)`. Used in an earlier debug build where elements had to be re-anchored into a cropped preprocessed-SVG coordinate space. The current viewer renders the original PDF and these are not called. Keep them or strip them.

## The `rect_curve.corners` workaround

The backend ships `corners` with a vertical mirror about the page mid-line — see [`backend.md`](backend.md#the-rect_curvecorners-double-y-flip) for the root cause.

The viewer never renders the polygon from `corners`. `MeasurementItem.hitShape` and `ElementOverlay`'s rect path both use the axis-aligned bbox (`x0/top/x1/bottom`), which comes from a separate pdfplumber attribute and is correct.

`rectSideLengthsPts` uses `Math.hypot(corners[0]-corners[1])` and `Math.hypot(corners[1]-corners[2])`, which are distances. Distances are invariant under reflection, so the side lengths returned for rotated rectangles are still correct even though the corners themselves are mirrored.

A comment in `viewer.tsx` near `hitShape` calls this out. If you fix the bug in `extractor.py`, remove the comment.

## Why `next/dynamic` with `ssr: false`?

`pdfjs-dist` (used by `react-pdf`) sets `pdfjs.GlobalWorkerOptions.workerSrc` at module evaluation time and reads `window` / `Worker`. Importing it during SSR throws. Both `Viewer` and `Cropper` mount via `dynamic(..., { ssr: false })` so their bundles execute only in the browser.

This is the right pattern for this app and is **not** something to "improve" toward Server Components or `use cache` — the interactive viewer needs the worker, the wheel handler, the canvas, and the cropper-region state in the browser. There is no server state to render.
