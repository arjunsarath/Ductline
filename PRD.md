# HVAC Duct Detection & Annotation System
## Problem Statement & Product Requirements Document (v0.1)

> **Status:** Draft for assessment submission. Business-context-first, high-level technical framing.
> **Next phases:** market research → user research → thesis development → solution validation. Each section flags where those phases will sharpen the definition.
> **Author:** Arjun Sarath
> **Date:** May 2026

---

## 1. Executive Summary

Mechanical engineering drawings are the source of truth for how a building's HVAC system is built, costed, and maintained — yet the information they encode (duct geometry, sizing, pressure class, run lengths) is locked inside a 2D visual artifact that downstream consumers (estimators, fabricators, BIM modelers, facility managers) re-key by hand.

This system reads an HVAC duct-layout drawing (image or PDF), automatically detects duct segments, overlays them with annotations, extracts dimensions and pressure class for each segment, and lets a user click any duct to inspect its metadata.

The take-home brief defines the *capability*. This document defines the *problem* — who needs it, why now, what "well-detected" actually means, what we are deliberately not solving in v1, and the open questions that the next phase of work (market and user research) must answer before scope is locked.

---

## 2. Business Context

### 2.1 Why this problem exists
HVAC drawings sit at the intersection of three workflows that all consume the same data and re-extract it independently:

- **Cost estimation.** Mechanical estimators bid jobs by measuring duct linear footage, counting fittings, and applying pressure-class-dependent labor and material rates. This is largely manual today, often done with on-screen takeoff tools where a human traces every line.
- **Fabrication.** Sheet-metal shops translate drawings into cut lists for sheet metal, with duct dimensions and pressure class driving gauge selection, seam type, and reinforcement spacing per SMACNA standards.
- **As-built / facilities.** Facility teams maintain digital twins or CMMS records of installed ducts; today the link from drawing to inventory is brittle and frequently rebuilt from scratch.

In every workflow, a human reads the same drawing and types the same numbers into a different system. The "drawing → structured duct data" extraction is the bottleneck.

### 2.2 Why now
Three trends make this tractable as a product, not just a feature inside a CAD tool:

- General-purpose vision models (object detection, segmentation, document VLMs) have crossed the threshold where structured information can be extracted from technical drawings without per-customer model training.
- BIM mandates (e.g., ISO 19650, federal/public-sector requirements in multiple geographies) increasingly require structured digital deliverables alongside drawings, expanding willingness to pay for extraction tools.
- Cloud OCR and LLM costs have dropped to a point where per-drawing economics work even for sub-enterprise customers.

### 2.3 Where this product would sit
Adjacent or competing categories, to be validated in the market-research phase:
- **Construction takeoff software** (e.g., PlanSwift, STACK, Bluebeam Revu measurements). Heavy in workflow, light in automation.
- **Drawing-extraction startups** in the AECO (architecture, engineering, construction, operations) space that target structural, electrical, or plumbing extraction. HVAC-specific extraction is less commoditized.
- **CAD-vendor add-ons** (Revit / AutoCAD MEP) that work on native CAD files but not on the PDFs/scans that move between firms.

> **Open business questions** (resolved in market research, §11)
> - Which buyer pays first — estimators, fabricators, or BIM consultants?
> - Is willingness-to-pay per-drawing, per-seat, or per-project?
> - Do incumbents already solve "good enough" for the highest-value segment?

---

## 3. Problem Statement

A mechanical engineer (or anyone downstream of one) opens an HVAC duct-layout drawing in a PDF or image. They need a structured list of every duct segment with its dimension and pressure class, and they need to be able to verify each extraction by seeing it overlaid on the original drawing.

Today they get this by reading the drawing manually — slow, error-prone, and not reusable. The cost of *not* solving this is hours of skilled labor per drawing, repeated across every workflow that consumes the drawing, multiplied by the thousands of HVAC drawings produced per major project.

This document scopes a v1 system that does the extraction, displays it interactively, and surfaces what it could not extract confidently — leaving the human in the loop where automation is unreliable.

---

## 4. Target Users & Personas

The take-home brief is silent on the user. The PRD names three plausible personas; user research (§11) will confirm which is the v1 design partner.

### 4.1 Primary persona — "Eli, the mechanical estimator"
Bids HVAC scopes from PDF drawing sets. Lives in takeoff software. Measures linear feet of duct by size, counts fittings, applies SMACNA labor rates. Pain: time-per-bid is the limiter on how many bids his shop can win; takeoffs from cluttered or scanned drawings eat hours.

### 4.2 Secondary persona — "Priya, the fabrication engineer"
Translates drawings into sheet-metal cut lists. Cares about pressure class because it determines gauge and reinforcement. Pain: drawings sometimes ambiguous on pressure class (carried in title block notes, not on the duct itself); errors here are expensive.

### 4.3 Tertiary persona — "Marcus, the BIM coordinator"
Builds or audits digital models from contract drawings. Pain: reconciling what was drawn vs. what was modeled; needs structured duct data for clash detection and as-built records.

> **Open user questions** (resolved in user research, §11)
> - Whose workflow today does this most disrupt? Whose fits naturally?
> - Do users want a standalone tool or an extraction layer that exports to their existing tools (Revit, Excel takeoffs, Procore)?
> - What level of accuracy must we hit before they trust the output without a full re-check?

---

## 5. Goals & Non-Goals

### 5.1 Goals
1. **Detect duct segments on a single-page HVAC plan-view drawing with high recall** — under-detection is more costly than false positives because a missed duct silently disappears from the takeoff, while a false positive gets dismissed by the user.
2. **Annotate every detected duct visibly** on the original drawing so the user can verify at a glance what was found.
3. **Extract dimension text** (e.g., `14"⌀`, `10" x 8"`) and associate it with the correct duct segment.
4. **Classify pressure class** for each segment (Low / Medium / High) using the most reliable signal available on the drawing — explicit annotation if present, schedule lookup if a schedule is detected, otherwise a heuristic with a confidence flag.
5. **Provide an interactive UI** where clicking any annotated duct surfaces its dimension and pressure class in under 200 ms.
6. **Communicate uncertainty** — every extracted value carries a confidence indicator so the user knows where to verify.

### 5.2 Non-goals (v1)
1. **No support for multi-page drawing sets.** Single-page input only. *Why:* multi-sheet correlation (matchlines, key plans, schedules on different sheets) is its own problem; v1 establishes the per-sheet primitive.
2. **No fitting-level extraction (elbows, tees, transitions, dampers, diffusers).** Detection is at the duct-run / segment level. *Why:* fitting recognition multiplies the label space and benefits from a fitting-specific model; deferred to v2.
3. **No native CAD-file (DWG/RVT) parsing.** Image and PDF only. *Why:* the user value is greatest where structured CAD is unavailable; supporting CAD competes with vendor tools and reduces the differentiation.
4. **No editing of detections.** Read-only inspection in v1. *Why:* a human-in-the-loop edit/approve workflow is a meaningful design problem on its own; v1 demonstrates extraction quality, v2 adds correction.
5. **No export (CSV / IFC / Revit roundtrip).** *Why:* export format choice depends on the buyer persona, which user research has not yet confirmed.
6. **No 3D reconstruction or volumetric analysis.** Plan-view 2D only.

---

## 6. User Stories

Ordered by priority. Persona in brackets.

1. **[Eli]** *As an estimator, I want to upload a duct-layout PDF and see every duct outlined on the drawing within a minute, so I can confirm at a glance the system found the runs I care about.*
2. **[Eli]** *As an estimator, I want to click any annotated duct and see its dimension and pressure class immediately, so I can spot-check the extraction against what I'd otherwise read manually.*
3. **[Priya]** *As a fabrication engineer, I want every duct segment labeled with its pressure class and a confidence indicator, so I know which segments I still need to verify against the title-block notes.*
4. **[Eli]** *As an estimator, I want clearly visible markers when the system was uncertain about a dimension or pressure class, so I don't silently inherit a wrong number into my bid.*
5. **[Marcus]** *As a BIM coordinator, I want to know the system's per-segment confidence so I can prioritize which areas of the model to manually reconcile.*
6. **[Eli]** *As an estimator, when the drawing is a low-quality scan, I want the system to tell me upfront that detection quality may be reduced, so I plan my time accordingly.*

---

## 7. Functional Requirements

### 7.1 Must-Have (P0)

| ID | Requirement | Acceptance criteria |
|----|------------|---------------------|
| F-01 | Drawing upload | User can upload a PDF (single-page) or image (PNG/JPG). System validates file type and surfaces a clear error otherwise. |
| F-02 | Duct detection | System identifies duct-run segments in the drawing and produces a list of detections with bounding geometry (polylines or boxes). |
| F-03 | Annotation overlay | Every detected duct is rendered on top of the original drawing with a visible marker. The original drawing remains legible underneath. |
| F-04 | Dimension extraction | For each detected duct, the system extracts the associated dimension text (round `NN"⌀` or rectangular `WW" x HH"`) where present on the drawing. |
| F-05 | Pressure class classification | For each detected duct, the system assigns Low / Medium / High pressure class with a confidence indicator. (See §8 — open question on derivation.) |
| F-06 | Click-to-inspect | Clicking any annotated duct shows the duct's dimension and pressure class within 200 ms. |
| F-07 | Uncertainty signal | Each extracted value is rendered with a confidence indicator (e.g., color, badge). Low-confidence extractions are visually distinct. |

### 7.2 Nice-to-Have (P1)
- Per-segment "why" panel (which OCR string, which heuristic, which model output produced this answer) — supports trust and debugging.
- Aggregate summary (total segments, count by pressure class, total linear footage estimate at drawing scale).
- Side-by-side view: original drawing left, extracted table right.

### 7.3 Future Considerations (P2)
- Multi-page drawing sets with cross-sheet schedule and matchline resolution.
- Fitting and accessory detection (elbows, tees, dampers, diffusers).
- CAD-native parsing (DWG, RVT, IFC) as a parallel ingestion path.
- Edit / approve workflow for human correction of extractions.
- Export to CSV, Revit, IFC, Procore.
- API for integration with takeoff or BIM tools.

---

## 8. High-Level Solution Architecture

> Stack direction: Python backend, React frontend. Hybrid extraction approach — classical CV + ML detection + LLM/VLM reasoning + OCR. Implementation depth is deliberately left for the build phase; this section names the seams, not the libraries.

The system has five logical stages:

1. **Ingest & normalize** — accept PDF or image, rasterize PDFs to a working DPI, normalize orientation, detect drawing scale and the title-block region.
2. **Detect duct geometry** — identify duct runs in the drawing. Hybrid signal:
   - Classical CV (line detection, parallel-line pairing, contour analysis) for the bulk of clean line work.
   - An ML detection / segmentation model to handle dashed lines, broken segments, and crowded regions.
   - VLM as a backstop for ambiguous regions and to disambiguate ducts from non-duct line work (piping, structural, electrical).
3. **Extract metadata** — for each detected duct:
   - OCR over the immediate neighborhood to capture dimension callouts.
   - Title-block / schedule region OCR to capture global pressure-class notes.
   - Heuristic association of nearby text → owning duct segment (proximity + leader-line tracing).
4. **Classify pressure class** — ranked-confidence policy:
   - **High confidence:** explicit annotation on or adjacent to the duct ("LOW PRESSURE", "MED. PRESS.", etc.).
   - **Medium confidence:** schedule or title-block note that applies to the system the duct belongs to.
   - **Low confidence (heuristic fallback):** inferred from duct dimension + assumed velocity range per SMACNA. Always flagged as low confidence.
5. **Serve & render** — backend exposes a per-drawing JSON payload (segments, geometry, extractions, confidences); frontend renders the original drawing with an SVG overlay of the detected geometry and a click-to-inspect popover.

Data model — minimal viable shape, to be sharpened during build:
```
Drawing { id, source_file, width_px, height_px, scale_hint, title_block_region }
Segment { id, drawing_id, geometry, dimension { value, shape, source, confidence },
          pressure_class { value, source, confidence } }
```

---

## 9. Success Metrics

### 9.1 Leading indicators (measurable on a small benchmark drawing set within the build)
- **Duct detection recall** on a held-out benchmark (target: ≥85% recall on canonical CAD-quality plan views; ≥70% on scanned drawings).
- **Dimension extraction accuracy** (target: ≥90% exact-match on detected segments that carry a visible callout).
- **Pressure-class classification accuracy** when an explicit annotation exists (target: ≥95%).
- **End-to-end latency** for a typical single-page drawing (target: ≤30 s P50, ≤90 s P95).
- **Click-to-inspect responsiveness** (target: ≤200 ms render of the popover).

### 9.2 Lagging indicators (out of scope to measure in the take-home, named for completeness)
- Time saved per takeoff vs. manual.
- Trust score: % of extractions accepted without correction by the user.
- Drawings processed per user per week after onboarding.

### 9.3 Evaluation methodology
The take-home assessment will be evaluated on a small set of drawings provided or sourced. The README will document the chosen benchmark, the failure modes observed, and the gap between observed accuracy and the targets above.

---

## 10. Edge Cases & Risks

Edge cases the v1 must handle gracefully (even if not perfectly):

- **Scanned drawings with skew, noise, or compression artifacts.** Detection quality degrades; system should warn the user.
- **Hand-drawn or marked-up drawings.** Out of scope for accuracy; system should still attempt detection and clearly communicate low confidence.
- **Drawings where pressure class is encoded only in the title block / schedule** and not adjacent to ducts. This is the *expected* case; the architecture in §8 handles it via the schedule-OCR path.
- **Drawings with no pressure class information at all.** System should fall back to the heuristic and label everything low confidence. It must not silently invent a class.
- **Drawings with mixed systems** (supply / return / exhaust) where pressure class differs by system. The drawing identifies the system; the title-block path must respect the system mapping.
- **Round vs. rectangular vs. flat-oval ducts.** All three should be detected and dimensioned. Flat-oval is least common and acceptable to defer.
- **Non-HVAC line work in the same drawing** (structural grid, piping, electrical). False-positive risk — the system must use context (line patterns, line weights, callout vocabulary) to filter.
- **Multiple ducts intersecting at a fitting.** Segment boundaries should break at fittings even if fittings themselves aren't classified.
- **Very small or very large drawings** (8.5×11 single-zone diagrams vs. E1 floor plans). Coordinate handling and DPI selection must be robust.

Risks:
- **Pressure-class derivation is ill-defined by the brief.** This is the single largest scoping ambiguity. See §11 — open questions.
- **No labeled training data.** The build will rely on pretrained models and heuristics; ML-fine-tuning is out of scope for the take-home.
- **Drawing variability across firms is large.** Demonstrating robustness on one drawing set does not guarantee generalization. The README will be explicit about this.

---

## 11. Open Questions

Each is tagged with the phase that owns the answer.

### 11.1 Blocking — must answer before scope is locked
- **[product / brief author]** What is meant by "duct" — a continuous duct run, a segment between fittings, or any straight piece of duct line work? *v1 assumption: segment between fittings or between a fitting and a terminal device.*
- **[product / brief author]** How is pressure class to be derived when the drawing does not annotate it on each duct? Is the heuristic-from-dimension fallback acceptable, or should the system refuse to classify and surface "unknown"? *v1 assumption: ranked-confidence policy in §8.4 with explicit low-confidence labeling.*
- **[product]** Is the user expected to upload one drawing at a time, or batch-upload? *v1 assumption: one at a time.*

### 11.2 Non-blocking — resolve in market & user research
- **[market research]** Which buyer persona (estimator / fabricator / BIM) has the highest willingness to pay and the most acute pain?
- **[market research]** What do incumbent takeoff and extraction tools already do well, and where is the genuine gap?
- **[user research]** What confidence threshold do users need before they stop double-checking extractions?
- **[user research]** Standalone tool, or extraction layer feeding existing tools (Revit, Excel, Procore)?
- **[thesis development]** Is the wedge "general drawing extraction with HVAC as the first vertical" or "HVAC-only depth"?
- **[solution validation]** What is the minimum-viable accuracy bar at which a paid pilot is achievable?

### 11.3 Engineering open questions (resolve during build)
- **[engineering]** Which OCR engine handles engineering-drawing typography (often non-standard fonts, rotated text) best out of the box?
- **[engineering]** Is a single VLM call per drawing economically viable, or does the architecture need a hybrid budget (CV first, VLM only for ambiguous regions)?
- **[engineering]** What's the right working DPI for raster pipelines — high enough for OCR on small callouts, low enough for latency?

---

## 12. Timeline & Phasing (assessment scope)

The take-home is sized for 2–4 days of work. Suggested phasing:

- **Day 1 — framing and scaffold.** This document. Repo skeleton (FastAPI backend, React frontend, shared schema). Pick benchmark drawing(s).
- **Day 2 — extraction pipeline.** Ingest, classical-CV detection, OCR, naive pressure-class. End-to-end through to a JSON payload.
- **Day 3 — UI and interaction.** Overlay rendering, click-to-inspect, confidence visualization. Iterate detection on observed failure modes.
- **Day 4 — polish, documentation, demo.** README with assumptions/limitations, architecture diagram, demo video, edge-case writeup.

After the assessment — the next phases that this PRD explicitly hands off to:
1. **Market research.** Validate buyer persona, willingness to pay, competitive landscape (§2.3, §11.2).
2. **User research.** Interview 5–10 people across the three personas; resolve standalone-vs-embedded and accuracy-bar questions (§4, §11.2).
3. **Thesis development.** Sharpen the wedge: HVAC depth vs. drawing-extraction breadth (§11.2).
4. **Solution validation.** Paid-pilot test against the highest-confidence persona on real drawings (§11.2).

---

## 13. Glossary

- **Pressure class** — Per SMACNA HVAC Duct Construction Standards, the static-pressure rating a duct is built to. Common buckets: Low (≤2" w.g.), Medium (>2" to 6" w.g.), High (>6" w.g.). Determines gauge, seam type, reinforcement.
- **SMACNA** — Sheet Metal and Air Conditioning Contractors' National Association; sets the canonical North American duct-construction standards.
- **Title block** — The standardized region of a drawing (typically lower-right) carrying project metadata, sheet number, scale, revision, and notes including pressure class.
- **Schedule** — A tabular region of a drawing summarizing equipment / system properties (e.g., "Duct schedule" giving size and class per system tag).
- **Plan view** — Top-down 2D view of the layout. The brief's primary target.
- **Takeoff** — The process of measuring quantities (linear feet, fittings, areas) from a drawing for cost estimation.
- **VLM** — Vision-language model; a multimodal model that reads images and returns text/structured output.

---

*This is v0.1 of the problem statement. It deliberately defines the problem in more detail than it defines the solution — well-defined problems are halfway-solved problems.*
