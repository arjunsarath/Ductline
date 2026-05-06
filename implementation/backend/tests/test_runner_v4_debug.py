"""Debug-payload contract for the V4 runner.

When ``debug=True`` the runner attaches a ``V4Debug`` carrying every polygon
``detect_duct_polygons`` produced, each tagged with a kept/dropped status that
matches the runner's three filter points (shape, plausibility, label).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.pipeline.runner_v4 import run_v4
from app.schemas import V4Debug

TESTSET2 = (
    Path(__file__).resolve().parents[2] / "drawings" / "testset2.pdf"
)


@pytest.fixture
def blank_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    pdf_path = tmp_path / "blank.pdf"
    doc.save(pdf_path)
    doc.close()
    return pdf_path


def test_debug_off_keeps_payload_unchanged(blank_pdf: Path) -> None:
    result = run_v4(blank_pdf)
    assert result.debug is None


def test_debug_on_emits_v4debug(blank_pdf: Path) -> None:
    result = run_v4(blank_pdf, debug=True)
    assert isinstance(result.debug, V4Debug)
    # Blank pages produce no polygons; the contract still holds.
    assert result.debug.polygons == []


@pytest.mark.skipif(not TESTSET2.exists(), reason="testset2.pdf not available")
def test_debug_payload_consistency_on_testset2() -> None:
    result = run_v4(TESTSET2, debug=True)
    assert result.debug is not None
    polys = result.debug.polygons
    assert len(polys) >= len(result.segments), (
        f"debug polygons ({len(polys)}) < segments ({len(result.segments)})"
    )
    for p in polys:
        # kept ↔ drop_reason is None
        assert p.kept is (p.drop_reason is None)
        if p.drop_reason is not None:
            assert p.drop_reason in {
                "shape_unknown", "diameter_out_of_range", "no_label",
            }
        assert len(p.bbox) == 4
        assert isinstance(p.shape_hint, str)
