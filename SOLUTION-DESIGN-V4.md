# Solution Design V4 — HVAC Duct Detection: Length, CFM, Pressure

**Status:** locked design, pre-implementation
**Builds on:** V3 (color-driven detection pipeline). V4 supersedes V3 for the new test set (`testset2.pdf`) and the V3 stack is retained as a fallback path until V4 is validated end-to-end.

**Driver:** post-submission feedback —
1. test on `testset2.pdf`, and
2. detect and display **duct run lengths** in addition to dimensions.

V4 also adds **CFM trace** and **pressure (value + SMACNA class)** because the test drawing exposes those signals cleanly and they were already in scope of the original brief.

---

## 1. Scope

| In scope (MVP) | Out of scope (deferred) |
|---|---|
| Single-page PDFs | Multi-page sets, cross-sheet continuations |
| Round (`N"ø`) and rectangular (`W"xH"`) duct labels | Oval, flat-oval, metric, neck-size annotations |
| Drawing-to-scale geometry; scale read from title block or user-entered | Schematic/NTS drawings |
| Segments bounded by perpendicular cross-cut bars | Hand-drawn / scanned drawings |
| Connectors: transitions, elbows, tees, Y-branches, equipment boxes (treated generically) | Equipment-type semantics (VAV/FPB/AHU internal models) |
| Air terminals = circle with horizontal divider, type top, CFM bottom | Linear bar diffusers, custom terminal symbols |
| Pressure: drop value + SMACNA class per segment | Real-world balancing, damper modulation |

---

## 2. Locked drawing conventions (assumptions)

These are MVP **assumptions**. Each must be highlighted in the README and CHANGELOG.

1. **A1.** Dimension labels live **inside** the duct fill (one label per segment).
2. **A2.** Only **one** numeric token lives inside a duct interior (no other measurements share the space).
3. **A3.** Rectangular duct labels are always **`WxH`** (width × height).
4. **A4.** No insulation/double-line wrap pattern. If encountered, the **inner bbox** is the duct.
5. **A5.** Air terminal symbol = **circle with horizontal divider**; top half = type letter (ignored in MVP), bottom half = numeric **CFM**.
6. **A6.** A **segment** is a region bounded by two perpendicular cross-cut bars at its ends. Transitions, elbows, tees, Y-branches, and equipment boxes are **connectors**, not segments.
7. **A7.** Solid-touching ducts are **connected**. **Dashed** rendering = duct passes underneath; logically a single segment, displayed with alpha overlay so the overlap appears darker.
8. **A8.** Drawing is **to scale**. Label text is **axis-aligned** (0° or 90°), never angled to follow the duct.
9. **A9.** Unlabeled segments (e.g., bent continuation at the same size) are sized by **direct pixel measurement × scale**, not by inheritance.
10. **A10.** A single segment can host **N air terminals along its length** (vents in a dining-room run). CFM varies along the segment; segment length is the full run.
11. **A11.** Connector materials (rigid vs flex) vary; for MVP both are treated as a generic connector with a default equivalent length the user can override.
12. **A12.** **Grey-shaded regions** in the drawing are non-HVAC architectural fill and are **stripped** during preprocessing as noise.
13. **A13.** Open-ended ducts have **no airflow** unless tagged with a terminal symbol or a user-entered CFM.
14. **A14.** All CFM values for MVP are read from terminal symbols (not from plan-note prose).
15. **A15.** Single-page PDF only. The user picks the page on upload if the source has more than one.

---

## 3. Pipeline (top-level)

```
PDF upload
  └─ Page select (if multi-page) — user picks the mechanical plan
  └─ Preprocess
       ├─ Remove grey architectural fill (A12)
       ├─ Read scale from title block (OCR)  — or user override
       └─ Rasterize at fixed DPI
  └─ Detect
       ├─ Duct shapes (outline-based — no color dependency)
       ├─ Cross-cut bars  → segment boundaries (A6)
       ├─ Connectors (transitions / elbows / tees / equipment)
       ├─ Air terminals (circle + divider) (A5)
       └─ Crossings (dashed under solid) (A7)
  └─ OCR labels (axis-aligned, two regex formats) (A3, A8)
  └─ Build network graph
       ├─ Nodes: connectors + terminals + open-ends
       ├─ Edges: segments (with attached terminals)
       └─ Resolve unlabeled segments by pixel-width × scale (A9)
  └─ Compute
       ├─ Length per segment (centerline polyline × scale)
       ├─ Direction (auto: from non-terminal end → terminals; user-overridable)
       ├─ CFM trace (sum of downstream terminals)
       ├─ Velocity, pressure drop, pressure value at endpoints
       └─ SMACNA class per segment
  └─ Annotate + serve
       ├─ Overlay polygons + IDs on the drawing
       ├─ Click segment  → length, dimension, CFM range, P at both ends, class
       └─ Click terminal → CFM, type letter
```

---

## 4. Module map (Python, additive over V3)

```
backend/app/
  cv/
    preprocess_v4.py        # grey-area removal, rasterization, scale extraction
    duct_outline.py         # outline-based duct polygon detection
    crosscut.py             # perpendicular bar detection → segment ends
    connectors.py           # transitions, elbows, tees, equipment boxes
    terminals.py            # air-terminal circle + divider detection
    crossings.py            # dashed-line bridging
  ocr/
    label_v4.py             # axis-aligned (0°/90°) OCR for `Nø` / `WxH`
    scale_block.py          # title-block scale extraction
  detect/
    network.py              # graph build (nodes, edges, attached terminals)
    geometry.py             # centerline + length + pixel-width sizing
  pipeline/
    runner_v4.py            # orchestration
    flow_trace.py           # CFM accumulation, direction inference
    pressure.py             # ASHRAE friction + fitting K + SMACNA class
  schemas.py                # extend with: length_ft, cfm, velocity_fpm,
                            # pressure_in_wc_start/end, pressure_class
  api/
    sessions.py             # extend response payload; no new endpoints
```

Frontend additions are localized to `frontend/src/components/Annotated*` — extend the click-detail panel; no new routes.

---

## 5. Length calculation

- Each segment has a centerline polyline (midline between its two long edges).
- `length_ft = polyline_pixel_length × scale_inches_per_pixel ÷ 12`.
- **Cross-check:** when a segment has a labeled diameter, `pixel_width / labeled_inches` must agree with the title-block scale within ±3%; mismatch flags the segment for review.
- For **unlabeled** segments, the pixel width yields the diameter directly via the title-block scale. No inheritance.

---

## 6. CFM trace + pressure

- Network direction defaults to flowing from the single non-terminal node (equipment / open / source) toward the terminals; user can flip per network.
- CFM at any segment endpoint = Σ CFM of all terminals reachable downstream, including terminals attached to that segment beyond the point.
- Velocity per segment cross-section = CFM ÷ area.
- Pressure drop per segment uses ASHRAE/SMACNA friction (Darcy with galvanized-steel roughness default) + fitting K-values at incident connectors. **All operational variables — air density, friction factor, fitting K-values, flex-duct equivalent length — are user-editable with sensible defaults.**
- Pressure value at endpoints is reported in inches of water column (in. w.c.).
- **SMACNA pressure class** (per-segment) — **Low ≤ 2" w.c., Medium 2–3" w.c., High > 3" w.c.**, with a secondary velocity check (Low ≤ 2000 FPM, Medium 2000–2500, High > 2500). User-editable. The output panel shows both the numeric value and the class label, citing SMACNA.

---

## 7. UI / interaction

- The annotated overlay renders segments with stable IDs and crossing overlaps as alpha-darkened regions.
- **Click a segment** → panel shows: dimension, length (ft), CFM at start/end, velocity, pressure at start/end, pressure class. Class displays the SMACNA basis.
- **Click a terminal** → panel shows: CFM, type letter (raw, no interpretation in MVP).
- Operational variables (density, friction factor, flex equivalent length, threshold table) live in a single "Calculation settings" drawer; changes recompute live.

---

## 8. Code quality (lead-level expectations)

- Python: PEP-8, ≤ 100 chars/line, modules ≤ ~400 lines, functions ≤ ~60 lines. Type hints on public functions.
- Frontend: TS strict, components ≤ ~200 lines, no `any` in new code.
- Comments only where the *why* is non-obvious. No "added for X" or "TODO". No marketing/celebratory language.
- No speculative abstractions, no feature flags, no shims, no parallel V3/V4 implementations of the same concern — V4 modules replace V3 for the V4 path.
- Tests: unit tests for `geometry`, `flow_trace`, `pressure`, OCR regex; one end-to-end test on `testset2.pdf` covering at least one round, one rectangular, one multi-terminal segment, and one crossing.
- Quality gate (per project memory): proper standards, clean comments, intent-driven, no over-engineering — run as a completion gate before declaring V4 done.

---

## 9. Repo cleanup (post-implementation)

1. Move legacy artifacts to `implementation/archive/v1-v3/`:
   - V1/V2/V3-only source files no longer referenced by V4 runner
   - Legacy fixtures and screenshots
   - Old runner scripts under `backend/scripts/`
2. Update top-level `README.md` and `implementation/README.md` to describe the V4 flow and **explicitly list every assumption A1–A15**.
3. Append a `CHANGELOG.md` entry for V4 calling out: feedback addressed, new outputs (length, CFM, pressure), the assumption list, and known limitations.
4. Add ADRs:
   - `adr/0014-v4-scope-and-assumptions.md` — locks A1–A15
   - `adr/0015-outline-based-duct-detection.md` — switch from V3 color-driven to outline-based
   - `adr/0016-pressure-class-via-smacna.md` — class thresholds + user override
5. Refresh `SOLUTION-DESIGN-V3.md` with a "superseded by V4" header (do not delete).

---

## 10. Open / deferred items (explicitly not MVP)

- Cross-sheet continuation (`see M3.0`) and multi-page PDF intake.
- CFM extracted from plan-note prose (currently terminal symbols only).
- Equipment-type semantics (VAV/FPB modeled as flow modulators).
- Insulation / liner notation.
- Oval, flat-oval, metric duct labels.
- Hand-drawn / scanned drawings.

---

## 11. Acceptance criteria

- `testset2.pdf` and the original `01-afdb-clean-cad.pdf` … `06-techjay.png` set both produce: annotated overlay, per-segment length, CFM range, pressure value at endpoints, and SMACNA class.
- Round + rectangular + multi-terminal + crossing cases all click-through correctly in the UI.
- Assumption list visible on the upload page (or in the result panel) with each MVP assumption flagged.
- Length cross-check (paper-scale vs pixel-width-vs-diameter) passes within ±3% on every labeled round segment in `testset2.pdf`.
