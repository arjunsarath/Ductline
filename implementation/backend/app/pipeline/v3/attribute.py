"""Token-to-system attribution + per-token width measurement (V3 §5.7).

Pattern B (the V3 ship path) uses the in-mask filter:

    A token belongs to system S iff its bbox center lies inside S.filled.

This eliminates equipment-suffix noise (CD-1, RG-1, TG-3, …) because
those labels are drawn outside the colored duct outline by construction.
For each in-mask token, the duct width at the token location is measured
by snapping to the nearest skeleton pixel and reading the distance
transform (= radius); doubled it's the full duct width perpendicular to
the run.

Pattern A and C attribution rules are designed in V3 §5.7 but not yet
shipped — they need a different mask shape (parallel walls / colored
centerline) and a width-from-grayscale-rays measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.pipeline.v3.color_mask import SystemMask
from app.pipeline.v3.config import V3PipelineConfig
from app.pipeline.v3.ocr_classify import ClassifiedToken


@dataclass
class AttributedToken:
    """A token paired to a system + the local pixel width measured at the token."""

    token: ClassifiedToken
    system_index: int           # index into V3PipelineConfig.picks
    width_px: float             # measured at the nearest skel pixel
    skel_xy: tuple[int, int]    # the skeleton point used for width
    # Which attribution rule fired:
    #   • ``in_mask``    — bbox row intersects the system's filled mask
    #   • ``proximity``  — bbox didn't intersect any mask, but the
    #                       nearest skeleton pixel was within
    #                       ``config.proximity_attr_search_px``
    rule: Literal["in_mask", "proximity"]


def _nearest_skel_pixel(
    skel: np.ndarray, cx: int, cy: int, search_r: int
) -> tuple[int, int] | None:
    """Find the skeleton pixel nearest to (cx, cy) within ``search_r`` px.

    Hot loop: expand a square window in 5-px rings; the first ring with
    any skeleton pixels wins. Within the winning ring we pick the
    Euclidean-nearest. Returns None if no skel pixel is within search_r.
    """
    h, w = skel.shape
    if 0 <= cy < h and 0 <= cx < w and skel[cy, cx]:
        return cx, cy
    for r in range(5, search_r + 1, 5):
        x0, x1 = max(0, cx - r), min(w, cx + r + 1)
        y0, y1 = max(0, cy - r), min(h, cy + r + 1)
        sub = skel[y0:y1, x0:x1]
        if sub.any():
            ys, xs = np.where(sub > 0)
            d2 = (xs - (cx - x0)) ** 2 + (ys - (cy - y0)) ** 2
            idx = int(np.argmin(d2))
            return x0 + int(xs[idx]), y0 + int(ys[idx])
    return None


def _proximity_attribute_one(
    tok: ClassifiedToken,
    system_masks: list[SystemMask],
    config: V3PipelineConfig,
) -> AttributedToken | None:
    """Snap a single off-mask token to the nearest system's skeleton.

    Many CAD plans place the dim label *beside* the colored duct outline
    rather than inside it, so the bbox-row-in-mask rule misses them. The
    proximity rule: pick the system whose skeleton has a pixel closest to
    the token's bbox center within ``proximity_attr_search_px``. Width is
    measured at that skeleton pixel — same DT semantics as the in-mask
    path so calibration sees a comparable ppu candidate.

    Picks ties by Euclidean distance, so when multiple systems run near a
    token the closest one wins. Returns ``None`` if no system is in range.
    """
    bx, by, bw, bh = tok.bbox
    cx = bx + bw // 2
    cy = by + bh // 2
    radius = config.proximity_attr_search_px

    best: tuple[float, int, tuple[int, int], float] | None = None
    for s_idx, sm in enumerate(system_masks):
        anchor = _nearest_skel_pixel(sm.skel, cx, cy, radius)
        if anchor is None:
            continue
        ax, ay = anchor
        d = ((ax - cx) ** 2 + (ay - cy) ** 2) ** 0.5
        radius_px = float(sm.dt[ay, ax])
        if radius_px < config.min_segment_radius_px:
            continue
        if best is None or d < best[0]:
            best = (d, s_idx, (ax, ay), radius_px)
    if best is None:
        return None
    _, s_idx, anchor_xy, radius_px = best
    return AttributedToken(
        token=tok,
        system_index=s_idx,
        width_px=2.0 * radius_px,
        skel_xy=anchor_xy,
        rule="proximity",
    )


def attribute_in_mask(
    rect_tokens: list[ClassifiedToken],
    system_masks: list[SystemMask],
    config: V3PipelineConfig,
) -> list[AttributedToken]:
    """Pattern B attribution by token-bbox row intersection with the mask.

    The simple "bbox-center inside filled" rule from the spike worked for
    drawing 03's first floor plan because dim labels like "23x13" sit
    inside the colored duct rectangle. It misses cases where the dim is
    written next to a duct (the second floor plan: "16x8 ED-1" is on the
    duct line but the bbox center includes the equipment suffix to the
    right of the dim, sometimes off the colored mask).

    The relaxed rule: a token attributes to system S iff its bbox HAS
    ANY OVERLAP with S.filled along the bbox's vertical center row.
    The anchor (skeleton pixel for width measurement) is picked as the
    middle in-mask pixel along the bbox row. Equipment labels (CD-1,
    RG-1, …) typically sit far enough away from the colored duct that
    their bbox row doesn't intersect the mask — so the equipment-label
    filter the spike validated still holds.

    Tokens whose bbox doesn't intersect any system's mask fall through
    to the ``_proximity_attribute_one`` fallback, which catches the
    common case of dim labels written *beside* a colored duct outline.
    Pure equipment-suffix labels (CD-1, RG-1, ...) sit far enough from
    the duct that they fail the proximity radius too and stay dropped.
    """
    out: list[AttributedToken] = []
    for tok in rect_tokens:
        bx, by, bw, bh = tok.bbox
        cx = bx + bw // 2
        cy = by + bh // 2

        attributed = False
        for s_idx, sm in enumerate(system_masks):
            h, w = sm.filled.shape
            if not (0 <= cy < h):
                continue
            row_x0 = max(0, bx)
            row_x1 = min(w, bx + bw)
            if row_x0 >= row_x1:
                continue
            row = sm.filled[cy, row_x0:row_x1]
            if not (row > 0).any():
                continue
            # Find an anchor: prefer the bbox-center if in mask, else the
            # nearest in-mask x along the bbox row. Then snap to skeleton.
            in_mask_xs = np.where(row > 0)[0] + row_x0
            if 0 <= cx < w and sm.filled[cy, cx] > 0:
                anchor_x, anchor_y = cx, cy
            else:
                # Median of in-mask xs along the row — robust to a wide
                # bbox that only partially overlaps the colored region.
                anchor_x = int(in_mask_xs[len(in_mask_xs) // 2])
                anchor_y = cy
            anchor = _nearest_skel_pixel(
                sm.skel, anchor_x, anchor_y, config.nearest_skel_search_px,
            )
            if anchor is None:
                radius_px = float(sm.dt[anchor_y, anchor_x])
                anchor = (anchor_x, anchor_y)
            else:
                nx, ny = anchor
                radius_px = float(sm.dt[ny, nx])
            if radius_px < config.min_segment_radius_px:
                attributed = True
                break  # too thin to be a duct — don't attribute, don't fallback
            out.append(
                AttributedToken(
                    token=tok,
                    system_index=s_idx,
                    width_px=2.0 * radius_px,
                    skel_xy=anchor,
                    rule="in_mask",
                )
            )
            attributed = True
            break  # token is attributed; move to next token
        if not attributed:
            prox = _proximity_attribute_one(tok, system_masks, config)
            if prox is not None:
                out.append(prox)
    return out


def attribute_round_in_mask(
    round_tokens: list[ClassifiedToken],
    system_masks: list[SystemMask],
    config: V3PipelineConfig,
) -> list[AttributedToken]:
    """Pattern B attribution for round-duct callouts (e.g., ``13"Ø``).

    Same bbox-row-intersection geometry as the rectangular path; the only
    difference is that round tokens carry a single ``diameter`` value
    instead of an ``a × b`` pair, so visible-side disambiguation is a
    no-op. Width measurement at the anchor still happens — the runner
    cross-checks ``pixel_diameter / token.diameter`` against the global
    ppu calibrated from the rectangular pairs.
    """
    out: list[AttributedToken] = []
    for tok in round_tokens:
        if tok.diameter is None:
            continue
        bx, by, bw, bh = tok.bbox
        cy = by + bh // 2
        attributed = False
        for s_idx, sm in enumerate(system_masks):
            h, w = sm.filled.shape
            if not (0 <= cy < h):
                continue
            row_x0 = max(0, bx)
            row_x1 = min(w, bx + bw)
            if row_x0 >= row_x1:
                continue
            row = sm.filled[cy, row_x0:row_x1]
            if not (row > 0).any():
                continue
            in_mask_xs = np.where(row > 0)[0] + row_x0
            cx = bx + bw // 2
            if 0 <= cx < w and sm.filled[cy, cx] > 0:
                anchor_x, anchor_y = cx, cy
            else:
                anchor_x = int(in_mask_xs[len(in_mask_xs) // 2])
                anchor_y = cy
            anchor = _nearest_skel_pixel(
                sm.skel, anchor_x, anchor_y, config.nearest_skel_search_px,
            )
            if anchor is None:
                radius_px = float(sm.dt[anchor_y, anchor_x])
                anchor = (anchor_x, anchor_y)
            else:
                nx, ny = anchor
                radius_px = float(sm.dt[ny, nx])
            if radius_px < config.min_segment_radius_px:
                attributed = True
                break
            out.append(
                AttributedToken(
                    token=tok,
                    system_index=s_idx,
                    width_px=2.0 * radius_px,
                    skel_xy=anchor,
                    rule="in_mask",
                )
            )
            attributed = True
            break
        if not attributed:
            prox = _proximity_attribute_one(tok, system_masks, config)
            if prox is not None:
                out.append(prox)
    return out


def attribute_flow_in_mask(
    flow_tokens: list[ClassifiedToken],
    system_masks: list[SystemMask],
    config: V3PipelineConfig,
) -> list[AttributedToken]:
    """Strict in-mask attribution for flow (CFM/L-s) tokens.

    Same geometric rule as ``attribute_in_mask`` for dim tokens. The
    posture: a flow value is the property of *the duct segment it sits
    inside*, never of a neighbouring segment we hopefully-found by
    proximity. Mains that don't carry their own CFM label produce no
    in-mask flow attribution and fall back to size-only pressure-class
    estimation — that is the engineering-honest answer until V3 phase 2
    adds duct topology + downstream CFM aggregation (V3 §10).

    Each flow token is attributed to at most one system (the first whose
    filled mask contains the bbox center). Equipment-label CFM tokens
    that sit outside every system's outline are dropped.
    """
    out: list[AttributedToken] = []
    for tok in flow_tokens:
        bx, by, bw, bh = tok.bbox
        cx = bx + bw // 2
        cy = by + bh // 2
        for s_idx, sm in enumerate(system_masks):
            h, w = sm.filled.shape
            if not (0 <= cx < w and 0 <= cy < h):
                continue
            if sm.filled[cy, cx] == 0:
                continue
            anchor = _nearest_skel_pixel(sm.skel, cx, cy, config.nearest_skel_search_px)
            if anchor is None:
                anchor = (cx, cy)
                radius_px = float(sm.dt[cy, cx])
            else:
                nx, ny = anchor
                radius_px = float(sm.dt[ny, nx])
            out.append(
                AttributedToken(
                    token=tok,
                    system_index=s_idx,
                    width_px=2.0 * radius_px,
                    skel_xy=anchor,
                    rule="in_mask",
                )
            )
            break
    return out
