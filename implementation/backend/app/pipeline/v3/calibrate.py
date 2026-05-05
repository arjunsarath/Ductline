"""Pixels-per-unit calibration via histogram of candidates (V3 §5.8).

For each attributed dim_rect token we have a measured ``width_px`` plus
two candidate side values ``(a, b)``. We don't know which side is the
plan-visible projection without external information, so we let the
data tell us:

  Each token contributes two candidates: ``width_px / a`` and ``width_px / b``.
  Build a histogram of all candidates from all tokens.
  The dominant bin is the global pixels-per-unit — most tokens agree
  on the rendering scale, so the right answer is the most common one.
  Per-token visible-side disambiguation: pick whichever of (a, b)
  produced the in-band candidate.

Tokens whose chosen ppu lands outside ±N% of the global ppu are
flagged ``low confidence`` — emitted but the popover surfaces the gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.pipeline.v3.attribute import AttributedToken
from app.pipeline.v3.config import V3PipelineConfig


@dataclass
class CalibrationResult:
    ppu: float | None              # px per page-unit (in or mm). None if calibration failed.
    n_pairs: int                   # how many (token, segment) pairs went in
    n_in_band: int                 # how many candidates fell in the dominant bin
    band_lo: float | None          # bin's lower edge
    band_hi: float | None          # bin's upper edge


@dataclass
class ResolvedDimension:
    """One dim_rect token after visible-side disambiguation + ppu cross-check."""

    attributed: AttributedToken
    visible: int                  # plan-visible side picked from (a, b)
    hidden: int                   # the other side
    chosen_ppu: float             # width_px / visible
    delta_pct: float              # (chosen_ppu - global_ppu) / global_ppu * 100
    confidence: Literal["high", "medium", "low"]


def calibrate(
    rect_pairs: list[AttributedToken],
    config: V3PipelineConfig,
) -> CalibrationResult:
    """Run the histogram-of-candidates calibration over rect-only pairs."""
    if len(rect_pairs) < config.min_pairs_for_calibration:
        return CalibrationResult(
            ppu=None, n_pairs=len(rect_pairs), n_in_band=0,
            band_lo=None, band_hi=None,
        )

    cands: list[float] = []
    for p in rect_pairs:
        a, b = p.token.a, p.token.b
        if a is None or b is None or p.width_px <= 0:
            continue
        cands.append(p.width_px / a)
        cands.append(p.width_px / b)

    if len(cands) < config.min_pairs_for_calibration * 2:
        return CalibrationResult(
            ppu=None, n_pairs=len(rect_pairs), n_in_band=0,
            band_lo=None, band_hi=None,
        )

    arr = np.array(cands)
    bins = np.linspace(arr.min(), arr.max(), config.histogram_bins)
    hist, edges = np.histogram(arr, bins=bins)
    peak = int(np.argmax(hist))
    band_lo = float(edges[max(0, peak - 1)])
    band_hi = float(edges[min(len(edges) - 1, peak + 2)])
    in_band = arr[(arr >= band_lo) & (arr <= band_hi)]
    if len(in_band) == 0:
        return CalibrationResult(
            ppu=None, n_pairs=len(rect_pairs), n_in_band=0,
            band_lo=None, band_hi=None,
        )
    ppu = float(np.median(in_band))
    return CalibrationResult(
        ppu=ppu,
        n_pairs=len(rect_pairs),
        n_in_band=int(len(in_band)),
        band_lo=band_lo,
        band_hi=band_hi,
    )


def resolve_visible_sides(
    rect_pairs: list[AttributedToken],
    cal: CalibrationResult,
    config: V3PipelineConfig,
) -> list[ResolvedDimension]:
    """For each pair, pick the side whose ppu is closer to global ppu.

    Tokens within ±config.inlier_band_pct → high confidence.
    Outside → low confidence. (We don't drop them — the user might want
    to see the discrepancy and override.)
    """
    if cal.ppu is None:
        return []

    resolved: list[ResolvedDimension] = []
    for p in rect_pairs:
        a, b = p.token.a, p.token.b
        if a is None or b is None or p.width_px <= 0:
            continue
        a_ppu = p.width_px / a
        b_ppu = p.width_px / b
        if abs(a_ppu - cal.ppu) <= abs(b_ppu - cal.ppu):
            visible, hidden, chosen_ppu = a, b, a_ppu
        else:
            visible, hidden, chosen_ppu = b, a, b_ppu
        delta_pct = (chosen_ppu - cal.ppu) / cal.ppu * 100.0
        if abs(delta_pct) <= config.inlier_band_pct:
            confidence: Literal["high", "medium", "low"] = "high"
        elif abs(delta_pct) <= 2 * config.inlier_band_pct:
            confidence = "medium"
        else:
            confidence = "low"
        resolved.append(
            ResolvedDimension(
                attributed=p,
                visible=visible,
                hidden=hidden,
                chosen_ppu=chosen_ppu,
                delta_pct=delta_pct,
                confidence=confidence,
            )
        )
    return resolved
