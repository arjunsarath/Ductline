"""Progress-callback contract for the V4 runner.

The streaming endpoint relies on every pipeline stage emitting a progress
event in a stable order. We run on a deliberately empty synthetic PDF so the
test is fast — the runner still walks every stage even when CV finds nothing.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.pipeline.runner_v4 import run_v4
from app.schemas import PageDims, V4Result

EXPECTED_STAGES = (
    "rasterize",
    "grey_removal",
    "find_rectangles",
    "filter_oversized",
    "filter_rectangle",
    "ocr_all",
    "done",
)


@pytest.fixture
def blank_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    pdf_path = tmp_path / "blank.pdf"
    doc.save(pdf_path)
    doc.close()
    return pdf_path


def test_progress_callback_fires_each_stage_in_order(blank_pdf: Path) -> None:
    seen: list[tuple[str, dict]] = []

    def on_progress(stage: str, payload: dict) -> None:
        seen.append((stage, payload))

    result = run_v4(blank_pdf, progress=on_progress)

    assert isinstance(result, V4Result)
    stages = [stage for stage, _ in seen]
    assert stages == list(EXPECTED_STAGES), stages

    for _, payload in seen:
        assert "stage" in payload
        assert "message" in payload
        assert isinstance(payload["elapsed_ms"], int)
        assert payload["elapsed_ms"] >= 0


def test_runner_populates_page_dims(blank_pdf: Path) -> None:
    result = run_v4(blank_pdf)
    assert isinstance(result.page_dims, PageDims)
    assert result.page_dims.width_px > 0
    assert result.page_dims.height_px > 0
    assert 100 <= result.page_dims.dpi <= 300


def test_progress_optional(blank_pdf: Path) -> None:
    """Calling without a progress callback must still succeed."""
    result = run_v4(blank_pdf)
    assert isinstance(result, V4Result)
