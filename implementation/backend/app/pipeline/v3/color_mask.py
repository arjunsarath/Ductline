"""Color masking + segmentation (SOLUTION-DESIGN-V3 §5.5).

For each user-picked system color, build an HSV ``inRange`` mask and run
the pattern-specific post-processing. Pattern B (outline) flood-fills
the closed outline to recover the duct interior. Pattern A (parallel
walls) and centerline mode produce a different mask shape — see the
docstrings of the two helpers.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray
from skimage.morphology import skeletonize

from app.pipeline.v3.config import ColorPick, V3PipelineConfig


@dataclass
class SystemMask:
    """Output of color masking for a single picked system."""

    pick: ColorPick
    mask: NDArray[np.uint8]  # raw inRange result (walls/lines only)
    filled: NDArray[
        np.uint8
    ]  # post-processed: full duct extent for outline; thickened line for centerline
    skel: NDArray[np.uint8]  # skeletonised filled
    dt: NDArray[
        np.float32
    ]  # distance transform of filled — DT[y,x] = px distance to nearest non-filled


def hsv_inrange(hsv: NDArray[np.uint8], pick: ColorPick) -> NDArray[np.uint8]:
    """Build the raw color mask honouring optional hue-wraparound ``second_range``.

    Saturation/value floors keep anti-aliased edge pixels (low S) and
    near-black/near-white pixels (low V) from contaminating the mask. The
    pick's ranges already encode these floors — this helper just unions
    primary + secondary.
    """
    lo, hi = pick.primary_range.lo, pick.primary_range.hi
    mask = cv2.inRange(hsv, np.array(lo, dtype=np.uint8), np.array(hi, dtype=np.uint8))
    if pick.second_range is not None:
        lo2, hi2 = pick.second_range.lo, pick.second_range.hi
        mask = cv2.bitwise_or(
            mask,
            cv2.inRange(hsv, np.array(lo2, dtype=np.uint8), np.array(hi2, dtype=np.uint8)),
        )
    return mask


def fill_outline(mask: NDArray[np.uint8], close_k: int) -> NDArray[np.uint8]:
    """Pattern B fill: bridge gaps in the outline, then mark interior.

    The closed outline is treated as a single closed curve. Flood-fill
    from (0,0) marks the *exterior*; inverting gives the interior. We
    OR the interior back with the closed mask so the result is the full
    duct extent (walls + interior) rather than just the interior.
    """
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_k, close_k))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    h, w = closed.shape
    flood = closed.copy()
    ff_mask = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(flood, ff_mask, (0, 0), 255)
    interior = cv2.bitwise_not(flood)
    return cv2.bitwise_or(closed, interior)


def drop_small_components(
    mask: NDArray[np.uint8],
    min_area_px: int,
) -> NDArray[np.uint8]:
    """Remove connected components in ``mask`` smaller than ``min_area_px``.

    Used as a post-filter on the *filled* mask to kill text-glyph false
    positives that share the picked color. A maroon "ROYE" label flooded
    into its glyph interiors gets a few hundred-pixel components; a
    legitimate duct interior is several thousand pixels. The threshold is
    deliberately area-only (no aspect ratio): round-duct fills are
    near-circular with low elongation but large area, so we'd reject them
    if we filtered on aspect.
    """
    if min_area_px <= 0:
        return mask
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n_labels <= 1:
        return mask
    keep = np.zeros(n_labels, dtype=bool)
    keep[0] = False  # background label
    for i in range(1, n_labels):
        if int(stats[i, cv2.CC_STAT_AREA]) >= min_area_px:
            keep[i] = True
    if not keep[1:].any():
        return np.zeros_like(mask)
    keep_lut = keep.astype(np.uint8) * 255
    return keep_lut[labels]


def thicken_centerline(mask: NDArray[np.uint8], iterations: int) -> NDArray[np.uint8]:
    """Pattern C fill: dilate the colored centerline so it has a usable
    width. The centerline mode pipeline doesn't flood-fill — the line is
    not closed. ``iterations`` of a 3x3 dilation thickens 1-px lines to
    ~5–7 px so attribute_centerline can do reliable nearest-pixel queries.
    """
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    return cv2.dilate(mask, kernel, iterations=iterations)


def drop_blob_components(
    mask: NDArray[np.uint8],
    area_floor_px: int,
    fill_ratio_max: float,
) -> NDArray[np.uint8]:
    """Drop big blob-shaped components (rooms, title blocks).

    Discriminator: bbox fill ratio = component_area / (bbox_w × bbox_h).
    A duct *tree* is interconnected branches — its bounding box contains
    lots of empty space (fill ratio ~0.10–0.30). A room or title block
    is a compact rectangle whose interior is essentially fully filled
    by flood-fill (fill ratio ~0.70–0.95). The filter fires only when
    *both* the area is large (a small blob is a diffuser, harmless) and
    the bbox is densely filled, so a giant interconnected duct system
    (drawing 03's main supply tree fills ~1.4M px² of mask but only
    ~15% of its bbox) survives intact.
    """
    if area_floor_px <= 0 or fill_ratio_max >= 1.0:
        return mask
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n_labels <= 1:
        return mask
    keep = np.ones(n_labels, dtype=bool)
    keep[0] = False
    for i in range(1, n_labels):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area <= area_floor_px:
            continue  # small enough — keep regardless of shape
        bbox_w = int(stats[i, cv2.CC_STAT_WIDTH])
        bbox_h = int(stats[i, cv2.CC_STAT_HEIGHT])
        bbox_area = bbox_w * bbox_h
        if bbox_area <= 0:
            keep[i] = False
            continue
        fill_ratio = area / bbox_area
        if fill_ratio > fill_ratio_max:
            keep[i] = False
    if not keep[1:].any():
        return np.zeros_like(mask)
    keep_lut = keep.astype(np.uint8) * 255
    return keep_lut[labels]


def drop_text_components(
    mask: NDArray[np.uint8],
    text_mask: NDArray[np.uint8] | None,
    overlap_threshold: float,
) -> NDArray[np.uint8]:
    """Drop connected components in ``mask`` that are mostly text.

    A maroon TEXT label sharing the duct hue gets caught by ``inRange``;
    its filled component is essentially the union of its glyph bboxes,
    so the overlap with the OCR-text mask is near 100%. A legitimate
    duct interior is much larger than any dim labels sitting inside it,
    so the same overlap is a few percent. ``overlap_threshold = 0.5``
    cleanly separates the two cases on real drawings.

    Subtracting ``text_mask`` from the raw color mask BEFORE fill is
    tempting but creates gaps in colored outlines that pass through text,
    and those gaps (often wider than the morph-close kernel) cause the
    flood-fill to leak out of the duct interior. Post-filtering after
    fill avoids that failure mode.
    """
    if text_mask is None or overlap_threshold <= 0.0:
        return mask
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n_labels <= 1:
        return mask
    text_bool = text_mask > 0
    keep = np.ones(n_labels, dtype=bool)
    keep[0] = False
    for i in range(1, n_labels):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area <= 0:
            keep[i] = False
            continue
        in_text = int(((labels == i) & text_bool).sum())
        if in_text / area >= overlap_threshold:
            keep[i] = False
    if not keep[1:].any():
        return np.zeros_like(mask)
    keep_lut = keep.astype(np.uint8) * 255
    return keep_lut[labels]


def build_system_mask(
    img_bgr: NDArray[np.uint8],
    pick: ColorPick,
    config: V3PipelineConfig,
    *,
    text_mask: NDArray[np.uint8] | None = None,
) -> SystemMask:
    """Run mask + pattern-specific fill + skeleton + distance-transform for one pick.

    ``text_mask`` (binary OCR-bbox image) drives the area-overlap filter —
    a maroon TEXT label whose color matches the pick gets caught by
    inRange and the fill component is mostly text, so it's dropped.
    """
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    raw = hsv_inrange(hsv, pick)

    if pick.pattern == "outline":
        filled = fill_outline(raw, close_k=config.outline_close_kernel)
    else:  # centerline
        filled = thicken_centerline(raw, iterations=config.centerline_dilate_iters)

    filled = drop_small_components(filled, config.min_component_area_px)
    filled = drop_blob_components(
        filled,
        config.blob_area_floor_px,
        config.blob_fill_ratio_max,
    )
    filled = drop_text_components(
        filled,
        text_mask,
        overlap_threshold=config.text_overlap_threshold,
    )
    skel = (skeletonize(filled > 0).astype(np.uint8)) * 255
    dt = cv2.distanceTransform(filled, cv2.DIST_L2, 5)

    return SystemMask(pick=pick, mask=raw, filled=filled, skel=skel, dt=dt)


def build_all_system_masks(
    img_bgr: NDArray[np.uint8],
    config: V3PipelineConfig,
    *,
    text_mask: NDArray[np.uint8] | None = None,
) -> list[SystemMask]:
    """Build masks for every pick. Empty masks are returned in-place — the
    runner promotes them to a per-system warning rather than skipping
    silently, so the user knows which color produced no segments.
    """
    return [build_system_mask(img_bgr, p, config, text_mask=text_mask) for p in config.picks]
