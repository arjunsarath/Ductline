# ADR-0001 — Hybrid detection stack: VLM + OCR + classical CV

**Status:** Accepted, 2026-05-02
**Deciders:** Arjun Sarath
**Build window:** 2 days

## Context

The take-home requires duct detection, dimension extraction, and pressure-class classification on a single-page HVAC drawing. There is no labeled training data and no time to produce any. The competitive read (`competitor-research.md`) shows TaksoAI handles geometry well; the synthetic research (`research-report.html`) reframes the wedge as *reconciliation with reasoning shown*, not raw extraction accuracy.

The system has three distinct sub-problems that are not best solved by the same kind of model:

1. **Geometric duct detection** — find duct runs in line work that includes piping, structural, electrical.
2. **Text extraction** — read dimension callouts and schedule cells, often in non-standard typography, sometimes rotated.
3. **Reasoning** — decide whether a piece of nearby text is a dimension callout for *this* duct vs. a different one, or whether a schedule row applies to *this* system.

A single-model approach (pure VLM, pure OCR, pure CV, pure fine-tuned detector) loses on at least one of these.

## Decision

The detection pipeline is hybrid:

- **VLM (default Claude vision via `VLMClient`)** for duct detection + nearby-text recall, invoked exactly once per drawing through a typed tool (`DetectDuctsTool`). Used because it gives reasonable zero-shot detection without training data and handles the "is this a duct or piping" disambiguation.
- **PaddleOCR** for text extraction — dimension callouts and schedule region. Used because OCR engines beat VLMs on small-text precision, and dimensions follow a strict regex grammar that's easier to apply to OCR strings than to VLM prose.
- **OpenCV (HoughLinesP + parallel-line pairing)** for geometry refinement on top of the VLM's coarse bounding boxes. Used because VLM-returned bounding boxes are geometrically loose; classical CV tightens them deterministically.
- **Deterministic ranked-policy state machine** for pressure-class — never inferred by the VLM (see ADR-0004).

## Consequences

**Positive**
- Each tool plays to its strengths; no single failure mode disables the whole pipeline.
- Only one VLM hop in the hot path → cost predictable (~$0.05/drawing) and latency predictable.
- Failure of the VLM stage degrades to CV-only mode, surfaced to the user — not a hard error.
- The reasoning trace (PRD requirement, research insight) becomes a natural artifact: each segment carries which stage produced which value.

**Negative**
- More moving parts than a single-model solution. Mitigated by named seams in `pipeline/base.py` and a stable contract per stage.
- VLM bounding boxes need refinement to be useful. Accepted; OpenCV is fast and predictable.
- OCR for non-standard fonts may underperform. Mitigated by time-boxed swap to Tesseract or RapidOCR if PaddleOCR fails on the first drawing.

## Alternatives considered

1. **Pure VLM (one call, full JSON).** Fastest to build. Rejected: weak geometric precision, poor signal of system thinking for the assessment.
2. **Pure classical CV + OCR (no VLM).** Most explainable and lowest cost. Rejected: detection breaks on dashed lines and crowded regions where pure CV can't tell ducts from other line work.
3. **Fine-tuned YOLO / Detectron2.** Highest accuracy ceiling. Rejected: no labeled data, no time to label; fine-tune budget alone exceeds the 2-day window.
4. **Open-vocab detector (Grounding DINO / YOLO-World) + OCR + CV.** Strongest architecture story, the "YOLO-flavored" version of this decision. Rejected for v1 due to integration risk in a 2-day window. Documented in README as the planned v1.1 substitution for the VLM detection role.
