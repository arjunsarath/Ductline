"""V3 pipeline regression test on drawing 03 (SOLUTION-DESIGN-V3 §11).

This test locks in the empirically validated numbers from the V3 spike +
end-to-end implementation run. Any future change that moves these numbers
deserves an explicit decision in the diff — bumping ppu, dropping below
22 high-confidence segments, or losing a distinct-size class is a real
regression worth surfacing.

The test is gated on the sample PDF being present at the expected path;
CI environments without the binary skip cleanly. (The PDF is committed
to the repo at sample-HVAC/03-caddsultants-shop.pdf.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.api.deps import build_ocr
from app.pipeline.v3.config import ColorPick, HSVRange, V3PipelineConfig
from app.pipeline.v3.runner import V3DetectionPipeline

SAMPLE_PDF = Path(__file__).resolve().parents[3] / "sample-HVAC" / "03-caddsultants-shop.pdf"


@pytest.fixture
def picks_03() -> V3PipelineConfig:
    """Blue rectangular outline pick that matched 88% in the spike."""
    return V3PipelineConfig(
        picks=[
            ColorPick(
                label="Supply Air",
                primary_range=HSVRange(lo=(100, 80, 80), hi=(130, 255, 255)),
                pattern="outline",
                kind="supply",
                display_color_bgr=(255, 100, 0),
                system_id="sys_supply_blue",
            )
        ]
    )


@pytest.mark.skipif(not SAMPLE_PDF.exists(), reason="sample PDF not present")
def test_v3_drawing_03_endtoend(picks_03: V3PipelineConfig) -> None:
    """End-to-end V3 run on drawing 03 produces stable numbers.

    These bounds are deliberately loose around the spike's exact values
    so OCR-engine drift (model updates) doesn't break the test, but they
    catch real regressions:
      • segments: 25 ± 5
      • ppu: 4.38 ± 0.4 (per-inch, drawing 03's rendered scale)
      • high-confidence segments: ≥ 18 (the spike's 22 minus a buffer)
      • distinct duct sizes: ≥ 6
      • flow tokens in-mask: ≤ 5 (drawing 03 labels CFM at diffusers,
        outside the duct outlines — V3 §5.10 finding)
    """
    file_bytes = SAMPLE_PDF.read_bytes()
    pipe = V3DetectionPipeline(ocr=build_ocr())
    result = pipe.run(file_bytes, "03-caddsultants-shop.pdf", picks_03)

    # Page rendered at adaptive DPI, rotated to canonical orientation
    assert (
        result.target_dpi >= 400
    ), "expected adaptive DPI to land >= 400 for vector PDF with small text"
    assert result.rotation_applied in (0, 90, 180, 270)
    assert (
        result.page_unit == "in"
    ), "drawing 03 has 41 CFM tokens, 0 L/s — page unit must resolve to 'in'"

    # Calibration converged
    assert result.ppu is not None, "calibration must converge on drawing 03"
    assert 4.0 <= result.ppu <= 4.8, f"ppu out of validated band: {result.ppu}"

    # Segment count — drawing 03 has both rectangular ducts (top plan)
    # and round ducts (bottom plan). Spike + round-support implementation
    # produces ~44 (25 rect + 19 round). Loose band catches OCR drift.
    assert (
        35 <= len(result.segments) <= 60
    ), f"segment count outside validated band: {len(result.segments)}"
    rect_segs = [s for s in result.segments if s.shape == "rectangular"]
    round_segs = [s for s in result.segments if s.shape == "round"]
    assert len(rect_segs) >= 18, f"rect segments dropped: {len(rect_segs)}"
    assert (
        len(round_segs) >= 10
    ), f"round segments dropped — bottom plan should produce some: {len(round_segs)}"

    # Confidence distribution — spike got 22 high on rect alone; with
    # round added we expect additional high-confidence tokens because
    # round ppu cross-validates well against rect-derived global ppu.
    high = sum(1 for s in result.segments if s.dim_confidence == "high")
    assert high >= 25, f"high-confidence segments dropped below floor: {high}"

    # Distinct duct sizes recovered
    sizes: set[tuple[int, int]] = set()
    for s in result.segments:
        if s.dim_confidence == "high":
            sizes.add((min(s.visible_unit, s.hidden_unit), max(s.visible_unit, s.hidden_unit)))
    assert len(sizes) >= 6, f"distinct duct sizes dropped: {sizes}"

    # Flow tokens are mostly outside duct outlines on drawing 03 — V3 §5.10
    # documents this as the empirical finding for this drawing class.
    assert result.n_attributed_flow <= 5, (
        f"flow attribution rose unexpectedly — drawing 03 was validated at 0/41 in-mask: "
        f"{result.n_attributed_flow}"
    )

    # All segments either extracted-flow or size-only pressure class
    for seg in result.segments:
        assert seg.pressure.source in ("extracted", "estimated:size_only")
        if seg.pressure.source == "extracted":
            assert seg.pressure.flow_value is not None
            assert seg.pressure.velocity_fpm is not None
            assert seg.pressure.confidence == "high"
        else:
            assert seg.pressure.confidence == "low"
            assert seg.pressure.flow_value is None
