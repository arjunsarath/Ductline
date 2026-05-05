# ADR 0012 — Dark-line HSV band and explicit pattern toggle

**Status:** Accepted
**Date:** 2026-05-04
**Related:** ADR-0011 (V3 pivot rationale)

---

## Context

V3's color-pick UX assumes the duct system has a saturated color the user can identify (drawings 01, 03 — supply ducts in blue or cyan). On a meaningful fraction of the benchmark, that assumption breaks:

- **Drawing 02** (`newwest-mixed-trades`): ducts are **black** on a **faded grey** background. Everything else (building outlines, walls, equipment) is the same faded grey. The "duct color" is `RGB(0,0,0)`, distinguishable from background by **brightness**, not hue.
- **Drawings 04, 05** (`asc2018-bid-set`, `federal-attachment`): also black ducts on light grey. Compounded by parallel-wall convention (see ADR-0013 for that side of the issue).

The default `defaultBand` HSV band (`h ± 12`, `s_lo = max(60, hsv.s − 80)`, `v_lo = max(60, hsv.v − 80)`) breaks on a black pick: with `hsv.h = 0, hsv.s = 0, hsv.v = 0` it builds a band centred on red with high saturation/value floors — matches almost nothing.

The picker also previously rejected dark picks outright (`if hsv.v < 25 → reject`) on the assumption that dark pixels are text/gridlines. That rejection is wrong on dark-line drawings, where the duct *is* the dark pixel.

A second issue surfaced on drawing 04: even with the right HSV band, the **fill pattern** matters. Drawing 02's ducts are *closed* dark outlines — flood-fill from outside marks the interior. Drawing 04's ducts are *parallel walls without a closed perimeter* — flood-fill leaks across the whole plan and the blob filter drops the result. The user needs to be able to switch the pipeline from `outline` mode to `centerline` mode (dilate-the-line, no flood-fill) for these drawings.

## Decision

Two coupled changes in the picker:

### 1. Dark-line HSV band

Detect "dark pick" when sampled `V < 60` and build a **permissive band**: `h ∈ [0, 180]`, `s ∈ [0, 255]`, `v ∈ [0, max(60, V + 30)]`. Hue is meaningless when the duct is rendered in black; the discriminator is brightness only.

Frontend code: `frontend/src/components/v3/V3PickerView.tsx` `darkBand()`. Branch in `addPick`:

```ts
const isDarkPick = hsv.v < 60;
const newPick = isDarkPick
  ? { ..., kind: "other", primary: darkBand(hsv), label: "Marked duct (dark)" }
  : { ..., suggestion = suggestKind(hsv), primary: defaultBand(hsv), ... };
```

Click-rejection rules updated:
- White-ish (`V > 240` *and* `S < 30`) → reject.
- Faded mid-grey (`V ∈ [60, 240]` *and* `S < 25`) → reject.
- Black with any `S` → **accept** (was previously rejected by `V < 25`).

The faded-grey rejection still fires on building outlines and gridlines (which sit at `V ≈ 180–220`), so dark-line picks are protected from accidentally selecting a wall.

### 2. Explicit pattern toggle in the pick card

The pick card has a dropdown:

```
Pattern:
  ⊙ Outline (closed colored loop)        ← default for color picks + drawing 02
  ○ Centerline (line through duct)       ← required for drawings 04, 05
```

The default is `outline` because Pattern B is the dominant convention. Users with parallel-wall drawings flip to `centerline`.

The dropdown is the **least-bad UX** until Pattern A auto-detection is built (M7–M9 in the production timeline). Previous attempts to auto-detect parallel-wall convention from a sparse-detection signal were too unreliable to ship as a default.

## Why this is the right call

1. **Doesn't fork the picker UX.** Same picker, same magnifier, same click-and-go. The dark-pick branch is internal; users don't think "should I use dark mode?" — they just click on the duct.
2. **Recovers drawing 02.** On the benchmark, the dark-line band + outline pattern produces 20 segments with extracted CFM on drawing 02 — equivalent quality to drawings 01 and 03.
3. **Surfaces drawing 04, 05 as recoverable** with one extra dropdown change. Not as good as auto-detection but bounded and explicit.
4. **Composes with the auto-detection roadmap.** Adding Pattern A auto-detection later means setting the dropdown default automatically; the underlying pipeline knobs don't change.

## Consequences

**Positive:**
- Drawing 02 ships at production quality.
- Drawings 04, 05 ship at "labels attributed correctly, widths suspect" quality (centerline mode limits widths to dilation thickness — see ADR-0013).
- Click-rejection feedback (transient banner on rejected clicks) makes failures visible instead of silent.

**Negative:**
- Two paths in `addPick` (dark vs default). Maintenance cost is real but the branch is a few lines.
- Users with parallel-wall drawings need to know to flip the pattern toggle. Mitigated by labeling the centerline option clearly.
- The dark-line band is permissive (`V ≤ 60` on any hue). On drawings with dark equipment outlines that aren't ducts, those will get caught by the mask too. The structural filters (`drop_blob_components`, `drop_text_components`) clean most of this up.

## Alternatives considered

- **Auto-detect dark vs colored picks at sample time, no separate band logic.** Same outcome, same code; the explicit `isDarkPick` branch is just more readable.
- **Always use the dark-line band, even for color picks.** Fails on drawings 01, 03 because cyan/blue ducts have `S = 200+, V = 180+` and the dark-line band doesn't cover them.
- **Always use a hue-range band, treat black as a special hue.** Black has no hue; the math falls apart at `S = 0`.
- **Auto-detect pattern (outline vs centerline) instead of a dropdown.** Investigated three POCs (DT-threshold, isotropic tophat, directional tophat). All produced too many false positives on dense plans to be a default. The dropdown is the correct tactical answer until M7–M9.

## What this changes downstream

- `ColorPick.pattern` already supported `outline | centerline`; the picker now exposes this as a UI control rather than hardcoding `outline` for every pick.
- The runner is unchanged — pattern-specific logic in `color_mask.fill_outline` vs `color_mask.thicken_centerline` was already in place.
- No test changes; drawing 03 regression test continues to pin the dominant code path.

## References

- [`../SOLUTION-DESIGN-V3.md`](../SOLUTION-DESIGN-V3.md) §5.4 (color pick) and §5.5 (color mask).
- Frontend implementation: `frontend/src/components/v3/V3PickerView.tsx`.
- Click-rejection styling: `frontend/src/styles/v3.css` `.picker-instructions.is-error`.
