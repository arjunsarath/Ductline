# ADR 0014 — V4 scope lock: single-page MVP and the 15 drawing assumptions

**Status:** Accepted
**Date:** 2026-05-06
**Related:** ADR-0011 (V3 pivot), ADR-0012 (dark-line band), ADR-0013 (Pattern A deferral)

---

## Context

Post-submission feedback added two requirements on top of V3:

1. Run on `testset2.pdf` (a drawing the V3 color-driven path cannot detect — see ADR-0015).
2. Detect and display **duct run lengths** in addition to dimensions.

The original brief also asked for **CFM** and **pressure (value + class)**, both of which `testset2.pdf` exposes cleanly (terminal symbols carry CFM; pressure is a function of CFM, geometry, and SMACNA tables). V4 picks those up rather than deferring them again.

V4 needs an explicit scope boundary. Without one, the same drift that bloated V1 (general-purpose detector, no convention assumed) recurs: every edge case in every drawing becomes a feature request, the pipeline grows knobs, and nothing gets validated.

## Decision

V4 MVP is locked to single-page mechanical plans with the following 15 conventions assumed. Any drawing that violates an assumption is out of scope for the MVP and is documented as such. The assumptions are surfaced to the user in the UI and the README, not buried.

The full list (verbatim from [`../SOLUTION-DESIGN-V4.md`](../SOLUTION-DESIGN-V4.md) §2):

- **A1.** Dimension labels live **inside** the duct fill (one label per segment).
- **A2.** Only **one** numeric token lives inside a duct interior.
- **A3.** Rectangular duct labels are always **`WxH`** (width × height).
- **A4.** No insulation/double-line wrap pattern. If encountered, the **inner bbox** is the duct.
- **A5.** Air terminal symbol = circle with horizontal divider; top half = type letter (ignored in MVP), bottom half = numeric **CFM**.
- **A6.** A **segment** is a region bounded by two perpendicular cross-cut bars at its ends. Transitions, elbows, tees, Y-branches, and equipment boxes are **connectors**, not segments.
- **A7.** Solid-touching ducts are **connected**. Dashed rendering = duct passes underneath; logically a single segment, displayed with alpha overlay.
- **A8.** Drawing is to scale. Label text is axis-aligned (0° or 90°), never angled.
- **A9.** Unlabeled segments are sized by **direct pixel measurement × scale**, not by inheritance.
- **A10.** A single segment can host N air terminals along its length.
- **A11.** Connector materials (rigid vs flex) are treated as a generic connector with a default equivalent length the user can override.
- **A12.** Grey-shaded regions are non-HVAC architectural fill and are **stripped** during preprocessing.
- **A13.** Open-ended ducts have no airflow unless tagged with a terminal or user-entered CFM.
- **A14.** All CFM values for MVP are read from terminal symbols, not plan-note prose.
- **A15.** Single-page PDF only. The user picks the page on upload if the source has more than one.

The pipeline implementation is allowed to assume these. Where a drawing satisfies an assumption only partially, the segment is flagged for review rather than silently bypassed.

## Why this is the right call

1. **The assumptions are taken from real industry conventions, not invented.** A1–A10 reflect SMACNA / ASHRAE plan conventions; A12 reflects standard architectural shading; A8 reflects CAD layout norms. The MVP is "drawings that follow the conventions," not "drawings shaped to make the algorithm work."
2. **Visible assumptions beat hidden heuristics.** Listing A1–A15 in the UI lets the user see immediately whether their drawing fits the MVP. If it doesn't, no engineering time is wasted debugging detection failures that were predictable from the conventions.
3. **Bounds the V3→V4 refactor.** With the assumption set fixed, the modules in [`../SOLUTION-DESIGN-V4.md`](../SOLUTION-DESIGN-V4.md) §4 have a clear contract. Module size limits (§8) become enforceable because nothing creeps in to handle out-of-scope cases.
4. **Composes with the deferred-items list.** Multi-page, oval ducts, plan-note CFM, equipment semantics — every deferred item has an explicit assumption it would relax (A15, A3, A14, A11). The deferral path is mechanical, not an open question.

## Consequences

**Positive:**
- A drawing that satisfies A1–A15 has a deterministic path through V4. No detection guessing.
- The assumption list is the user's mental model of "what kind of drawing this works on" — onboarding cost is low.
- Tests can be written against the assumptions: each is a precondition with a known-good and known-bad fixture.

**Negative — what breaks if an assumption is violated:**
- **A1 / A2 violated** (label outside the fill, or multiple numeric tokens inside): label-to-segment attribution becomes ambiguous. Segment is flagged unlabeled; user must enter the dimension.
- **A3 violated** (`WxH` written as `W"H"` or with units interleaved): OCR regex misses the label. Falls through to A9 pixel-width sizing.
- **A4 violated** (insulated ducts with double outline): outline detection picks the outer boundary; reported width overshoots by ~2× insulation thickness. Out of MVP.
- **A5 violated** (linear bar diffusers, custom symbols): terminal not detected, CFM at that point is zero. User can enter CFM manually per A13's escape hatch.
- **A6 violated** (no perpendicular cross-cut bars between size changes): segments merge across transitions. Length is correct; per-segment dimension/CFM is wrong.
- **A7 violated** (crossings drawn as solid lines, not dashed): topology builds the wrong graph; flow trace is wrong.
- **A8 violated** (angled labels): OCR rotation handling is not built; labels read as gibberish.
- **A9 violated** (drawing is NTS / schematic): pixel-width sizing produces nonsense; titleblock scale read fails or is absent.
- **A12 violated** (architectural fill shares the duct color or sits at the same brightness): preprocessing strip removes ducts along with fill, or vice-versa.
- **A14 violated** (CFM in plan notes only, no terminal symbols): no terminals → no CFM → no pressure trace.
- **A15 violated** (multi-page set without explicit page pick): user picks the wrong page; no error, just wrong output.

In every case the failure mode is documented and surfaced as a flag in the result UI, not a silent miss.

**Neutral:**
- Future work (oval ducts, plan-note CFM, multi-page, equipment semantics) is not blocked by this ADR; each relaxes a specific assumption with a corresponding implementation cost.

## Alternatives considered

- **Multi-page support in V4 MVP.** Rejected. Multi-page introduces sheet-to-sheet continuation references (`see M3.0`), which is its own detection problem and out of proportion with the rest of V4. A15 + a page-picker is a 1-day cost; multi-page is multi-week.
- **Plan-note CFM parsing.** Rejected for MVP. Parsing prose like "supply 1200 CFM to dining room" is an NLP problem; terminal-symbol CFM is a CV problem the rest of the pipeline already handles. Belongs after V4 ships — relaxes A14.
- **Equipment-type semantics (VAV / FPB / AHU as flow modulators).** Rejected. Modeling VAV/FPB modulation requires per-equipment data sheets and runtime config that aren't in the brief. A11 treats all connectors generically; the deferred path is to specialize specific equipment classes once the generic case is validated.
- **No assumption list at all (V1 posture: handle anything).** Rejected — that's the path V1 was on. Without explicit conventions, every detection failure looks like a bug rather than an out-of-scope drawing, and engineering time gets spent in the wrong place.

## References

- [`../SOLUTION-DESIGN-V4.md`](../SOLUTION-DESIGN-V4.md) §1 (scope), §2 (assumptions A1–A15), §10 (deferred), §11 (acceptance).
- ADR-0015 covers the detection switch driven by `testset2.pdf`'s outline-only rendering.
- ADR-0016 covers the SMACNA pressure-class assignment that A6's per-segment boundary makes well-defined.
