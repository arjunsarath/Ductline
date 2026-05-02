"""Hallucination guards in app.vlm.ollama (PR-A).

Pure-function tests exercising ``_reject_if_hallucinated`` directly — no live
Ollama, no httpx. Covers the new uniform-pattern guard (introduced after the
column-marker hallucination on drawing 01) plus the legacy duplicate / grid /
count guards to lock their behaviour against the new check.
"""

from __future__ import annotations

import pytest

from app.vlm.base import VLMError
from app.vlm.ollama import _reject_if_hallucinated
from app.vlm.tools import DetectDuctsTool, VLMSegment


def _seg(bbox: tuple[float, float, float, float]) -> VLMSegment:
    return VLMSegment(bbox=bbox, shape_hint="rectangular", nearby_text=[])


def _tool(bboxes: list[tuple[float, float, float, float]]) -> DetectDuctsTool:
    return DetectDuctsTool(segments=[_seg(b) for b in bboxes])


def test_uniform_pattern_rejected() -> None:
    """Four identically-shaped bboxes at different positions → uniform-pattern reject.

    This is the column-marker hallucination from drawing 01: the model emits
    one box per repeating geometric element (column marker), all the same
    width and height, just translated along an axis. Off-grid coordinates
    are used so this exercises the new uniform-pattern check rather than
    the legacy tenth-grid check.
    """
    tool = _tool(
        [
            (0.073, 0.121, 0.139, 0.234),
            (0.193, 0.121, 0.259, 0.234),
            (0.313, 0.121, 0.379, 0.234),
            (0.433, 0.121, 0.499, 0.234),
        ]
    )

    with pytest.raises(VLMError, match="uniformly-shaped bboxes"):
        _reject_if_hallucinated(tool)


def test_diverse_dimensions_pass() -> None:
    """Real ducts have varied sizes — guard must let them through.

    Values are deliberately off-grid (no two coords round to the same tenth)
    so we don't accidentally trip the tenth-grid heuristic.
    """
    tool = _tool(
        [
            (0.073, 0.142, 0.418, 0.187),   # long thin horizontal
            (0.231, 0.318, 0.286, 0.812),   # tall thin vertical
            (0.512, 0.418, 0.731, 0.612),   # square-ish
            (0.067, 0.857, 0.946, 0.928),   # very wide thin
        ]
    )

    _reject_if_hallucinated(tool)  # no raise


def test_uniform_check_skipped_below_min_count() -> None:
    """3 uniform bboxes is below the 4-segment minimum — must not trip guard."""
    tool = _tool(
        [
            (0.073, 0.121, 0.139, 0.234),
            (0.193, 0.121, 0.259, 0.234),
            (0.313, 0.121, 0.379, 0.234),
        ]
    )

    _reject_if_hallucinated(tool)  # no raise


def test_count_limit_still_fires() -> None:
    """Legacy guard: > 80 segments → reject."""
    tool = _tool([(i * 0.001, 0.0, i * 0.001 + 0.01, 0.01) for i in range(81)])

    with pytest.raises(VLMError, match="likely hallucinated"):
        _reject_if_hallucinated(tool)


def test_duplicate_guard_still_fires() -> None:
    """Legacy guard: ≥ 50% duplicate bboxes → reject.

    Bboxes share x0/y0 with their predecessors but each differs in width to
    avoid tripping the uniform-pattern check before the duplicate check.
    """
    tool = _tool(
        [
            (0.10, 0.10, 0.213, 0.20),
            (0.10, 0.10, 0.213, 0.20),
            (0.10, 0.10, 0.213, 0.20),
            (0.30, 0.30, 0.452, 0.40),
        ]
    )

    with pytest.raises(VLMError, match="duplicate"):
        _reject_if_hallucinated(tool)


def test_tenth_grid_guard_fires_on_clean_tenths() -> None:
    """Bboxes with all coords at exact tenths (0.1, 0.2, …) → tenth-grid reject.

    Locks the corrected tenth-grid heuristic — the V1 implementation used
    ``round(c, 1) in _GRID_VALUES`` which always evaluated True for any
    value in [0, 1] (every float rounds to some tenth). The fixed version
    requires the value to be within tolerance of its nearest tenth.
    """
    tool = _tool(
        [
            (0.10, 0.10, 0.20, 0.30),
            (0.30, 0.10, 0.40, 0.30),
            (0.50, 0.10, 0.60, 0.30),
            (0.70, 0.10, 0.90, 0.40),  # different size, escapes uniform check
        ]
    )

    with pytest.raises(VLMError, match="tenth-grid"):
        _reject_if_hallucinated(tool)
