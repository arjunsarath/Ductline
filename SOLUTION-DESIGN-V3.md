# HVAC Duct Detection & Annotation — Solution Design V3 (Pivot)

> **Status: Superseded by V4** (2026-05-06). V3 is retained as the colour-driven
> fallback path; V4 (`SOLUTION-DESIGN-V4.md`) is the active outline-based
> pipeline. See `adr/0011-v3-pivot-rationale.md` and
> `adr/0014-v4-scope-and-assumptions.md`.

> **Status:** Draft, build-in-progress. V3 is a deliberate scope pivot away
> from V1/V2's general detection pipeline toward a narrower, deterministic
> color-driven MVP grounded by a successful spike on `03-caddsultants-shop.pdf`.
> **Author:** Arjun Sarath
> **Date:** 2026-05-04
> **Builds on:** [`SOLUTION-DESIGN-V2.md`](./SOLUTION-DESIGN-V2.md)
> **Spike artefacts:** `/tmp/spike-v3/` — see §11
> **Companion artefacts:** [`PRD.md`](./PRD.md), [`adr/`](./adr/)

---

## 1. Why V3 exists

V2 introduced tiled detection, a reviewer loop, page categorization, and
legend parsing — all to defend the VLM detection step against the failure
modes V1 surfaced (G1–G10 in V2 §2.2). It works, but the architecture is
heavy and the failure surface is wide: every stage that *could* fail
*does* fail on at least one drawing in the benchmark, so the pipeline
spends most of its budget on degradation handling.

The pivot in V3 is structural, not incremental:

> **The user already knows which lines on the drawing are ducts —
> they're color-coded.** Asking the VLM to *find* ducts on a colored
> drawing is asking it to redo work the engineer already did with their
> pen. We ask the user to point at the colors and label them, then run
> a deterministic CV pipeline against that input.

This collapses the detection problem from *"infer duct location from a
multi-trade drawing"* to *"mask color X, segment, OCR dim text inside
the mask, derive scale, output."* No VLM. No reviewer. No legend parser.
No tiling.

The trade-off is explicit: V3 only works on **color-coded MEP drawings**.
Plain monochrome construction documents (V1 corpus drawings 02, 04, 05)
are out of scope until phase 2, and a hybrid color-overlay-for-system /
black-geometry-for-width pipeline lands.

## 2. What this pivot buys and what it costs

### 2.1 Buys

| | |
|---|---|
| **Determinism** | Detection is HSV `inRange` + morphology. No model in the loop until OCR. |
| **Speed** | ~5–10× faster than V2 — no VLM rounds, no reviewer iterations. |
| **Honest confidence** | Calibration cross-validates extracted dims against measured pixels. The system reports which segments have OCR-confirmed dims vs. derived-from-pixel dims. |
| **Tractable per-segment attribution** | The hardest unsolved problem in V1/V2 (matching dim text to the right duct) becomes geometric: *is the token bbox inside the colored mask?* On Pattern B drawings this works at 88% accuracy in the spike. |
| **Correct posture** | Algorithmic-first / workflow-second / no agents-without-tools. V3 has no agents. |

### 2.2 Costs

| | |
|---|---|
| **Narrower addressable surface** | Drawings 02, 04, 05 in the V1 corpus produce no result until phase 2. |
| **Picker workflow added** | User must pick + label system colors before the pipeline runs. UX overhead. |
| **Pattern A (parallel walls) still in flight** | Drawing 01's coloring style identifies systems but width measurement needs the underlying black geometry. Designed but not validated. See §6.5. |
| **Centerline mode (Pattern C) needs higher-res source** | Validation blocked on Techjay providing a non-thumbnail render. Designed. See §6.6. |

## 3. Scope

### 3.1 In scope (V3 ship)

- **Pattern B pipeline (closed colored outline → flood-fill → in-mask token filter)** — validated end-to-end on `03-caddsultants-shop.pdf` with 88% attribution accuracy. This is the demo path.
- **Color picker UI** — user clicks colors on the rendered page, labels each ("Supply Air", "Return Air", "Exhaust", …), picks the pattern (outline / centerline) per color. SMACNA hue convention pre-fills suggested labels.
- **Adaptive-DPI render** — re-render the source at the resolution that makes the smallest dim token ≥ 24 px tall before OCR.
- **Token classification** — `dim_rect` (`AxB`), `dim_round` (`A"Ø`), `flow` (CFM | L/s). Imperial and metric simultaneously.
- **Page-unit detection** — count CFM vs L/s tokens, majority wins.
- **Histogram-of-candidates `ppu` calibration** — each `dim_rect` token contributes two candidate `pixels_per_unit` values; dominant histogram bin wins.
- **Plan-visible side disambiguation** — pick whichever of (A, B) gives `ppu` closer to the global value.
- **Pressure class** — SMACNA tier from CFM/L/s + dimensions if available; size-only heuristic with explicit `"estimated"` flag if not.
- **Result rendering** — overlay color masks + dimension labels + system legend + per-segment popover.

### 3.2 Out of scope (V3)

Carried forward from V1/V2's deferrals **and additionally**:

- **Pattern A drawings (drawing 01-style parallel colored walls)** — color masking identifies systems but width measurement needs the underlying black geometry. Phase 2.
- **Pattern C drawings (06-techjay-style colored centerline through black duct)** — designed; validation blocked on higher-res source.
- **Monochrome MEP drawings (drawings 02, 04, 05)** — phase 2.
- **VLM-anything**. V3 does not call any LLM.
- **Reviewer loop, page categorizer, legend parser, tiling**. All from V2 are retired.
- **Multi-page PDFs**. V3 carries forward the V1 single-page constraint.

### 3.3 V3 vs V2 — the diff

| Stage | V2 | V3 |
|---|---|---|
| Ingest | ✅ | **kept** — `IngestStage` reused as-is |
| Probe OCR + rotation | ✅ | **kept** — `ProbeOCRStage` (plus its rotation resolver) reused |
| Page categorize | VLM | **dropped** |
| Legend parse | VLM | **replaced** by user color-pick |
| Quality | OCR-driven | **kept**, simplified |
| Region detect | VLM | **dropped** (no longer needed — color mask replaces region) |
| Tiled detection | VLM (per-tile, neighbour context) | **dropped** |
| Text extraction | per-segment OCR | **replaced** by full-page OCR + regex classify |
| Pressure class | 4-tier ranked policy | **kept**, simplified — schedule lookup + size fallback |
| Reviewer | VLM, iterated | **dropped** |
| Assemble | ✅ | **adapted** for the new segment structure |

V1/V2 modules retired in V3 stay in the repo for reference; V3 introduces
its own runner that composes only the surviving and new stages.

---

## 4. Architecture

### 4.1 Pipeline (V3)

```
1. Ingest
2. Rotation fix + DPI probe   (ProbeOCRStage from V2 — reused unchanged)
3. Render-for-OCR             (NEW — adaptive DPI, tile + overlap if oversize)
4. Color pick                 (NEW — UI input or config-driven for batch)
5. Color mask + segment       (NEW — HSV inRange + mode-specific morphology)
6. OCR + classify             (NEW — full-page OCR, regex token classify)
7. Attribute                  (NEW — in-mask filter for Pattern B)
8. Calibrate                  (NEW — histogram-of-candidates ppu)
9. Derive + cross-validate    (NEW — width-from-pixels for unmatched segs)
10. Pressure class            (NEW — flow-based, size-fallback)
11. Assemble + render         (adapted from V2)
```

Stages 4–10 are deterministic. None call an LLM.

### 4.2 Module layout

```
app/
  pipeline/
    v3/
      __init__.py
      config.py         — V3PipelineConfig (colors, modes, defaults)
      color_mask.py     — HSV inRange, fill_pattern_b, skeletonize
      ocr_classify.py   — full-page OCR, regex grammar, page-unit detection
      attribute.py      — in-mask filter, nearest-skel, width measurement
      calibrate.py      — histogram-of-candidates ppu, side disambiguation
      pressure.py       — SMACNA tier, size fallback
      assemble.py       — V3DrawingResult builder
      runner.py         — V3DetectionPipeline composing stages
  schemas_v3.py         — V3-specific frozen models (System, V3Segment, …)
scripts/
  run_v3.py             — CLI: pdf → V3 result JSON
```

Existing V1/V2 code is untouched. The runner composition is independent.

---

## 5. Stage details

### 5.1 Ingest (reused from V1)

`app/pipeline/ingest.py` — unchanged. Validates file type, single-page PDF,
size limits. Builds a `DrawingSource` (vector_pdf or raster).

### 5.2 Probe OCR + rotation (reused from V2)

`app/pipeline/probe_ocr.py` — unchanged. Computes the smallest 5th-percentile
text-character height in pixels at probe DPI; resolves rotation. The output
that V3 needs is `ctx.source.rotation_applied` (so the canvas the user picks
colors on is upright) and the smallest-text-height (so V3 can pick its own
adaptive render DPI in §5.3).

### 5.3 Render-for-OCR (new)

```
target_text_height_px = 24
scale = target_text_height_px / smallest_text_height_at_probe_dpi
target_dpi = clamp(probe_dpi * scale, 200, 600)
re-render page at target_dpi
if long_edge > 3500: tile with 200 px overlap, OCR per tile, dedupe by IoU
```

For vector PDFs this gives perfect-quality up-rendering for free. For
raster sources, `cv2.resize(INTER_CUBIC)` + unsharp mask is the V3
implementation. A `preprocess_for_ocr(tile) -> tile` seam is declared so
phase 2 can swap in EDSR / LapSRN super-resolution.

**Minimum source resolution.** Raster sources with long edge < 3000 px
are flagged and the pipeline emits a clean error: *"Source resolution
too low for dimension extraction. Provide a vector PDF or a higher-
resolution scan."* This rule was added after the 06-techjay spike showed
that no upscale strategy can recover dim glyphs that aren't present in
the source pixel data.

### 5.4 Color pick (new)

#### Inputs
- `picks: list[ColorPick]` where each pick is:
  ```
  ColorPick {
    label: str               # "Supply Air"
    h_lo: int, s_lo: int, v_lo: int
    h_hi: int, s_hi: int, v_hi: int
    second_range: HSVRange | None   # for hue-wraparound colors (red)
    pattern: "outline" | "centerline"
    system_kind: "supply" | "return" | "exhaust" | "outside" | "other"
  }
  ```

#### UX flow (frontend)
1. The page is rendered at the adaptive DPI.
2. User clicks a colored region. Frontend reads the BGR triplet and
   converts to HSV; expands by `(±10, ±50, ±50)` to get a tolerance
   band. The band is editable.
3. User assigns a **label** from a dropdown pre-filled by SMACNA hue
   convention (blue → supply, red → return/exhaust, green → return/OA,
   yellow → exhaust, …).
4. User picks the **pattern** (outline / centerline).
5. Repeat for each system color. Submit.

#### Batch / non-UI path
The CLI accepts a YAML config:
```yaml
picks:
  - label: "Supply Air"
    pattern: outline
    h_lo: 100; h_hi: 130; s_lo: 80; s_hi: 255; v_lo: 80; v_hi: 255
    system_kind: supply
```

### 5.5 Color mask + segment (new)

For each `pick` with `pattern == "outline"` (Pattern B):

```
mask    = cv2.inRange(hsv, lo, hi)   (+ second_range for red wraparound)
closed  = cv2.morphologyEx(mask, MORPH_CLOSE, RECT(11×11))
flood   = cv2.floodFill(closed, (0,0), 255)        # exterior → 255
interior= cv2.bitwise_not(flood)
filled  = closed | interior
skel    = skimage.morphology.skeletonize(filled)
dt      = cv2.distanceTransform(filled, DIST_L2, 5)
```

Per-segment graph splitting at junctions (skeleton degree > 2) is **deferred**
to a follow-up — the spike showed `filled` is one large connected
network per system, but the histogram-of-candidates calibration is
robust to this. Per-segment IDs in the V3 result are derived from
connected components; junction-split refinement is tracked as future work.

### 5.6 OCR + classify (new)

Full-page OCR via the existing `RapidOCRExtractor` (or `PaddleOCRExtractor`
when available — the protocol is interchangeable). Tile + overlap when
long edge > 3500 px; dedupe tokens by `iou(box_a, box_b) > 0.5` and
text equality.

Regex grammar (from spike `03b_reclassify.py`):

```python
DIM_RECT  = r'(?<!\d)(\d{2,4})\s*[xX×]\s*(\d{2,4})(?!\d)'
DIM_ROUND = r'(?<!\d)(\d{1,4})\s*["”]?\s*[øØ⌀∅]'
CFM       = r'(?<!\d)(\d{1,5})\s*CFM'
LPS       = r'(?<!\d)(\d{1,5})\s*L/?S'
```

Bounds: imperial pairs `4 ≤ a, b ≤ 144`; metric pairs `50 ≤ a, b ≤ 2400` and
both divisible by 5. Tokens passing both bounds carry both `units_candidate`s
to be resolved at page-unit detection time.

**Page unit detection:** `count(CFM) >= count(L/s) → "in"` else `"mm"`.
Trivial; rock-solid in the spike (drawing 01: 79 L/s, 0 CFM → mm;
drawing 03: 41 CFM, 0 L/s → in).

### 5.7 Attribute (new — Pattern B)

```python
for tok in rect_tokens:
    cx, cy = bbox_center(tok)
    if filled[cy, cx] == 0:
        skip   # token outside any duct mask
    if skel[cy, cx]:
        nx, ny = cx, cy
    else:
        nx, ny = nearest_skel_pixel(skel, cx, cy, search_r=80)
    radius_px = dt[ny, nx]
    if radius_px < 4: skip
    width_px = 2 * radius_px
    record (tok, system, width_px)
```

The "token bbox center inside `filled`" filter eliminates equipment
labels (CD-1, RG-1, TG-3) automatically without a hardcoded suffix list.
This is the single most important V3 insight from the spike — it's the
clean rule v1/v2's `nearest text` heuristic had been missing.

#### Pattern A and Centerline attribution (designed, not yet validated)

- **Pattern A (parallel walls):** the in-mask rule fails because labels
  sit *outside* the walls. Phase 2 rule:
  > Token belongs to system S if the nearest masked pixel within K
  > (~ max-duct-width / 2) px is system S, AND no other system's mask
  > is closer.
  Width measurement: detect the underlying black duct walls (Canny / threshold
  on grayscale) and ray-cast perpendicular from the centerline.
- **Centerline (Pattern C):** mask the colored centerline; for each token,
  proximity-attribute to nearest centerline pixel; estimate local skeleton
  tangent via PCA on a 12 px window; ray-cast perpendicular into the
  grayscale until first dark-wall pixel on each side; sum = duct width.
  Spike code in `/tmp/spike-v3/11_techjay_centerline.py`.

### 5.8 Calibrate (new)

For each `(token, width_px)` pair, push two candidates into a list:

```
candidates += [width_px / token.a, width_px / token.b]
```

Histogram with adaptive bin width (Freedman-Diaconis). Take the dominant
bin's neighbours (peak ± 1 bin) and emit `ppu = median(in_band)`.

**Per-token visible-side disambiguation:**

```
a_ppu, b_ppu = width_px / a, width_px / b
visible = a if abs(a_ppu - ppu) <= abs(b_ppu - ppu) else b
hidden  = the other
```

Cross-validation: any pair with `|chosen_ppu / ppu - 1| > 15%` is flagged
as low-confidence — it's still emitted but the segment popover surfaces
the discrepancy. Spike: 22/25 pairs (88%) within ±15% on drawing 03.

### 5.9 Derive + cross-validate (new)

For segments without an OCR token in their mask:

```
derived_width_in = (segment.median_pixel_width / ppu)
snap to nearest standard SMACNA increment (2", 4", 6", … in imperial; 50, 100, 150, … mm metric)
mark dimension.source = "derived:pixel-measure"
mark dimension.confidence = "medium"
```

For segments WITH an OCR token, the OCR'd value is authoritative; the
derived value is a sanity check.

### 5.10 Pressure class (new)

```
def pressure_class(width_in, height_in, flow_value, flow_unit):
    if flow_value is not None:
        velocity_fpm = flow_to_fpm(flow_value, flow_unit, width_in, height_in)
        if velocity_fpm < 2000:  return ("LOW", "extracted", confidence="high")
        if velocity_fpm < 4000:  return ("MEDIUM", "extracted", confidence="high")
        return ("HIGH", "extracted", confidence="high")
    else:
        # Size-only heuristic — engineering-dishonest if reported as fact.
        # Always emit with source="estimated:size_only" and confidence="low"
        # so the UI can surface the disclaimer.
        perimeter = 2 * (width_in + height_in)
        if perimeter < 60:  return ("LOW", "estimated:size_only", confidence="low")
        if perimeter < 120: return ("MEDIUM", "estimated:size_only", confidence="low")
        return ("HIGH", "estimated:size_only", confidence="low")
```

The `flow_to_fpm` helper handles both CFM (imperial) and L/s (metric);
metric path converts to ft³/min internally. Default duct material is
galvanized steel; user can override per-segment in the UI.

Flow tokens are attributed to segments by the same in-mask rule as
dim tokens — strict containment, not proximity. The reason is
load-bearing: in real MEP drawings, CFM is most often labelled at
*diffuser/terminal* positions outside the colored duct outline, and
the main duct's true flow is the *sum* of downstream diffuser CFMs.
A nearest-flow proximity rule would attach a 100-CFM diffuser flow
to a 24×17 main duct that actually carries 4 × 100 = 400 CFM, and
quietly under-classify it as low pressure. We refuse that mistake:

  • If a CFM/L-s token sits inside a duct's outline → ``source="extracted"``
    pressure class with high confidence (the engineer wrote that flow on
    that duct intentionally).
  • If not → ``source="estimated:size_only"`` with low confidence and an
    explicit popover disclaimer ("Pressure class estimated from size —
    no CFM/L/s extracted on this segment. User override available.").

Drawing 03's empirical result confirms this: **0 of 41 CFM tokens
sat inside a duct outline** — the engineer placed every CFM at a
diffuser face. All 25 segments fell back to size-only, which is the
correct, honest output for V3. Phase-2 work (V3 §10) adds duct
topology + downstream CFM aggregation so a main duct's flow is the
sum of its descendants' flows.

### 5.11 Assemble + render (adapted)

V3 emits a `V3DrawingResult` with the same top-level shape as V1's
`DrawingResult` plus:

- `systems: list[System]` — labelled colors, picker-supplied
- `ppu: float | None` — calibrated pixels-per-unit (None if calibration failed)
- `page_unit: "in" | "mm"`
- per-segment `system_id: str` (which `System` it belongs to)
- per-segment `dimension.source` extended with `"derived:pixel-measure"`
- per-segment `pressure_class.source` extended with `"estimated:size_only"`

Rendering: color mask overlay at α=0.4 in the system's display color;
dim label at segment centroid; popover lists OCR-attributed vs. derived
flag, calibration confidence, pressure class source.

---

## 6. Pattern catalogue

Three patterns observed across the corpus — V3 ships only Pattern B.

### 6.1 Pattern A — solid colored walls (no underlying black outline)

> **Example:** drawing 01 (cyan/green/red strips that *are* the duct walls)
> **Status:** designed §5.7; **not in V3 ship**

Two parallel colored lines form the duct sides; the interior is the
white space between them. Dim labels live alongside or between the
walls. The "in-mask" filter doesn't apply because labels are outside
the thin colored strips. Phase 2 rule + ray-cast width measurement
documented above.

### 6.2 Pattern B — closed colored outline around the duct

> **Example:** drawing 03 (blue rectangles around each duct run)
> **Status:** **shipped in V3**, validated in spike at 88% attribution

The colored outline forms a closed loop that flood-fill captures
cleanly. Dim labels sit inside the outline. This is the easy mode and
the V3 demo path.

### 6.3 Pattern C — colored centerline through black-outlined duct

> **Example:** 06-techjay (small bitmap; full validation pending higher-res source)
> **Status:** designed §5.7; **not in V3 ship**

A colored line runs down the middle of an otherwise black-outlined duct.
Centerline mask + ray-cast for width. Designed; unvalidated due to
06-techjay being a 621×308 thumbnail with no OCR-recoverable dim text.

---

## 7. Result schema (V3)

```python
class System(_Frozen):
    id: str                    # e.g. "sys_supply_blue"
    label: str                 # "Supply Air"
    display_color: tuple[int, int, int]
    hsv_lo: tuple[int, int, int]
    hsv_hi: tuple[int, int, int]
    pattern: Literal["outline", "centerline"]
    kind: Literal["supply", "return", "exhaust", "outside", "other"]


class V3Dimension(_Frozen):
    value: str
    shape: Literal["round", "rectangular"]
    confidence: Literal["high", "medium", "low"]
    source: Literal[
        "ocr:in_mask",            # OCR'd dim, token inside mask
        "ocr:nearest_outside",    # OCR'd dim, Pattern A nearest rule (phase 2)
        "derived:pixel-measure",  # measured from pixels via global ppu
    ]


class V3PressureClass(_Frozen):
    value: Literal["LOW", "MEDIUM", "HIGH"]
    confidence: Literal["high", "medium", "low"]
    source: Literal[
        "extracted",                # CFM/L-s + dimensions → SMACNA tier
        "estimated:size_only",      # no flow data, size heuristic
    ]
    flow_value: float | None       # CFM or L/s
    flow_unit: Literal["CFM", "L/s"] | None
    velocity_fpm: float | None
    material: str = "galvanized_steel"


class V3Segment(_Frozen):
    id: str
    system_id: str
    geometry: Geometry              # polyline, in pixel space
    pixel_width: float              # measured at segment median skel point
    dimension: V3Dimension | None
    pressure_class: V3PressureClass
    reasoning_trace: list[ReasoningStep]


class V3DrawingResult(_Frozen):
    drawing_id: str
    width_px: int
    height_px: int
    page_unit: Literal["in", "mm"]
    ppu: float | None              # px per (in | mm). None when calibration failed.
    rotation_applied: Literal[0, 90, 180, 270]
    systems: list[System]
    segments: list[V3Segment]
    aggregate: AggregateStats       # reused from V1
    errors: list[str]
```

---

## 8. Confidence and honesty

- **`ocr:in_mask` + within ±15% of `ppu`** → `confidence="high"`
- **`ocr:in_mask` + outside ±15% of `ppu`** → `confidence="low"`, flagged in popover
- **`derived:pixel-measure`** → `confidence="medium"`
- **`estimated:size_only` pressure class** → `confidence="low"`, popover surfaces
  *"Pressure class estimated from size — no CFM/L/s extracted. User override available."*

The system never claims `"high"` confidence for a value it didn't OCR or
cross-validate.

---

## 9. Failure modes

| Mode | Detection | Behaviour |
|---|---|---|
| User picks no colors | `len(picks) == 0` | Pipeline returns empty `segments`, error: *"No colors selected — please pick at least one system color."* |
| User picks a color with no matching pixels | `mask.sum() == 0` for a system | That system contributes 0 segments; warning in `errors`, pipeline continues for other systems. |
| Page unit detection fails (no flow tokens at all) | `count(CFM) == 0 and count(L/s) == 0` | Default to `"in"`, flag in `errors`. |
| Calibration fails (< 3 token-segment pairs) | `len(in_band) < 3` | `ppu = None`, all `dimension.source = "ocr:in_mask"` if available else None. Pressure class falls back to size-only with low confidence. |
| Render-for-OCR oversize and tiling fails | engine error per tile | Per-tile failure absorbed; full-page result is the union of successful tiles. |
| Source raster < 3000 px long edge | size check at ingest | Hard error: 400 *"Source resolution too low for dimension extraction."* |

---

## 10. Phase 2 — designed, not shipped

1. **Pattern A (drawing 01-style):** add `attribute_pattern_a()` per §5.7 with
   nearest-system rule + black-geometry width measurement. Validate on 01.
2. **Pattern C (06-techjay-style):** validate the centerline pipeline on a
   higher-res source from Techjay.
3. **Per-segment graph splitting at skeleton junctions:** split connected
   networks into individual duct runs at branch points. Improves per-segment
   reporting and confidence.
4. **Real super-resolution layer:** swap `INTER_CUBIC + unsharp` for EDSR or
   LapSRN at the `preprocess_for_ocr(tile)` seam. Helps marginal raster sources.
5. **Schedule lookup for material override:** if the drawing's schedule lists
   per-system material, override the galvanized-steel default.
6. **Monochrome-drawing pipeline (drawings 02/04/05):** different sub-pipeline
   entirely; out of scope for V3 but called out for sequencing.

---

## 11. Spike artefacts and validation evidence

The pivot was de-risked with a spike before this design was written.
All spike code is in `/tmp/spike-v3/`. Key results (drawing 03):

| Stage | Evidence |
|---|---|
| Color mask | HSV `inRange((100,80,80) → (130,255,255))` cleanly captures all blue duct outlines |
| OCR @ 600 DPI | 71 `dim_rect` + 41 CFM tokens, mean confidence 0.95 |
| In-mask filter | 71 → 25 tokens (eliminates `12x12 CD-2`-style equipment labels with no hardcoded list) |
| Histogram ppu | dominant bin → `ppu = 4.38 px/in`, stable |
| Visible-side disambiguation | 22/25 pairs (88%) within ±15% of `ppu` |
| Distinct sizes recovered | 8: 13×15, 13×17, 13×19, 13×25, 13×29, 15×21, 17×29, 13×71 |
| Flow tokens in-mask | 0 / 41 — all CFM sits at diffuser faces outside the colored outlines (see §5.10) |
| Pressure class result | 6 LOW + 18 MEDIUM + 1 HIGH, all `source="estimated:size_only"` |

Drawing 01 spike (Pattern A) confirmed system identification via color
masking but width measurement breaks down because labels are outside the
parallel walls and aggressive morphological closing merges adjacent
ducts. Pattern A is real future work, not just integration.

06-techjay spike (Pattern C) showed the source thumbnail (621×308) has
no OCR-recoverable dim text at any upscale factor — design is sound,
validation requires a higher-res source from Techjay.

---

## 12. Open items

- **OQ-1.** Does the user pick colors *before* the page is shown
  (config) or *after* (interactive)? Current plan: interactive, with
  the rendered page as the picker target. Confirm UX.
- **OQ-2.** SMACNA hue → label dictionary needs a final pass with the
  user. Initial mapping (§5.4) is a starting point.
- **OQ-3.** Standard size snap table for `derived:pixel-measure` —
  imperial increments and metric increments need to be confirmed
  against SMACNA standard duct sizes.
- **OQ-4.** When the schedule lists CFM but no per-segment CFM tokens
  exist on the plan view, should we attribute schedule rows to systems
  by tag matching? Phase 2 if yes — V3 ships without it.

---

## 13. As-shipped architecture (V3 alpha)

Sections 1–12 above describe the design as it was committed before
implementation. This section captures **what actually shipped** after the
implementation iteration cycle. Where this section conflicts with §1–§12,
this section is correct; the earlier sections are preserved as design
history.

### 13.1 Live API surface

| Endpoint | Purpose | Notes |
|---|---|---|
| `POST /v3/render` | PDF/image → adaptive-DPI render + dominant-color swatches | Used by the picker to present the page + offer auto-suggested swatches |
| `POST /v3/detect` | PDF/image + `picks_json` → result + page PNG + overlay PNG | The full pipeline run. Synchronous. ~18–28s on benchmark drawings |
| `GET /v3/samples` | List bundled sample drawings | Looks at `V3_SAMPLES_DIR` env var, falls back to `../sample-HVAC/` |
| `GET /v3/samples/{name}` | Fetch a sample as a `File` for client-side drag-drop | |

The legacy V1 `/agent/*` routes remain mounted but are not exercised by
the V3 frontend.

### 13.2 As-shipped pipeline

The §4 pipeline diagram describes the design. As shipped, the order is
slightly rearranged because OCR runs **before** mask building (the OCR
text-bbox mask is needed for the text-overlap filter):

```
Ingest                    (V1, reused)
  ↓
Probe OCR + rotation      (V1, reused — autorotates the source if needed)
  ↓
Render at adaptive DPI    (V3 — text-height-driven, capped at 600 DPI)
  ↓
Full-page OCR             (horizontal pass + 90°-rotated pass)
  ↓
classify_all              (regex: dim_rect, dim_round, flow)
  ↓
build_all_system_masks    (per-pick:
                              hsv_inrange
                            → fill_outline  | thicken_centerline
                            → drop_small_components
                            → drop_blob_components
                            → drop_text_components
                            → skeletonize + distance transform)
  ↓
attribute_in_mask         (rect, with proximity fallback)
  ↓
attribute_round_in_mask   (round, with proximity fallback)
  ↓
attribute_flow_in_mask    (flow, strict in-mask only)
  ↓
calibrate                 (histogram-of-candidates ppu)
  ↓
resolve_visible_sides     (which side of an A×B token is plan-visible)
  ↓
pressure: from_flow       (when in-mask CFM/L-s exists)
       OR from_size_only  (SMACNA tier from dimension)
  ↓
render_overlay → result + page PNG + overlay PNG
```

### 13.3 Filter knob reference (config.py)

Every threshold exists in `app/pipeline/v3/config.py`. Each threshold
has a comment explaining the rationale. Summary:

| Knob | Default | Rationale |
|---|---|---|
| `target_text_height_px` | 24 | Adaptive-DPI target — render at the DPI that makes the smallest text 24 px tall |
| `min_dpi` | 200 | Floor — below this OCR fails on small text |
| `max_dpi` | 600 | Ceiling — 1200 DPI was investigated and didn't help structurally on dense plans (see ADR-0013) |
| `outline_close_kernel` | 11 | `MORPH_CLOSE` kernel for fill_outline — bridges 5–10 px gaps in colored outlines from anti-aliasing |
| `centerline_dilate_iters` | 2 | 3×3 dilation iterations for centerline mode — turns 1-px lines into 5–7-px usable corridors |
| `min_component_area_px` | 1500 | Drops text-glyph false positives. At 600 DPI a single OCR letter is ~1k px²; smallest legitimate round duct fill is ~5k+ px² |
| `blob_area_floor_px` | 500_000 | Combined with fill_ratio_max — drops only big blob-shaped components (rooms, title blocks) |
| `blob_fill_ratio_max` | 0.5 | A duct *tree* fills 10–30 % of its bbox; a room fills 70–90 %. 0.5 cleanly separates |
| `text_overlap_threshold` | 0.30 | Drops components mostly covered by OCR-text bboxes (callout boxes ~30 %, real ducts <5 %) |
| `nearest_skel_search_px` | 80 | In-mask attribution snap radius |
| `proximity_attr_search_px` | 50 | Off-mask proximity-attribution radius. At 150 px (initial value) drawing 03 over-attributed equipment labels and the regression test broke |
| `min_segment_radius_px` | 4.0 | DT half-width below which a "duct" is too thin to be real |
| `histogram_bins` | 60 | ppu histogram resolution |
| `inlier_band_pct` | 15.0 | ±N % from global ppu = high-confidence band |
| `min_pairs_for_calibration` | 3 | Minimum dim_rect attributions needed to converge calibration |

### 13.4 OCR rotation pass

PaddleOCR's detection head was trained primarily on horizontal text and
misses vertical/rotated labels. V3 runs OCR a second time on the page
rotated 90° CW; the rotated bboxes are remapped to original coords by
`runner.py::_remap_bboxes_from_cw90`.

The rotated matches feed **only** the text-exclusion mask
(`drop_text_components`). They do **not** feed the classifier
(`classify_all`) because rotated readings are noisy and would pollute
the dim grammar.

Cost: ~+10 s per `/v3/detect` call. The benefit (catching vertical
text labels on dim-callout boxes that share the duct hue) is worth it
on the benchmark; can be made conditional in a future iteration.

### 13.5 Regex grammar — production form

```
_DIM_RECT  = (?<!\d)(\d{2,4})\s*[\"”]?\s*[xX×]\s*[\"”]?\s*(\d{1,4})(?!\d)
   matches  15x13     bare, drawing 03
            28"x18"   inch-marked, drawing 02 callouts
            12"x10"   inch-marked, drawing 05 (Federal/SmithGroup)

_DIM_ROUND = (?<!\d)(\d{1,4})\s*(?:[\"”]?\s*[øØ⌀∅] | [\"”]\s*[0OQD](?!\d))
   catches both native diameter symbols and OCR misreads (digit "0",
   letter "O", letter "Q", letter "D" after a quote)

_CFM       = (?<!\d)(\d{1,5})\s*CFM
_LPS       = (?<!\d)(\d{1,5})\s*L/?S
```

The optional inch-mark variants in `_DIM_RECT` were added when
drawings 02 and 05 surfaced the convention. Drawing 03 uses bare format,
so all three variants need to match through one regex (rather than
trying multiple regexes per token).

### 13.6 Attribution rules — production form

```
attribute_in_mask(rect_tokens):
  for each rect token:
    if bbox-row intersects any system's filled mask:
      anchor = first in-mask pixel along the bbox row
      anchor = nearest_skel_pixel(anchor, search_radius=80) or anchor
      width_px = 2 × DT[anchor]
      rule = "in_mask"
    else:
      anchor = nearest_skel_pixel of any system, search_radius=50
      if anchor exists:
        width_px = 2 × DT[anchor]
        rule = "proximity"
      else:
        DROP — token doesn't belong to any duct
```

The `rule` field is preserved through the pipeline so downstream code
(and the popover) can be more skeptical of `proximity` attributions
than `in_mask` attributions.

### 13.7 Picker UX — production form

After three iterations, the picker landed on:

- **Cursor-following magnifier** — 140-px circular overlay, 6× zoom,
  pixelated rendering for crisp pixel-level visibility, lives outside
  the transformed wrap so it's constant size at any page zoom.
- **Click-rejection feedback** — transient banner explains why a click
  was rejected (`page background`, `faded gridline`, etc.). Sampled
  HSV is included in the message so the user sees what the system
  saw.
- **Dark-line band** — V<60 picks build a permissive HSV band (any
  hue, any saturation, V≤max(60, V+30)) and label the pick "Marked
  duct (dark)" with kind=other. See ADR-0012.
- **Pattern dropdown** — outline (default) or centerline, in the pick
  card. See ADR-0012.
- **Pixel-click as primary, swatches as fallback** — initial design
  used dominant-color swatches; user feedback rejected this approach
  ("suggesting colors is not the correct approach"). The swatch list
  stays in the API response for opt-in tooling but isn't surfaced in
  the UI.

### 13.8 Sample-set status (V3 alpha)

| Drawing | Convention | Pick | Pattern | Segments | Trustworthy widths |
|---|---|---|---|---|---|
| 01-afdb-clean-cad | Cyan saturated outlines | RGB(0,255,255) | outline | 25 | ✓ |
| 02-newwest-mixed-trades | Black closed outlines + callout boxes | RGB(0,0,0) dark band | outline | 20 (2 with extracted CFM) | ✓ |
| 03-caddsultants-shop | Blue saturated outlines | RGB(0,91,184) | outline | 35–60 (regression-tested) | ✓ |
| 04-asc2018-bid-set | Black parallel walls, no closed outline | RGB(0,0,0) dark band | centerline | 10 | ✗ — see ADR-0013 |
| 05-federal-attachment | Black parallel walls, dense plan | RGB(0,0,0) dark band | centerline | 58 | ✗ — see ADR-0013 |

The drawing-03 regression test (`backend/tests/test_v3_runner.py`)
locks the segment-count band, ppu band, high-confidence floor, and
distinct-size count. CI runs on every change to filter knobs, regex
grammar, or attribution logic.

### 13.9 Known limitations

Listed in priority order for the production-timeline roadmap (see
[`README.md` §4`](./README.md)):

1. **Pattern A not shipped** (drawings 04, 05). ADR-0013 documents
   the deferral and the OCR-anchored implementation plan.
2. **Manual color pick** required per drawing. Production roadmap
   includes auto-detection (M7–M9) and custom-trained detector
   (Year 2).
3. **Single-page only** — multi-sheet PDFs are processed page-by-page
   with no cross-sheet topology. M5–M6 in the roadmap.
4. **No schedule + legend extraction** — equipment lists, room
   schedules, and material specs are visible on the page but not
   structured in the API output. M3–M4 in the roadmap.
5. **No flow-tracing for size-only-pressure segments** — when CFM
   labels live at diffuser positions outside the mask, segments fall
   back to size-only pressure-class estimation. Phase-2 work
   (V3 §10).

### 13.10 Backend layout (production-relevant subset)

```
backend/app/
├── api/
│   ├── v3_routes.py        ← LIVE
│   ├── routes.py            (V1 /agent/* — parked)
│   └── deps.py
├── pipeline/
│   ├── v3/                  ← LIVE
│   │   ├── runner.py        (V3DetectionPipeline.run / run_with_artifacts)
│   │   ├── config.py        (every knob, with rationale comments)
│   │   ├── color_mask.py    (hsv_inrange, fill_outline, thicken_centerline,
│   │                         drop_small_components, drop_blob_components,
│   │                         drop_text_components, build_system_mask)
│   │   ├── ocr_classify.py  (regex grammar + classify_all + ocr_full_page +
│   │                         tile + dedupe + filter_for_page_unit)
│   │   ├── attribute.py     (in_mask + proximity)
│   │   ├── calibrate.py     (histogram-of-candidates + visible-side resolve)
│   │   ├── pressure.py      (SMACNA tiers, from_flow + from_size_only)
│   │   └── render.py        (overlay PNG construction)
│   ├── ingest.py            (reused from V1)
│   ├── probe_ocr.py         (reused from V1)
│   └── …                    (other V1 stages, not on V3 live path)
├── ocr/                     (RapidOCR ONNX wrapper)
├── source/                  (vector-PDF + raster source abstractions)
└── vlm/                     (parked — VLMClient seam preserved per ADR-0011)
```

### 13.11 Frontend layout (V3-relevant subset)

```
frontend/src/
├── components/v3/
│   ├── V3Upload.tsx          (landing — drag-drop + samples list)
│   ├── V3PickerView.tsx      (magnifier + click-rejection + dark-line band +
│   │                          pattern dropdown)
│   ├── V3PageCanvas.tsx      (PDF.js dynamic re-rasterization on zoom)
│   ├── V3CanvasViewer.tsx    (result viewer with SVG marker layer)
│   ├── V3ResultView.tsx      (sidebar + segment cards)
│   ├── V3Popover.tsx         (per-segment reasoning trace)
│   └── colorMath.ts          (rgb↔hsv, defaultBand, suggestKind, displayColor)
├── api/v3Client.ts           (LIVE — /api/v3/* via Vite proxy)
├── api/                      (V1 client, parked)
├── types/v3.ts               (TS mirrors of V3 Pydantic schemas)
└── styles/v3.css             (V3 components)
```
