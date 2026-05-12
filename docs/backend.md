# Backend

FastAPI service at `implementation/backend/`. Three modules of substance (`main.py`, `extractor.py`, `scale_detector.py`) plus a legacy `preprocess.py` whose endpoint is no longer called.

## Modules

### `main.py`

FastAPI app definition, request/response models, and four routes.

- CORS is locked to `http://localhost:3000`. Change `allow_origins` to deploy elsewhere.
- `MAX_UPLOAD_BYTES = 25 * 1024 * 1024` enforced on every upload.
- All upload routes verify magic bytes (`data.startswith(b"%PDF")`) instead of trusting the multipart `Content-Type` — clients can lie about it.
- Pydantic `Element` is permissive (`model_config = {"extra": "allow"}`) so backend can add fields like `points` and `corners` without versioning the response model.

#### Routes

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness probe. Returns `{"ok": true}`. |
| `POST` | `/api/extract` | Extract elements; optional per-page crop. |
| `POST` | `/api/preprocess` | Legacy SVG with non-black elements stripped. Still wired up; not called by current frontend. |
| `POST` | `/api/detect-scale` | OCR callouts and infer drawing scale for one crop region. |

#### `POST /api/extract`

Multipart form:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `file` | file (`%PDF`) | yes | ≤25 MB |
| `crop` | string | no | JSON array of `{"page": int, "x0": float, "top": float, "x1": float, "bottom": float}`. If omitted, every page is returned uncropped. |

Response (`ExtractResponse`):

```json
{
  "filename": "plan.pdf",
  "page_count": 6,
  "pages": [
    {
      "page_number": 2,
      "width": 1224.0,
      "height": 792.0,
      "elements": [
        {"id": "rect#0007", "type": "rect", "x0": 100.2, "top": 200.1, "x1": 145.0, "bottom": 218.4, "fill": null, "stroke": "#000000"},
        {"id": "rect_curve#0003", "type": "rect_curve", "x0": 300.0, "top": 400.0, "x1": 360.0, "bottom": 420.0, "points": [[...]], "corners": [[...]], "stroke": "#000000", "fill": null}
      ]
    }
  ]
}
```

Element types: `line`, `rect`, `rect_curve`, `rect_partial`, `curve`, `char`, `word`. The `inferred_rect` type is declared in the `Literal` union but never emitted (the partial-pairing pass is disabled — too many false matches).

Errors:
- `400` invalid PDF / bad crop JSON / negative-size crop.
- `413` file too large.

#### `POST /api/detect-scale`

Multipart form:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `file` | file (`%PDF`) | yes | ≤25 MB |
| `page_number` | int | yes | 1-based |
| `crop` | string | yes | JSON object `{x0, top, x1, bottom}` |
| `black_threshold` | float | no | `[0,1]`, default `0.05`. Frontend overrides to `0.02`. |

Response (`ScaleResponse`):

```json
{
  "page_number": 2,
  "dpi": 1200,
  "callout_count": 4,
  "drawing_scale_pts_per_inch": 1.5,
  "callouts": [
    {
      "id": "callout#0001",
      "text": "14\"ø",
      "diameter_in": 14.0,
      "raw_text": "14\"ø",
      "confidence": 87,
      "bbox":          {"x0": 410.1, "top": 250.8, "x1": 442.6, "bottom": 261.2},
      "enclosing_rect":{"x0": 408.5, "top": 248.0, "x1": 446.0, "bottom": 264.0},
      "duct_bbox":     {"x0": 100.0, "top": 240.0, "x1": 400.0, "bottom": 270.0},
      "drawn_diameter_pts": 21.0,
      "scale_pts_per_inch": 1.5,
      "wall_pairs": [
        {"a": {...}, "b": {...}, "distance_pts": 21.0}
      ]
    }
  ]
}
```

`drawing_scale_pts_per_inch` is `null` when zero callouts decoded **or** when every decoded callout failed geometry inference. The frontend treats this as a pipeline failure.

`enclosing_rect` is the callout's own bordered box (the candidate box that passed `_filter_callout_candidate_boxes` and was successfully OCR'd). `duct_bbox` is the rectangle the algorithm associated with the callout — typically the duct itself.

Errors:
- `400` invalid PDF, bad crop, page out of range, `black_threshold` not in `[0,1]`, or any downstream `ValueError`.
- `413` file too large.

#### `POST /api/preprocess` (legacy)

Returns an SVG of the cropped page with every non-black drawing element removed. The viewer used to display this in an earlier debug build; the current viewer renders the original PDF via react-pdf instead. Endpoint still works; not called by the current frontend.

### `extractor.py`

pdfplumber-driven element extraction.

`extract_pdf(data, filename, crops)` opens the PDF with pdfplumber, optionally filters to the pages named in `crops`, and calls `_extract_page` per page.

`_extract_page(page, page_number, crop)` produces the element list per page:

1. **Lines** — `page.lines` → `{"type": "line", x0/top/x1/bottom, linewidth, stroke}`. Stroke colour falls back from `stroke_color` to `non_stroking_color` (pdfplumber's two different keys for the same concept depending on the PDF).
2. **Rects** — `page.rects` → `{"type": "rect", ..., fill, stroke}`.
3. **Curves** — `page.curves` are bucketed:
   - If `_is_rectlike_curve(c)` (defined in `scale_detector.py`) → `rect_curve`, with both `points` (the raw path) and `corners` (the four rectangle vertices) attached.
   - Else if `_is_rect_partial(c)` → `rect_partial` with `points` and the partial's bbox.
   - Else → `curve` with `points`.
4. **Chars** — `page.chars` → `{"type": "char", text, fontname, size, fill}`.
5. **Words** — `page.extract_words()` → `{"type": "word", text}`.

If `crop` is provided, the element list is filtered to those whose bbox intersects the crop (`_intersects`).

#### Colour conversion (`_color_to_hex`)

pdfplumber returns colour tuples as 1-, 3-, or 4-floats in `[0,1]`. The 4-tuple is **CMYK**, not RGBA — `_color_to_hex` converts CMYK to RGB using the subtractive-mix approximation `(1-c)*(1-k)`. An earlier version returned `None` for 4-tuples, which mis-tagged CMYK-black callouts (common in CAD output) as colourless. The parity comment in `_color_to_hex` exists because the frontend filter then rejected those rects via the colour threshold — keep this in sync with `scale_detector._is_black`.

#### Dead code

`_visible_drawing_bboxes` and `_bbox_overlaps_any` are defined but never called. They were an attempt to cross-check pdfplumber's element list against PyMuPDF's `get_drawings()` output to drop entries on hidden PDF layers (Optional Content Groups), which pdfplumber ignores. The cross-check is not invoked anywhere in `extract_pdf`. Leave them or strip them as you like.

#### The `rect_curve.corners` double-Y-flip

A pre-existing bug. `rect_corners_from_curve` is called from `extractor.py` with `c` whose `pts` come from `page.curves` — pdfplumber **has already converted those points to top-left origin** before returning. `rect_corners_from_curve` operates as if they're still in bottom-left space (it derives `_has_axis_rectangle_in_path`, builds a convex hull, returns four `(x, y)` corners). The extractor then applies `[x, page_h - y]` to those returned corners a second time. The result mirrors them vertically about the page mid-line.

The `points` field on `rect_curve` and `curve` is similarly flipped (the extractor does `page_h - y` on raw `pts`). For `points` this matches the rest of the response (top-left); for `corners` it's the second flip.

Side lengths (`Math.hypot` on consecutive corners in the frontend) and bounding boxes (which come from pdfplumber's separate `x0/top/x1/bottom` attribute) are unaffected. The viewer renders bboxes, not the polygon from `corners`, which is why the bug doesn't surface visually.

### `scale_detector.py`

The hard part of the system. The public entry point is `detect_scale_callouts`; everything else is helpers — but `_is_rectlike_curve`, `_is_rect_partial`, and `rect_corners_from_curve` are also imported by `extractor.py`.

#### Key constants

| Constant | Value | What it does |
| --- | --- | --- |
| `_O_SLASH_SUBSTITUTES` | `"@°ØøOoDd6¢"` | Tesseract glyph substitutions for `Ø`. The regex character class accepts **one or more** of these so split reads (`°@`) are handled. |
| `_CALLOUT_RE` | `^\s*(\d+(?:\.\d+)?)\s*(?:"|″)?\s*([{substitutes}]+)\s*$` | Numeric diameter, optional inch mark, one-or-more `Ø`-substitute glyphs. |
| `_BOX_OCR_CONFIG` | `'--oem 1 --psm 7'` | LSTM-only engine, "treat as single text line" segmenter. No whitelist (a whitelist that excluded `"` dropped `14"@` from 76% to 0% confidence; including `"` breaks `pytesseract`'s shlex split). |
| `_BOX_OCR_DPI` | `1200` | Per-callout render DPI. A 40×16 pt callout becomes ~667×267 px — crisp glyphs vs. the aliased output from 600 DPI full-crop OCR. |
| `_BOX_INSET_PT` | `1.0` | Pixels to trim from every side before OCR so the box's own border rule doesn't OCR as a vertical bar and corrupt the leading digit. |
| `_MIN_CONF` | `20` | Tesseract confidence floor. Low because the Ø substitution is itself a miss-read and the regex / diameter range / geometry / band-mean downstream gates do the real filtering. Random gibberish still scores below 20. |
| `_MIN_DIAMETER_IN`, `_MAX_DIAMETER_IN` | `2.0`, `80.0` | Rejects stray year numbers (`2024@`) and page footers that happen to match the regex. |
| `_MIN_CALLOUT_WIDTH_PT`, `_MAX_CALLOUT_WIDTH_PT` | `15`, `80` | Callout box width range. Below 15 pt is glyph fragments; above 80 pt is duct rectangles. |
| `_MIN_CALLOUT_HEIGHT_PT`, `_MAX_CALLOUT_HEIGHT_PT` | `8`, `22` | Callout box height range. Matches one line of ~10pt text. |
| `_MIN_CALLOUT_ASPECT` | `1.4` | `max(w/h, h/w)` — callouts are elongated, duct rectangles closer to square or much more elongated. |
| `_GEOMETRY_SEARCH_RADIUS` | `120.0` pt | Max centre-to-centre distance for the NEAR fallback in geometry inference. ~1.7 inches at 1:1. |
| `_MIN_SCALE_PTS_PER_IN`, `_MAX_SCALE_PTS_PER_IN` | `0.3`, `5.0` | Plausible HVAC drawing scales. `5 pts/in ≈ 1:14` (full-floor plans rarely go larger); `0.3 ≈ 1:240`. Anything outside means the duct rectangle the algorithm picked can't be right for the declared diameter. |
| `_SCALE_BAND_PCT` | `0.10` | ±10% of median for the band-mean aggregation. Real callouts on one drawing share an exact scale; outliers are extreme (wrong wall pair), not Gaussian. |

#### Pipeline (`detect_scale_callouts`)

```
crop_bbox + black_threshold
  → _load_page_lines_and_rects (vector pass, black-only)
      → all black rects in the crop (page.rects ∪ rect-like curves)
  → _filter_callout_candidate_boxes
      → callout-sized, elongated subset
  → _dedupe_boxes
      → drop duplicates from CAD exporters that emit re+4-line for the same rect
  → for each candidate: _ocr_callout_box
      → 1200 DPI render of (box ⊖ inset)
      → _strip_non_black → pure black/white
      → pytesseract image_to_data with PSM 7
      → _normalise_callout on joined run, then per-token fallback
      → reject if min token conf < _MIN_CONF
      → return (canonical text, diameter_in, conf, tight text bbox)
  → for each decoded callout: _infer_geometry
      → find associated/nearby rectangle, derive scale
  → _aggregate_scale across all per-callout scales (band-mean)
```

#### Vector candidate pass — `_load_page_lines_and_rects`

Returns black-stroked lines (unused downstream — kept for potential reuse) and rectangles inside the crop. Rectangles come from two sources merged:

1. `page.rects` — true `re` operator.
2. `page.curves` filtered through `_is_rectlike_curve` — CAD exporters frequently emit a 4-line path instead of `re`.

A rect is "black" if either its stroke or its fill is black per `_is_black` (which mirrors `_strip_non_black` so vector and raster filtering agree). `_is_black` treats `None` as black because pdfplumber omits the colour key when the PDF leaves it at the DeviceGray default.

#### OCR pass — `_ocr_callout_box`

1. Inset the box by `_BOX_INSET_PT` on every side.
2. Render via pypdfium2 at 1200 DPI.
3. Threshold to pure black/white using `_strip_non_black` (RGB → L → point-cutoff → RGB).
4. `pytesseract.image_to_data` with `--oem 1 --psm 7`.
5. Two-pass regex match:
   - Join all tokens into one string, try `_normalise_callout` on it. Handles `8` `"` `Ø` returned as three tokens.
   - Then try each token individually. Handles `8"Ø` returned as one token.
6. Reject if `min(conf)` across tokens is below `_MIN_CONF`.
7. Convert the tight token-bbox back to PDF points (`px_to_pt = 72 / 1200`).

#### Geometry inference — `_infer_geometry`

For each callout `c`:

1. Set `expansion = max(callout_width, callout_height)`. With a typical 30×15 pt callout, that's 30 pt — enough to admit ducts up to ~30 pt away from the callout's bbox.
2. Compute `min_drawn = 0.3 * diameter_in` and `max_drawn = min(120, 5.0 * diameter_in)`. Rectangles whose **shorter side** falls outside this range are skipped — they can't possibly represent the declared diameter at any plausible scale.
3. Partition rectangles (excluding the callout's own enclosing rect) into two pools:
   - **ASSOCIATED** — those whose bbox, expanded by `expansion` in each direction, contains the callout's centre.
   - **NEAR** — those whose centre is within `_GEOMETRY_SEARCH_RADIUS` (120 pt) of the callout's centre.
4. If ASSOCIATED is non-empty, pick the **largest-area** rectangle in it. Real ducts are big, long rectangles; the false positives we want to avoid near a callout — dimension stubs, arrowhead boxes, leader marks — are small. Largest-area is what disambiguates.
5. Else, fall back to NEAR: the closest rect, ties broken by larger area.
6. Read `drawn_pts = min(width, height)` of the chosen rect — its shorter side. That's the on-paper diameter.
7. `scale = drawn_pts / diameter_in`. Returned alongside the duct bbox and a synthetic wall pair (`_wall_pair_from_rect`).

`_wall_pair_from_rect` synthesises the two long edges of the duct rectangle so the viewer can overlay a measurement line showing which rect the algorithm chose for this callout. For a horizontal duct (width ≥ height) the pair is the top and bottom edges; for vertical, the left and right edges.

#### Aggregation — `_aggregate_scale`

1. `med = median(scales)`.
2. `lo, hi = med * (1 ± 0.10)`.
3. `kept = [v for v in scales if lo ≤ v ≤ hi]`.
4. Return `mean(kept)` if non-empty, else `median(scales)`. The fallback is defensive — the median is always inside its own ±10% band so `kept` is never empty unless `scales` itself is.

Outlier-rejection by band-then-mean works here because real callouts share an **exact** scale (they're all sized off the same drawing). Bad reads (callout matched to the wrong rectangle) miss the band by a wide margin.

#### Rectangle classifiers (`_is_rectlike_curve`, `_is_rect_partial`, `rect_corners_from_curve`)

Exported and used by `extractor.py` too. They live in `scale_detector.py` because the detector needs them too and the cycle would otherwise go the wrong way.

`_is_rectlike_curve` accepts:
- Axis-aligned rectangles (with redundant moveto's, or with interior strokes like an X marker — `_has_axis_rectangle_in_path` requires ≥4 axis-aligned corner-to-corner segments, which rejects a lone X).
- Rotated rectangles via convex hull: exactly 4 hull vertices satisfying the equal-diagonal / shared-midpoint conditions. It does **not** require the path to trace the hull sides — CAD exporters often emit each side via a `c` (cubic) operator whose control points break a strict consecutive-pair check.

`_is_rect_partial` is strict — 3-segment axis-aligned U-shape with arm length ≥ 2× cap length and bbox ≥ 6×6 pt. The previous looser version let leader-line arrows and glyph fragments slip through.

`rect_corners_from_curve` returns:
- For axis-aligned paths, the four bbox corners.
- For rotated, the four hull vertices ordered CCW by `atan2` from the centroid.
- `None` if the curve isn't a rectangle.

These corners ship to the frontend in `rect_curve.corners` but **double-Y-flipped** due to the bug above.

### `preprocess.py` (legacy)

`build_preprocessed_svg(pdf_bytes, page_number, crop_bbox, black_threshold)` renders the cropped page to SVG via PyMuPDF (`page.get_svg_image(matrix=Matrix(1,1), text_as_path=False)`), then walks the tree dropping every element whose fill and stroke are both non-black. The SVG `viewBox` is rewritten to the crop region and `width`/`height` stripped so the host CSS box controls sizing.

Still wired to `POST /api/preprocess`. The current frontend doesn't call it — the viewer renders the original PDF via react-pdf instead. Keep or delete as appropriate; deletion would also let you remove the PyMuPDF dependency from `preprocess.py` (but `extractor.py` would still need it for `fitz` even though its only `fitz` use is dead code — see above).
