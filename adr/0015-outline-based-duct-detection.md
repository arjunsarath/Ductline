# ADR 0015 — Outline-based duct detection replaces V3 color-driven detection on the V4 path

**Status:** Accepted
**Date:** 2026-05-06
**Related:** ADR-0011 (V3 pivot to color-driven), ADR-0012 (dark-line band), ADR-0014 (V4 scope)

---

## Context

V3's detection assumes each duct system is rendered as a saturated colored region (Pattern B closed outline + flood-fill, Pattern C colored centerline + dilate) or, with ADR-0012's dark-line band, as a closed black outline on a faded grey background (drawing 02). The user identifies the system by clicking its color; the HSV mask + flood-fill does the rest.

`testset2.pdf` (the drawing surfaced in post-submission feedback) breaks this assumption directly:

- Ducts are drawn with **black outlines, no fill** — neither colored fill nor closed dark-fill silhouette.
- Architectural background is grey (covered by A12 / preprocessing strip), but the duct interior after strip is **white**, identical to every other empty region of the page.
- HSV color picking has nothing to bind to. A black-outline pick selects every black line on the page (text, grid, equipment, the title block).
- Flood-fill from inside the duct interior leaks across whatever the duct connects to — there is no perimeter loop to contain it.

Concretely: V3's `addPick` → `defaultBand` / `darkBand` → `fill_outline` chain produces 0 segments on `testset2.pdf`. The ADR-0012 dark-line path (which fixed drawing 02) does not generalize because drawing 02's ducts are *closed dark loops*; `testset2.pdf`'s ducts are *open dark lines forming a corridor*.

V4 needs duct geometry without depending on color or on closed dark fills.

## Decision

On the V4 path, detection becomes **outline-based**: identify duct polygons by their explicit shape geometry (the two long parallel lines forming each duct corridor), bounded at their ends by the perpendicular cross-cut bars defined in A6 ([`../SOLUTION-DESIGN-V4.md`](../SOLUTION-DESIGN-V4.md) §2).

Implementation lives in:

- `backend/app/cv/duct_outline.py` — outline polygon detection. Edge detection on the post-strip raster; HoughLinesP for line segment extraction; pair near-parallel lines within plausible duct-gap distance into corridor candidates.
- `backend/app/cv/crosscut.py` — perpendicular bar detection. Each corridor candidate is closed at its ends by short cross-cut bars (A6); detection looks for short line segments perpendicular to corridor axis at both ends.
- `backend/app/cv/connectors.py` — transitions / elbows / tees / equipment boxes that link corridors.

The pipeline does not pick a duct color from the user. The user identifies the *page* (A15) and the title-block scale; the outline algorithm runs unprompted.

The V3 color-driven path is **retained as a fallback** ([`../SOLUTION-DESIGN-V4.md`](../SOLUTION-DESIGN-V4.md) §1 status header) until V4 is validated end-to-end on both the legacy benchmark drawings and `testset2.pdf`. Drawings 01 / 03 (saturated colored fills) and 02 (closed dark outline, ADR-0012) continue to flow through V3 if the V4 outline detector under-recalls on them during validation.

## Why this is the right call

1. **Matches the actual drawing convention.** Mechanical plans render ducts as two parallel walls bounded by cross-cut bars. That's the geometric primitive. Color is a stylistic overlay that happens on some drawings, not the underlying signal. Detecting the primitive directly is the right level of abstraction.
2. **Removes the manual pick step.** ADR-0011 traded VLM unreliability for a one-click-per-system manual pick. ADR-0015 trades the manual pick for a deterministic shape detector. No regression in user friction; one less step.
3. **Handles A12 cleanly.** Grey architectural fill is stripped during preprocessing, leaving ducts as the only structured line geometry remaining. The outline detector then has a clean signal to operate on.
4. **Two-line-pair constraint is the discriminator.** This is the same insight that drove the deferred Pattern A design (ADR-0013 §"OCR-anchored Pattern A"): random narrow corridors are everywhere on a CAD plan; *paired* parallel lines bounded by cross-cuts are not. V4 generalizes that insight as the primary detector rather than as a per-pattern fallback.
5. **Composes with the V4 graph build.** Cross-cut bars (A6) are the natural segment boundary, so corridor + bar detection produces nodes-and-edges directly without a separate segmentation pass.

## Consequences

**Positive:**
- `testset2.pdf` becomes detectable; a category of drawings that V3 cannot handle at all is brought in scope.
- The detection step has no user-facing "pick a color" UI on the V4 path. Result: fewer UI states, fewer rejection cases (ADR-0012's click-rejection rules become V3-only).
- Outline detection is colour-agnostic, so future drawings with novel color schemes don't require new HSV bands.

**Negative:**
- Outline detection requires explicit shape geometry. Drawings that violate A6 (no perpendicular cross-cut bars) produce merged segments. The flag-for-review path absorbs this; the user can split segments manually.
- HoughLinesP recall on dense plans is the same risk that contributed to V1's CV-fallback over-detection (ADR-0011 context). V4 mitigates this with the **paired-line + cross-cut-bar** constraint, which is much stricter than HoughLinesP alone, but it is not free of false positives. The validation step on the full benchmark must confirm under-recall doesn't replace V1's over-recall.
- Two parallel implementations of the detection stage (V3 color-driven, V4 outline) exist during the validation window. [`../SOLUTION-DESIGN-V4.md`](../SOLUTION-DESIGN-V4.md) §8 is explicit that this is temporary; once V4 covers the legacy drawings, V3 detection moves to `implementation/archive/v1-v3/`.

**Neutral:**
- The ADR-0011 framing — "deterministic CV beats unreliable VLM" — still holds. V4 is more deterministic, not less.
- ADR-0012's dark-line band and ADR-0013's deferred Pattern A both sit on the V3 detection path; they remain accurate for that path and become moot for the V4 path once outline detection is validated.

## Alternatives considered

- **Extend V3's color thresholds to cover `testset2.pdf`.** Rejected. The grey architectural fill in `testset2.pdf` is itself non-colored, so widening the HSV band to catch black-outline ducts also catches building outlines, equipment, text, and grid lines. The faded-grey rejection rule (ADR-0012) cannot save this because the duct outline and the non-duct outlines sit at the same brightness.
- **Use the OCR-anchored Pattern A design from ADR-0013 as the V4 primary detector.** Considered. Pattern A's approach (window around each dim label, find the parallel-line pair locally) is the right shape but has a hard dependency on dim labels existing — A1 says they do, but unlabeled segments (A9) need to be detected too. V4's outline detector runs page-wide, then attributes labels; Pattern A becomes a confidence booster on labeled segments rather than the only detector.
- **Train a lightweight detection model on outline ducts.** Rejected for V4 MVP for the same reason as ADR-0011: training data isn't available, and the deterministic shape detector clears the bar without it. Belongs after V4 ships, as the M21+ trained-detector replacement.
- **Frontier VLM on `testset2.pdf`.** Same on-prem objection as ADR-0011; not a default. Available behind the parked VLM seam for cloud-OK customers.

## What this means for existing ADRs

- **ADR-0011** — V3 pivot remains valid for the V3 path. V4 supersedes V3 *only on the V4 path*; V3 stays current for color-fill drawings until V4 is validated on them.
- **ADR-0012** — Dark-line band stays valid for the V3 path (drawing 02). On the V4 path, brightness-only HSV logic is replaced by outline geometry; the dark-line-pick concept is retired only when V3 retires.
- **ADR-0013** — Pattern A deferral remains the V3 story for drawings 04 / 05. V4's outline detector is the production answer to the same problem and obsoletes the OCR-anchored Pattern A design described in that ADR (the local-window logic is preserved as a confidence booster for labeled segments).

## References

- [`../SOLUTION-DESIGN-V4.md`](../SOLUTION-DESIGN-V4.md) §3 (pipeline), §4 (`backend/app/cv/duct_outline.py`, `crosscut.py`), §2 A6 / A12 (assumption basis).
- ADR-0013 §"OCR-anchored Pattern A" — the design that informed V4's paired-line constraint.
