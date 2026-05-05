# ADR 0013 — Pattern A (parallel-wall ducts) deferred to OCR-anchored implementation

**Status:** Accepted (decision: defer); supersedes the structural-mask approaches investigated in this round
**Date:** 2026-05-04
**Related:** ADR-0011 (V3 pivot), ADR-0012 (dark-line band)

---

## Context

Drawings 04 (`asc2018-bid-set`) and 05 (`federal-attachment`) use a duct convention that V3 doesn't yet handle correctly: **parallel-wall ducts** — two unconnected black lines forming the duct boundaries, with the dimension label written inline along or inside the gap. There is no closed-outline loop for `fill_outline` to flood, no colored centerline for `thicken_centerline` to dilate cleanly, and no consistent topological signal that distinguishes a duct corridor from any other narrow white space (between text rows, around equipment, room divisions).

V3's existing patterns:

- **Pattern B** (closed colored outline → flood-fill interior): works on drawings 01, 03, and on drawing 02 because drawing 02's ducts are *closed* dark outlines.
- **Pattern C** (colored centerline → dilate): works on drawings where there's a single dominant centerline.
- **Pattern A** (parallel walls): designed in [`SOLUTION-DESIGN-V3.md`](../SOLUTION-DESIGN-V3.md) §6.1 but **not implemented**.

The user-visible symptom on drawings 04, 05: centerline mode (the closest existing pattern) attributes dim labels correctly to duct centerlines but the *pixel width measurement* is the dilation thickness (~5–7 px), not the duct gap. `dim_confidence: high` from centerline mode is therefore not a real signal — calibration converges on a self-consistent but bogus ppu, and most labels read as `high` by tautology.

## Investigation summary (recorded so we don't repeat it)

Four structural-mask approaches were prototyped in the V3 alpha cycle. **All failed on drawings 04, 05** for the same root reason: dense CAD plans have narrow white gaps everywhere — between text rows, equipment internals, hatching, room divisions — so "narrow white corridor" is not a sufficient discriminator.

| Approach | Result on drawing 05 | Why it failed |
|---|---|---|
| **DT-threshold** of inverted mask (white pixels at half-width 5–100 px) | 12M pixels lit up out of 35M total | No constraint on "between *two* parallel walls" — any white pixel near a single wall qualifies |
| **Isotropic tophat** with elliptical kernel (size 80–180 px) | 7–10M corridor pixels | Catches all narrow gaps regardless of structure, including text-row gaps |
| **Directional tophat** (vertical kernel for horizontal corridors, horizontal kernel for vertical) | 9–18M corridor pixels | Same noise problem at smaller scale |
| **OCR-seeded flood-fill** (start flood from each dim label's center on the inverted mask, accept if fill area < threshold) | 39/169 labels attributed (23%); 130 rejected for fill leaking into open rooms | Many labels sit *above* the duct, not *inside* — flood-fill from the label center expands into the room, not the duct corridor |
| **1200 DPI with same outline + filters** | 0 segments — same outcome as 600 DPI | At 1200 DPI the building outline is still fully closed; flood-fill from the page corner still leaks across the whole plan and the blob filter drops the resulting 109M-pixel component |

Each POC took 1–3 hours and is documented in commit history. The conclusion is the same: **mask-only Pattern A detection on plans this dense isn't going to work without sophisticated parallel-pair detection**.

## Decision

Defer Pattern A implementation to the OCR-anchored approach described below. Mark drawings 04, 05 as **partial coverage** in the V3 alpha (centerline mode produces correct attributions but not trustworthy widths). Be explicit in the documentation and in the result UI that confidence flags from centerline-mode segments cannot be relied on.

The shipping V3 stays as it is. Pattern A becomes M1–M2 in the production timeline (see [`../README.md` §4`](../README.md)).

## The OCR-anchored Pattern A design (M1–M2)

**Insight:** the dim labels are reliable. OCR finds them with high accuracy (162 dim_rect tokens on drawing 05). The labels tell the algorithm *where* the ducts are. The structural-mask part of the algorithm only needs to handle *local* geometry around each label — not separate ducts from non-ducts globally.

**Algorithm:**

```
For each dim_rect token:
    1. Window = ±150 px around bbox center on the rendered black mask
    2. Edge detection (Canny) on window
    3. HoughLinesP on edges → candidate line segments with (angle, distance, length)
    4. Cluster lines by angle within ±5° → find dominant orientation
    5. For each pair of near-parallel lines:
       - Same angle (within ±3°)
       - Perpendicular distance in plausible duct-gap range (5–150 px)
       - Both lines have length > min_wall_length
       Score the pair by closeness-of-parallel + label-position-between-walls.
    6. Pick the highest-scoring pair → its perpendicular gap = pixel duct width
    7. Compare to OCR-said-dim × global-ppu for confidence:
       - In ±15% of expected → high confidence
       - In ±30% → medium
       - Otherwise → low
```

This works because:

1. **Locality.** The algorithm only needs to handle ~150 px windows, not whole-plan structure.
2. **Anchored to OCR.** Random "narrow corridors" elsewhere on the page don't get analysed because there's no token there.
3. **Two-wall constraint.** Unlike the failed mask-only approaches, this requires *two* parallel lines, which is the actual discriminator between a duct corridor and a coincidental narrow gap.
4. **Trustworthy widths.** The pixel width is a real geometric measurement, so calibration and confidence flags become real signals.

**Estimated effort:** 2–3 weeks of focused implementation + 1 week of iteration on the benchmark. Drawing 03 regression must remain green throughout.

## Consequences of deferral

**Negative (current state):**
- Drawings 04, 05 ship as "partial" — labels attributed but widths bogus. Users need to know not to trust centerline-mode confidence.
- Documentation needs to be explicit about the limitation (this ADR + readme + result-UI affordance).

**Positive (M1–M2 path):**
- The OCR-anchored design is bounded and tractable. No open research.
- It composes with everything in V3 — the pipeline structure doesn't change, only the per-pattern fill stage gets a new branch.
- It doesn't depend on any custom training data or labeled corpus, so it ships independently of the data-collection roadmap.

## Alternatives considered

- **Ship a structural-mask hack and label it experimental.** Tried four structural-mask approaches. None produced clean enough results to ship even as experimental. False-positive rate would erode user trust faster than no-detection-at-all.
- **Skip ahead to a custom detection model.** That's the M21+ timeline (~Year 2). It needs labeled training data we don't have. Pattern A unblocks drawings 04, 05 today; the trained model replaces the picker UX entirely later.
- **Use a frontier VLM for parallel-wall plans only.** Same on-prem constraint as ADR-0011. Available as an opt-in for cloud-OK customers; not a default.

## What this means for the existing design doc

[`SOLUTION-DESIGN-V3.md`](../SOLUTION-DESIGN-V3.md) §6.1 describes Pattern A as designed-not-shipped. This ADR sharpens that:

- Pattern A is **deferred to OCR-anchored implementation** (not future investigation of structural-mask approaches).
- The structural-mask approaches are documented as **investigated and ruled out** for dense plans.
- The deferral is **bounded** — M1–M2 is 3–4 weeks of work, not an open question.

## References

- [`../SOLUTION-DESIGN-V3.md`](../SOLUTION-DESIGN-V3.md) §6 (pattern catalogue), §10 (phase-2 work).
- Centerline-mode width caveat: `app/pipeline/v3/color_mask.py` `thicken_centerline` (post-ADR-0012 the dropdown lets users opt into this with eyes open).
- The four structural-mask POCs are not committed; the design rationale is captured here.
