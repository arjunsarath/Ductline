# ADR 0016 — Pressure class assignment uses SMACNA static-pressure thresholds with a velocity check

**Status:** Accepted
**Date:** 2026-05-06
**Related:** ADR-0014 (V4 scope), ADR-0015 (outline-based detection)

---

## Context

The original brief asked for **pressure class** per duct (Low / Medium / High). V1–V3 deferred it because the detection stage wasn't producing a stable per-segment graph. V4's outline detection (ADR-0015) plus the segment definition in A6 makes "pressure per segment" well-defined — each segment has two endpoints, a known cross-section, and a CFM trace, so pressure value at each end is computable.

Once pressure values exist, a class assignment is needed. The HVAC industry standard is SMACNA's static-pressure classification, with a velocity cross-check that catches cases where static pressure looks low but the duct is undersized.

The decision is what numeric thresholds to use, where they live, and what the output panel surfaces.

## Decision

Pressure class is assigned per segment using SMACNA static-pressure thresholds:

- **Low** ≤ 2" w.c.
- **Medium** 2 – 3" w.c.
- **High** > 3" w.c.

A secondary velocity check is applied:

- **Low** ≤ 2000 FPM
- **Medium** 2000 – 2500 FPM
- **High** > 2500 FPM

A segment's class is the **higher** of the two checks (pressure-class or velocity-class). A 1.5" w.c. segment running at 2600 FPM classifies as High; a 2.5" w.c. segment at 1500 FPM classifies as Medium.

Both threshold tables and the underlying calculation inputs (air density, friction factor, fitting K-values, flex-duct equivalent length per A11) are **user-editable** via the "Calculation settings" drawer ([`../SOLUTION-DESIGN-V4.md`](../SOLUTION-DESIGN-V4.md) §7). Defaults match SMACNA / ASHRAE conventions for galvanized-steel rigid duct at standard density.

Implementation lives in `backend/app/pipeline/pressure.py` (per the V4 module map). The schema fields exposed to the frontend are `pressure_in_wc_start`, `pressure_in_wc_end`, `velocity_fpm`, and `pressure_class`.

The output panel for a clicked segment surfaces:

- Numeric pressure at both endpoints (in. w.c., 2 decimals).
- Velocity (FPM, 0 decimals).
- Class label (Low / Medium / High).
- A "SMACNA basis" footnote making the threshold source explicit so the user understands what "Medium" means and can override the table if their organization uses different cutoffs.

## Why this is the right call

1. **Matches an industry-standard reference.** SMACNA classes are the standard vocabulary for duct construction and leakage class. Using SMACNA thresholds means the output is directly usable for SMACNA construction-class selection without a translation step.
2. **Velocity check catches the undersized-duct case.** Static pressure alone says nothing about whether the duct is sized correctly for the flow it carries. A high-velocity / low-pressure segment is common in undersized branches; classing it as Low would understate the construction requirement. The "higher of the two" rule is the conservative engineering default.
3. **Surfacing the numbers, not just the label, keeps the user in control.** Engineers verify, they don't trust black boxes. Showing 2.4" w.c. + 2200 FPM → Medium lets the user audit the call. The class label without the numbers would be a regression.
4. **User-editable thresholds avoid baking-in one organization's conventions.** Some firms use 0.5"/2"/3" cutoffs, some use 2"/4"/6", and revisions to SMACNA itself shift the boundaries. The thresholds table being a settings entry rather than a constant means the same V4 deployment serves both default and custom configurations.

## Consequences

**Positive:**
- Pressure class is computed deterministically from per-segment geometry + CFM trace; no manual classification step.
- The output panel exposes both the value and the class, so the user can sanity-check at a glance.
- Threshold edits are live (panel recomputes on settings change), making "what if we move the Medium boundary to 1.5" w.c.?" a one-click question.

**Negative:**
- The "higher of two checks" rule is a defensible default, not the only possible policy. A user who wants pressure-only classification (no velocity escalation) needs to know they can collapse the velocity table to a no-op, which is not as obvious as a single toggle. Acceptable for MVP; a "policy = pressure-only | combined" toggle is a small post-MVP addition.
- Pressure values depend on the friction-factor and air-density inputs. With user-editable defaults, two analysts can produce different class labels for the same drawing. This is correct (different operating assumptions = different classes), but the class label without its inputs is incomplete information. The output panel showing the inputs alongside the class addresses this.
- Per A14 (CFM only from terminals), open-ended ducts have CFM = 0 and therefore P = 0 and class = Low. This is correct given the assumption but can mislead a user who didn't notice the open end. The result UI flags zero-flow segments distinctly from Low-class segments.

**Neutral:**
- Threshold edits do not affect detection or geometry, only the post-detection compute step. Cheap to recompute, no re-detection needed.

## Alternatives considered

- **Compute class from CFM alone.** Rejected as reductive. CFM with no cross-section information says nothing about pressure or velocity. Two ducts at the same CFM with different diameters have very different classes. The MVP has the cross-section, so there's no reason to throw it away.
- **Read class from a drawing schedule.** Some plans tabulate "Low / Med / High" per duct in a schedule block. Rejected because (a) not all plans have one (`testset2.pdf` doesn't), (b) it shifts the work to a separate OCR + table-extraction problem that is not solved elsewhere in V4, and (c) it would shadow the computed value, hiding disagreements between the schedule and the as-drawn geometry. Reading the schedule as a *cross-check* is a sensible post-MVP addition; using it as the primary source isn't.
- **Pressure-only classification (no velocity check).** Rejected as default for the undersized-branch reason above. Available via threshold edit (set velocity cutoffs to ∞).
- **Velocity-only classification (no pressure check).** Rejected for the symmetric reason: long low-velocity runs accumulate static pressure that velocity alone misses.
- **Hard-coded thresholds, no user override.** Rejected. SMACNA tables differ by edition and by firm convention; hard-coding is a future migration headache for marginal implementation savings.

## References

- [`../SOLUTION-DESIGN-V4.md`](../SOLUTION-DESIGN-V4.md) §6 (pressure compute + class), §7 (calculation-settings drawer), §11 (acceptance criteria for pressure outputs).
- A6 (segment definition) and A11 (flex-duct equivalent length, user-editable) from ADR-0014's assumption list — both feed the pressure compute step.
- Threshold defaults derived from SMACNA HVAC duct construction standards; table values are settings, not constants.
