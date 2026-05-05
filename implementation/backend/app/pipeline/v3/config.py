"""V3 pipeline configuration — color picks, mode, render settings.

A V3 pipeline run is parameterised by a ``V3PipelineConfig``: the list of
``ColorPick``s the user selected, plus the target render DPI and a few
knobs (search radius, calibration band). Defaults match the spike values
that produced 88% attribution accuracy on drawing 03.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

HSVTuple = tuple[int, int, int]


@dataclass(frozen=True)
class HSVRange:
    lo: HSVTuple
    hi: HSVTuple


@dataclass(frozen=True)
class ColorPick:
    """One system color the user picked + labelled.

    ``second_range`` carries hue-wraparound colors (red spans hue 0..10
    and 170..180 in OpenCV's 0..180 HSV space). ``pattern`` selects which
    morphological recipe is used in the mask stage; ``kind`` is the
    SMACNA system label used for downstream defaults (material, fall-back
    hue convention).
    """

    label: str
    primary_range: HSVRange
    pattern: Literal["outline", "centerline"]
    kind: Literal["supply", "return", "exhaust", "outside", "other"]
    display_color_bgr: tuple[int, int, int]  # for overlay rendering
    second_range: HSVRange | None = None
    system_id: str = ""  # filled by runner if blank


@dataclass(frozen=True)
class V3PipelineConfig:
    picks: list[ColorPick]

    # Rendering — adaptive DPI is computed at runtime from the OCR probe;
    # this is the floor and ceiling.
    target_text_height_px: int = 24
    min_dpi: int = 200
    max_dpi: int = 600
    raster_min_long_edge_px: int = 3000  # below this, hard error

    # Color-mask post-processing.
    outline_close_kernel: int = 11   # MORPH_CLOSE kernel for Pattern B fill
    centerline_dilate_iters: int = 2  # 3x3 dilation iters for Pattern C

    # Component filter — kills text-glyph false positives that share the
    # picked color (e.g., maroon text within a "return" pick). Tuned at
    # ~400–600 DPI: a single OCR letter is ~1k px², a small round duct's
    # filled interior is ~5k+ px². Components below the area floor are
    # dropped unconditionally.
    min_component_area_px: int = 1500
    # Blob filter — kills room-sized flood-fills + title blocks on
    # dark-line drawings (drawing 02). A duct tree is interconnected
    # branches that fill only ~10–30% of its bounding box; a room or
    # title block fills 70–90% of its bbox (it's a compact rectangle).
    # Drop only when *both* conditions fire so a giant interconnected
    # duct system on a dense plan (drawing 03 fills ~1.4M px² as one
    # connected supply tree) survives.
    blob_area_floor_px: int = 500000
    blob_fill_ratio_max: float = 0.5

    # Text-overlap filter — kills components whose pixels are heavily
    # covered by OCR text bboxes. Targets two patterns at once:
    #   (a) maroon TEXT-glyph false positives that share the picked
    #       hue (overlap ~100% — easy)
    #   (b) labelled callout boxes (drawing 02's ``TG | 24x12``-style
    #       small filled rectangles holding a system code + a dim
    #       token; overlap ~30% because text fills a large fraction
    #       of the small box).
    # Drawing 03's duct interiors and round-duct fills sit at <5% so
    # 0.30 has plenty of headroom and doesn't regress them.
    text_overlap_threshold: float = 0.30

    # Attribution.
    nearest_skel_search_px: int = 80  # max distance from token to skeleton
    min_segment_radius_px: float = 4.0  # below this DT, skip the pair
    # Proximity-attribution fallback radius — for tokens whose bbox
    # doesn't intersect any system mask, try snapping to the nearest
    # skeleton pixel within this many px. CAD plans typically place dim
    # labels right next to the line (~50 px gap at 600 DPI = 0.08 in).
    # Larger radii pull in equipment/room labels from a couple inches
    # away and pollute calibration on dense plans like drawing 03.
    proximity_attr_search_px: int = 50

    # Calibration.
    histogram_bins: int = 60
    inlier_band_pct: float = 15.0  # ±N% from ppu = "high confidence"
    min_pairs_for_calibration: int = 3
