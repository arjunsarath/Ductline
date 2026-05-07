# Ductline — HVAC Duct Detection & Annotation

> [!IMPORTANT]
> **Looking to install or run the project?** Head straight to [`implementation/README.md`](./implementation/README.md) — prerequisites, dev setup, run instructions, and the API surface all live there.
>
> This document is product-level: what it does, why each version exists, what works today, and the production roadmap.

> **Status:** V4.5 active — dual-branch detection (rectangles → ducts, circles → air terminals)
> + CFM-aware pressure attribution. V4 outline detection retained as the inner
> primitive. V3 retained as the colour-driven fallback path. V1 + V2 archived as
> design + retrospective. Roadmap and standards-based timeline below.
> **Repo layout:** `/PRD.md` · `/SOLUTION-DESIGN.md` (V1) · `/SOLUTION-DESIGN-V2.md` ·
> `/SOLUTION-DESIGN-V3.md` (superseded) · `/SOLUTION-DESIGN-V4.md` (active) ·
> `/adr/` · `/implementation/` · `/sample-HVAC/` (5 benchmark drawings) ·
> `/implementation/drawings/testset2.pdf` (V4 acceptance set)
> **Author:** Arjun Sarath

---

## 1. What this product does

Reads a single-page HVAC duct-layout drawing (PDF or image), detects each duct segment, extracts its dimension (`24"×16"`, `12" Ø`, …), classifies its pressure class against SMACNA tiers, and surfaces every detection with a reasoning trace the user can click into. The output is structured duct data that can drive cost estimation, fabrication cut lists, or as-built records — work that is currently done by hand at every estimator and shop.

The product wedge is **drawing → structured duct data** — replacing the manual re-keying that happens today across estimating, fabrication, and facilities.

---

## 2. Five iterations, one shipping path (V4.5)

Five architectures were attempted in sequence — V1 / V2 (VLM-driven, archived), V3 (colour-driven, retained as fallback), V4 (outline-based, contour primitive), and V4.5 (dual-branch + CFM-aware pressure, currently shipping). The sequence matters for understanding the current state.

### 2.1 V1 — Hybrid VLM + deterministic CV (built, observed limits)

> Full design: [`SOLUTION-DESIGN.md`](./SOLUTION-DESIGN.md). Implementation under `implementation/` previously used this path.

**Architecture:** 7-stage pipeline (Ingest → Quality → Region → Detect → Extract → Classify → Assemble). Stage 4 calls a vision-language model (VLM, `llama3.2-vision` 11B) once per drawing for duct bounding boxes, with a deterministic CV fallback (HoughLinesP + parallel-pair sweep).

**Why it doesn't work in production today:**

| Failure mode | Frequency on benchmark | Root cause |
|---|---|---|
| Malformed JSON from VLM | 4/5 drawings | Open-source 11B vision models can't reliably emit structured JSON for technical drawings |
| Hallucinated regular-grid bboxes | 2/5 drawings | Model parrots its training-distribution prior (web charts) instead of reading the input |
| Ollama timeout (≥3 min on host hardware) | 1/5 drawings | 11B model's vision pass exceeds soft latency budget on consumer hardware |
| CV-only fallback over-recalls | 5/5 drawings | HoughLinesP fires on walls, columns, grid lines, drawing borders — without OCR-proximity gating, every parallel pair becomes a candidate |

The 5-drawing sweep on V1 hit the 60-candidate cap on every drawing because of CV over-recall, with `[cv_fallback]` stage markers showing the VLM never actually contributed. **The seam (`VLMClient` Protocol, ADR-0002) survives.** Swapping to Anthropic Claude vision (or `llama3.2-vision:90b`) would likely meet the latency + JSON-quality bar — but that lands the product on a paid frontier API, which conflicts with the on-prem requirement that came out of user research with government/AEC firms.

**Why V1 is parked, not shipped:** the on-prem constraint plus the open-source VLM unreliability means V1's hybrid bet is unwinnable today. The 11B model isn't good enough; the 90B model is too slow on commodity hardware; the frontier proprietary models can't run on-prem. The pipeline stages around the VLM (ingest, OCR, classify, assemble) all work — the bet was the VLM call itself.

### 2.2 V2 — V1 + reviewer loop + region-aware detection (designed, not shipped)

> Full design: [`SOLUTION-DESIGN-V2.md`](./SOLUTION-DESIGN-V2.md).

V2 was the natural enhancement: a reviewer-agent second pass on low-confidence detections (ADR-0009), tiled detection with trail context (ADR-0008), and categorizer-first ordering (ADR-0010) so plan-view regions get sized differently from schedules and notes.

**Why V2 isn't being built right now:** V2's improvements all assume the underlying VLM detection works. They make a working detector more accurate; they don't fix a detector that produces malformed JSON 80% of the time. Each V2 enhancement is an additional VLM call — adding `n` more chances for the same failure. Building V2 on top of an unreliable VLM would compound the unreliability, not compensate for it.

V2 stays designed and named because every enhancement (reviewer loop, tiled detection, categorizer ordering) is genuinely valuable **once a reliable detector exists**. V3 is the path to a reliable detector.

### 2.3 V3 — color-driven deterministic pipeline (current, in iteration)

> Full design: [`SOLUTION-DESIGN-V3.md`](./SOLUTION-DESIGN-V3.md).

**Architecture posture:** the VLM is removed from the detection path entirely. The user picks the *color* of each duct system on the rendered page (a single click per system); a deterministic pipeline does the rest — HSV color masking → flood-fill → skeleton + distance transform → OCR-token attribution → SMACNA pressure-class classification.

This trades the failure mode of "VLM is unreliable" for the constraint of "user must identify the duct color once per system." On color-coded drawings (the dominant convention in commercial mechanical drawings) that constraint is fast and reliable.

**What works today (5-drawing benchmark):**

| Drawing | Convention | V3 result | Trustworthy widths? |
|---|---|---|---|
| 01 — `afdb-clean-cad` | Saturated colored outlines | 25 segments, dim + pressure class extracted | ✓ |
| 02 — `newwest-mixed-trades` | Black closed-outline ducts + callout boxes | 20 segments, 2 with extracted CFM | ✓ |
| 03 — `caddsultants-shop` | Saturated colored outlines | 35–60 segments (regression-tested) | ✓ |
| 04 — `asc2018-bid-set` | Parallel-wall dark ducts | 10 segments via centerline mode | ✗ — widths bogus |
| 05 — `federal-attachment` | Parallel-wall dark ducts, dense | 58 segments via centerline mode | ✗ — widths bogus |

Drawings 01–03 are production-quality. Drawings 04–05 attribute dim labels correctly but the pixel-width measurement is unreliable because the pipeline doesn't yet handle parallel-wall ducts with proper geometric width extraction (Pattern A — see §5).

### 2.4 V4 — outline-based pipeline with length, CFM trace, and pressure (active)

> Full design: [`SOLUTION-DESIGN-V4.md`](./SOLUTION-DESIGN-V4.md). ADRs:
> [`adr/0014-v4-scope-and-assumptions.md`](./adr/0014-v4-scope-and-assumptions.md),
> [`adr/0015-outline-based-duct-detection.md`](./adr/0015-outline-based-duct-detection.md),
> [`adr/0016-pressure-class-via-smacna.md`](./adr/0016-pressure-class-via-smacna.md).

V4 is the active path for the new acceptance drawing (`implementation/drawings/testset2.pdf`)
and addresses the two post-submission feedback items: validate on `testset2.pdf`,
and detect + display **duct run lengths** (in feet). It also adds **CFM trace**
through the duct network (terminal symbols → segment endpoints) and a per-segment
**pressure value + SMACNA class** (Low ≤ 2" w.c., Medium 2–3", High > 3", with a
secondary velocity check). All operational variables (air density, friction
factor, fitting K-values, flex-duct equivalent length, threshold table) are
user-editable in a Calculation Settings drawer.

V4 swaps V3's HSV-colour mask for outline-based duct detection (see ADR-0015) and
introduces a duct-network graph: nodes are connectors / terminals / open ends,
edges are segments bounded by perpendicular cross-cut bars. The frontend exposes
a **V3 / V4 tab toggle** on the upload page; V3 stays the default to avoid
disturbing the colour-driven flow.

**How to run V4 (CLI):**

```bash
cd implementation/backend
.venv/bin/python scripts/run_v4.py ../drawings/testset2.pdf
```

**How to run V4 (HTTP):**

```
POST /api/v4/sessions   ← multipart upload, single-page PDF
```

The session response carries the segment list (length_ft, dimension, CFM at
endpoints, velocity, pressure at endpoints, pressure class), the terminal list
(CFM, type letter), and the rendered annotated overlay. The frontend's V4 tab
consumes the same payload.

**MVP assumptions (A1–A15):** V4 ships with 15 explicit drawing-convention
assumptions locked in `SOLUTION-DESIGN-V4.md` §2 and surfaced on the V4 upload
page as an assumptions banner. The full list:

1. **A1.** Dimension labels live inside the duct fill (one label per segment).
2. **A2.** Only one numeric token lives inside a duct interior.
3. **A3.** Rectangular labels are always `WxH` (width × height).
4. **A4.** No insulation/double-line wrap pattern; if encountered, the inner
   bbox is the duct.
5. **A5.** Air terminal = circle with horizontal divider; top half = type
   letter (ignored in MVP), bottom half = numeric CFM.
6. **A6.** A segment is bounded by two perpendicular cross-cut bars at its
   ends. Transitions, elbows, tees, Y-branches, and equipment boxes are
   connectors, not segments.
7. **A7.** Solid-touching ducts are connected. Dashed rendering = duct passes
   underneath; logically a single segment, displayed with alpha overlay.
8. **A8.** Drawing is to scale. Label text is axis-aligned (0° or 90°).
9. **A9.** Unlabeled segments are sized by direct pixel measurement × scale,
   not by inheritance.
10. **A10.** A single segment can host N air terminals along its length.
11. **A11.** Connector materials (rigid vs flex) vary; for MVP both are
    treated as a generic connector with a default equivalent length the user
    can override.
12. **A12.** Grey-shaded regions are non-HVAC architectural fill and are
    stripped during preprocessing.
13. **A13.** Open-ended ducts have no airflow unless tagged with a terminal
    symbol or a user-entered CFM.
14. **A14.** All CFM values for MVP are read from terminal symbols (not from
    plan-note prose).
15. **A15.** Single-page PDF only. The user picks the page on upload if the
    source has more than one.

**Known V4 limitations:**

- Rectangular dimension labels on dense angled ducts may be missed by OCR and
  silently fall back to a round-pixel-measured estimate (observed on the
  `22"x14"` duct in `testset2.pdf`).
- Terminal-to-segment incidence on `testset2.pdf` is sparse — ~178 terminals
  are detected but few attach to segments due to limited CV recall on
  cross-cut bars; this suppresses CFM accumulation on those segments.
- Multi-page PDFs require manual page selection; the runner enforces
  single-page input.
- CFM is read only from terminal symbols; plan-note prose CFM (e.g.,
  `2,800 CFM up to roof`) is not parsed.
- Equipment nodes (VAV/FPB/AHU) are treated as generic connectors; no
  equipment-type semantics.
- Cross-sheet continuations (`see M3.0`) are dead-ends in V4.
- See `SOLUTION-DESIGN-V4.md` §10 for the full deferred list.

### 2.5 V4.5 — dual-branch detection + CFM-aware pressure (current)

> Full rationale: [`adr/0017-v4.5-dual-branch-and-cfm-aware-pressure.md`](./adr/0017-v4.5-dual-branch-and-cfm-aware-pressure.md).
> V4 outline detection still runs as the contour primitive; V4.5 layers a
> second classifier path for air terminals and a length-and-pressure pass
> on top.

**The pipeline (post-`filter_oversized`):**

```
post-oversized contours
  ├─ Duct branch:  filter_is_rectangle → squarish → ink-density → aspect
  │                → rect-grammar VLM ladder (Tesseract@600 → VLM@600/900/1200)
  │                → median px-per-inch scale → length_ft per duct
  └─ Terminal branch: filter_is_circle (4πA/P²) → filter_has_horizontal_divider
                    → 3-digit OCR ladder (same Tesseract→VLM ladder)
                    → CFM per terminal
        ↓
  Merge → CFM-aware pressure attribution per duct:
    direct-adjacency (≤6px) → terminal CFM exact
    fallback              → inverse-distance-weighted CFM proxy across a
                            scale-derived 4 ft neighborhood
        ↓
  Velocity → Darcy ΔP = f·(L/Dh)·(V/4005)² → SMACNA class
```

**What works on `testset2.pdf` today:**

- Rectangle detection, OCR (`22"x14"`, `14"ø`, …), length in feet derived from a median scale.
- Air-terminal detection (circle + horizontal divider) and CFM read via the 3-digit OCR ladder.
- Direct-adjacency duct↔terminal CFM attribution (every duct that touches a terminal gets the exact CFM).
- Neighborhood-weighted CFM proxy for the rest (a duct in a 700-CFM corridor reads "high pressure" even without a touching terminal).
- Per-bbox image-hash OCR cache — slider re-fires and parameter tweaks reuse prior reads.
- Frontend: full-pipeline run on confirm, live progress UI (7 stages with sub-status + per-bbox bars), PDF underlay with adjustable opacity, "shade by pressure class" overlay, click-to-highlight linked terminal, inspector with length / CFM / velocity / ΔP / class, stat strip showing counts + class breakdown.

**What is *not* production yet (full list in §5.4):**

- Network airflow — V4.5 does direct adjacency + a short-radius fallback. A real fan/AHU → trunk → branch → terminal flow trace is intentionally deferred.
- VLM still hallucinates on hard crops — handled by the duct-grammar regex but not by structural confidence calibration.
- Image preprocessing assumes upright single-page PDF; scanned/skewed/rotated input fails.
- Crossings (a duct passes under another, drawn dashed per A7) are not split — the contour fuses, length and CFM bleed across networks.
- Ducts without dimension labels inside (A9 fallback in V4) are not yet wired through V4.5; they currently drop out of the duct branch.
- Bends, elbows, tees, transitions, equipment boxes are *contours* but not classified as connectors. The duct branch keeps them or drops them based on ink density alone — segment topology is missing.

---

## 3. Why these failure modes are not bugs

Three claims that need to be defensible:

**On V1 + V2:** open-source VLMs at sizes that run on-prem (≤90B parameters) do not reliably produce structured detections from engineering drawings. This isn't a bug to be fixed by better prompting — it's the current state of small-VLM capability. Frontier proprietary models (Claude vision, GPT-4o vision) are sufficient but require sending drawings off-prem, which the user-research interviews explicitly rule out for government, defence, and large AEC firms. The architectural seam (`VLMClient` Protocol) is preserved so the integration is one config flip away when capability or compliance change.

**On V3 dimensional accuracy on parallel-wall plans:** the deterministic pipeline assumes a duct is rendered as a *closed* shape — colored outline (Pattern B), colored centerline (Pattern C), or solid colored fill (Pattern A). Drawings 04 and 05 use **uncolored parallel walls** (two black lines forming the duct boundaries, with the duct labeled inline). To measure pixel width on this convention, the pipeline must detect *pairs of parallel lines* and compute the perpendicular gap. That's a Hough-transform + parallel-pair-clustering step that hasn't been implemented yet. Centerline mode produces the right *attribution* (each label snaps to the right duct location) but the width is the dilation thickness, not the duct width — so confidence flags from centerline mode are correctly suspect.

**On the pivot to "user picks a color":** an alternative would be auto-detecting the duct system color. We considered this and parked it: an auto-detector that gets the wrong color produces zero detections silently, which is harder to recover from than a UI that takes one click. The picker UX (cursor-following magnifier, exact-RGB sampling, click-rejection feedback) has been iterated on this trade-off — see [`SOLUTION-DESIGN-V3.md` §5.4](./SOLUTION-DESIGN-V3.md).

---

## 4. Production timeline (standards-based)

Timeline frames each milestone as **"the product handles drawings of standard X in Y months."** Standards here are the conventions the AEC industry actually uses, observed across the benchmark set and in [`competitor-research.md`](./competitor-research.md).

```
Now (M0):     V3 alpha — color-coded Pattern B drawings work end-to-end.
              Manual color pick. Single page. SMACNA pressure class.
              5-drawing benchmark: 3/5 trustworthy, 2/5 partial.

M1–M2:        Pattern A (parallel-wall ducts) via OCR-anchored Hough.
              Drawings 04–05 produce trustworthy widths.
              Coverage shifts from 3/5 to 5/5.

M3–M4:        Schedule + legend extraction.
              Equipment lists, room schedules, and material specs become
              structured output alongside duct geometry.

M5–M6:        Multi-page + cross-sheet topology.
              Most real mechanical sets are 8–40 sheets. Cross-references
              ("see M2.05 for diffuser detail") need to resolve, and ducts
              that branch across sheets need to stitch.

M7–M9:        Dark-line auto-detection (no manual color picking).
              For drawings without a color-coded system, a small classifier
              identifies "this drawing uses parallel-wall convention" and
              auto-configures the pipeline. Reduces user friction to one
              upload.

M10–M12:      Confidence calibration via labeled corpus.
              Build a 200–500 drawing labeled corpus across estimating
              firms; calibrate confidence flags so HIGH means HIGH and
              LOW means a human definitely needs to look. This is what
              makes the product trustworthy enough for unattended ingest.

Year 2 Q1:    Hybrid VLM-assisted detection (see §5.2).
Year 2 Q2:    Multi-trade drawings (HVAC + plumbing + electrical sharing
              a sheet) — the V3 pipeline gates by user-picked color so
              this is mostly a UX problem, not an algorithm problem.
Year 2 Q3:    Custom detection model (see §5.3).
Year 2 Q4:    Production-grade unattended ingest with SLA-bounded latency
              and confidence calibration.
```

Caveats: timelines assume one engineer full-time on detection/algorithms, plus part-time front-end + ops. Each "M*N*" milestone is **2–3 weeks of focused implementation + 1 week iteration on the benchmark**. Real-world drift (new drawing convention surfaces in a customer pilot, regulatory change, label noise in collected corpus) can push any milestone by a multiple. The timeline is what's defensible if the product moves at the pace V3 has shipped at.

---

## 5. Plans for improving the current model

Four improvement vectors, ordered by ROI. V4.5 is the active product surface, so its path-to-production work (§5.1) is the highest-priority track.

### 5.1 Path to V4.5 production quality (M0–M9)

V4.5 demos end-to-end but several engineering surfaces sit at MVP fidelity. Each row is sized as a focused 1–6 week delivery; together they take V4.5 from "demo on `testset2.pdf`" to "trustworthy on the broader benchmark."

| Item | Why it matters | Approach | Effort |
|---|---|---|---|
| **Network-traversal CFM** (replace direct adjacency + neighborhood proxy) | Real ducts branch — a trunk's CFM is the *sum* of every downstream terminal. Direct adjacency only fits leaf ducts. | Connected-component analysis on the binary ink mask: every contour in one ink blob shares a network. CFM(duct) = sum(CFM of all terminals in same component). Source node is the largest equipment bbox. ~30 LOC, additive. | 1 wk |
| **VLM hallucination handling** | The 3-digit ladder catches OCR misreads via the regex predicate; the duct-grammar ladder uses `standardize_duct_label` similarly. But: silent fallbacks (a `θ` read as `0`, a `4'` read as `4` with no inch mark) still slip through and inflate the median px-per-inch scale. | (a) Multi-pass voting at each DPI step (3 generations, majority text wins). (b) Cross-check the OCR'd cross-section against the rectangle's actual pixel-short side at the global scale; reject mismatches >2× off median. (c) Reject reads with non-grammar punctuation (e.g. a CFM read of `1.0`) before they cast a scale vote. | 2 wks |
| **Image processing** | Today's preprocessing is grey-strip + binarise. Scanned PDFs, skewed exports, low-DPI sources all fail because the contour pipeline assumes crisp B/W with axis-aligned text. | (a) Probe-OCR rotation auto-correct (already in V1; reuse). (b) Adaptive DPI based on smallest text height (RapidOCR's first pass tells us the pixel size of digits → up-render until we hit the OCR confidence floor). (c) Skew correction via Hough-derived dominant line angle. | 2 wks |
| **Underlying ducts** (dashed crossings, A7) | A duct passing *under* another is rendered with a dashed gap. V4.5's contour pass merges the gap halves into separate broken contours, or worse, fuses them with a different network. | Detect dashed runs (gap pattern in line segments), virtually re-connect them as a single segment with an "underlying" flag. Existing `app/cv/crossings.py` was prototyped in V4 but not used; revive and integrate. | 2 wks |
| **Ducts without dimensions** (A9 fallback) | If OCR misses a label (or there is none), the duct currently has no length and no pressure. On real plans 10–20% of segments rely on the A9 inheritance / pixel-measurement fallback. | Once a global px-per-inch scale exists from labelled neighbours, derive cross-section from rectangle pixel-short × 1/scale, plausibility-gate to [4″, 60″], emit with `dim_inferred=true`. Wired in V4 design but deferred for V4.5; bring back. | 1 wk |
| **Bends, elbows, tees, transitions** | These are connectors per A6 — currently they're either kept as small rectangles (false positives in the duct branch) or dropped silently. Without explicit connector classification, the network graph is wrong, length is double-counted at corners, and pressure drop ignores fitting K-values. | Connector classifier: (a) elbow = two corridors meeting at ~90° with a quarter-circle interior; (b) tee/Y = three-way intersection; (c) transition = trapezoid (one short side, one long side); (d) equipment = labelled rectangle from schedule. Existing `app/cv/connectors.py` is prototyped — finish + integrate. K-value lookup already lives in `OperationalVars.fitting_k_table`. | 4–6 wks |
| **Confidence calibration** | "SMACNA: High" and "CFM: 700" carry the same visual weight today regardless of how derived. A *measured* high differs from an *estimated* high. | Surface `pressure_estimated` already-on-payload as a per-row confidence chip; add per-duct confidence aggregating (label OCR confidence) × (length plausibility) × (CFM source: measured vs neighborhood vs floor). | 2 wks |
| **Multi-page + cross-sheet** | Real estimating sets are 8–40 sheets. V4.5 enforces single-page. | Same plan as V3 §5.1 — sheet-by-sheet processing with cross-reference resolution. | 3–4 wks |

**Sequence:** start with network-traversal CFM (week 1) — it changes the most about *what the user sees* with the smallest diff. Then VLM hallucination + image processing in parallel (weeks 2–4). Underlying ducts + unlabeled fallback after that (weeks 5–6). Connector classification is the bigger bet (weeks 7–12); it's what gets V4.5 from "shows pressure heat-map" to "computes a real network static-pressure budget."

### 5.2 V3 deterministic pipeline — incremental wins (M0–M6)

Each item below is a focused 1–3 week delivery. They compound.

| Improvement | Why | Effort |
|---|---|---|
| **OCR-anchored Pattern A** — for each dim label, find local parallel walls via Hough + pair-clustering and measure perpendicular gap as the pixel duct width. | Drawings 04+05 produce trustworthy widths. Coverage 3/5 → 5/5. | 2–3 weeks |
| **Pattern A auto-detection** — recognise "this drawing has parallel-wall ducts" from a sparse-detection signal and auto-flip the pipeline. | Removes a user decision. | 1 week |
| **Schedule + legend extraction** — separate stage that recognises tabular regions and extracts cell content. | Adds equipment lists + material specs to the structured output. | 3–4 weeks |
| **Confidence calibration** — collect labeled ground truth on the benchmark + 200–500 additional drawings; tune the band-pct thresholds and dim-text-vs-pixel-width tolerances against the labels. | `dim_confidence: high` becomes statistically reliable; downstream consumers can trust unattended HIGH detections. | 6 weeks |
| **Multi-page support** — frontend + backend changes so a multi-sheet PDF is processed sheet-by-sheet, with cross-sheet duct topology resolved (`see M2.05`-style references parsed). | Most real estimating jobs ship as 8–40 sheet sets, not single sheets. | 3–4 weeks |
| **Drawing rotation auto-correction** — currently we trust the source orientation. Some scanned drawings arrive rotated. Detect via OCR-text orientation majority and rotate before processing. | Robustness on scanned input. | 1 week |
| **Round-duct attribution improvements** — round dims (`13" Ø`) currently attribute via the same in-mask rule as rectangular; round ducts are often labeled with leader lines outside the mask, so a leader-line tracer would help. | Round-duct attribution rate improves on drawings with leader-style callouts. | 2 weeks |

### 5.3 Hybrid VLM-assisted (Year 2)

The architectural seam (`VLMClient` in V1, ADR-0002) survives in the codebase precisely so this option remains open. Two flavours:

1. **Frontier proprietary VLM behind an opt-in flag** — when a customer is fine sending drawings off-prem (commercial estimating firms, non-defence work), Claude vision or GPT-4o vision detects duct regions and the V3 deterministic pipeline measures them. This is straightforward to ship; cost is per-drawing API spend.
2. **Quantised + fine-tuned open-source VLM for on-prem** — take a 90B-class vision model, fine-tune (LoRA adapters) on a labeled corpus of engineering drawings (~5K–10K labeled drawings), quantise to 4-bit (Q4_K_M or Q4_0), deploy via Ollama or vLLM on a single 24GB GPU. Cost estimate: $20–50K data + compute, 6–9 month timeline, success conditional on capability of the base model. This is high-risk: open-source VLMs may not improve enough at the 90B scale to overtake the deterministic pipeline + manual color pick on accuracy.

The split is a business decision (cloud-OK customers vs on-prem-only customers), not a technical one. Both share the same pipeline downstream of detection.

### 5.4 Custom detection model (Year 2 H2)

A purpose-trained object detector for HVAC duct outlines on rendered drawings:

- **Architecture choice:** Faster R-CNN with FPN backbone (mature, calibrated confidence) or YOLOv8/v10 (faster inference). For rendered CAD drawings the speed advantage of YOLO matters less than calibration; lean toward Faster R-CNN.
- **Data:** 1,000–5,000 labeled drawings spanning the conventions observed in the sample set + customer-contributed drawings collected during V3 deployment. Labels per drawing: every duct outline as a polygon, every dim label as a bbox + transcribed text. Estimated labeling cost: $30–80 per drawing for technical labelers, so $30K–400K total depending on corpus size.
- **Training:** ~$5–10K compute on a labeled corpus of 5K drawings. Iterations across architecture, augmentation, and hyperparameters add 3–4 months calendar time.
- **Replaces:** the user color-picking step. Upload → segments come out.
- **Risks:** distribution shift (new customer drawings differ from training corpus), confidence calibration (object detectors notoriously over-confident), and the need to ship V3 deterministic as the fallback when the model says "no detection."

The custom model is a *replacement* for the picker UX, not for the rest of the V3 pipeline. Pressure-class classification, dim attribution, and SMACNA logic continue to live downstream of detection.

---

## 6. Why each pivot was the right call

Defensible reasoning for the architectural decisions, in case future contributors look at the design tree and ask "why didn't they just stay on V1?":

- **V1 → V2 was deferred** because the failure modes V2 addresses (false-positive over-recall, low confidence, small-text legibility) all assume the V1 detector is *imperfect-but-working*. The observed V1 detector wasn't working — it was producing malformed JSON 80% of the time. V2's reviewer loop and tiled detection compound an unreliable signal; they don't fix it.

- **V1 → V3 was the correct pivot** because V3 removes the failure mode entirely. Instead of asking the VLM "where are the ducts?" (which it gets wrong), the user tells the system "ducts are this color" (which is fast and reliable). The downstream pipeline then runs on a deterministic mask. Trade-off: one user click per duct system instead of zero clicks. Win: the pipeline produces real results on drawings 01–03 today.

- **V3 isn't the final form** because it doesn't yet handle parallel-wall ducts (drawings 04–05) and it requires a manual color pick. The roadmap above closes both gaps without breaking V3's core insight (deterministic pipeline downstream of a reliable signal). The custom detection model in §5.3 replaces the manual pick; OCR-anchored Pattern A in §5.1 closes the parallel-wall gap.

- **The user-research interviews in [`synthetic-user-research/`](./synthetic-user-research/) drove the on-prem constraint** that rules out frontier VLMs in V1's primary path. Without that constraint, V1 + Anthropic vision is shippable as-is. With it, V3 is the only viable path today.

---

## 7. Pointers

- **What runs today:** [`implementation/README.md`](./implementation/README.md) — operational details, API, run instructions.
- **V3 architecture:** [`SOLUTION-DESIGN-V3.md`](./SOLUTION-DESIGN-V3.md) — every stage, every regex, every threshold + the rationale.
- **Architectural decisions:** [`adr/`](./adr/) — chronological design decisions, including the V3 pivot rationale (ADR-0011, ADR-0012, ADR-0013), V4 outline detection (ADR-0014, ADR-0015, ADR-0016), and the V4.5 dual-branch + CFM-aware pressure choice (ADR-0017).
- **Why V1 + V2:** [`SOLUTION-DESIGN.md`](./SOLUTION-DESIGN.md), [`SOLUTION-DESIGN-V2.md`](./SOLUTION-DESIGN-V2.md) — kept as design history, not implementation reference.
- **Sample drawings:** [`sample-HVAC/`](./sample-HVAC/) — 5 benchmark drawings spanning the conventions discussed throughout this README.
- **User research:** [`synthetic-user-research/`](./synthetic-user-research/) — the interviews that drove the on-prem requirement and the wedge framing.
- **Competitor scan:** [`competitor-research.md`](./competitor-research.md) — what exists in this space and where this product slots.
