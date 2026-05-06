"""On-demand crop helper for V4 detector tests.

Crops are extracted from `drawings/testset2.pdf` at 300 DPI and cached on
disk so subsequent test runs do not re-render the full 10800x7200 page.
The PDF lives outside the test tree, so a missing file is reported via
`pytest.skip` rather than a hard import error.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

DPI = 300

_REPO_ROOT = Path(__file__).resolve().parents[5]
_TESTSET2 = _REPO_ROOT / "implementation" / "drawings" / "testset2.pdf"
_CACHE_DIR = Path(__file__).resolve().parent / "cache"


@dataclass(frozen=True)
class CropSpec:
    name: str
    bbox: tuple[int, int, int, int]  # (x, y, w, h) at 300 DPI


# Coordinates picked from a manual sweep of testset2 at 300 DPI. Each crop
# isolates exactly one feature so detector counts are stable.
SPECS: dict[str, CropSpec] = {
    "round_duct_with_terminal": CropSpec(
        "round_duct_with_terminal", (1100, 1900, 1300, 700)
    ),
    "rect_duct": CropSpec("rect_duct", (4200, 2300, 1200, 400)),
    "connector_transition": CropSpec("connector_transition", (3200, 2300, 800, 400)),
    "air_terminal": CropSpec("air_terminal", (5650, 1620, 400, 400)),
    "dashed_crossing": CropSpec("dashed_crossing", (5400, 1300, 800, 600)),
}


def get_crop(spec_name: str) -> Image.Image:
    """Return the crop, rendering and caching on first access."""
    if spec_name not in SPECS:
        raise KeyError(f"unknown V4 crop spec: {spec_name}")
    spec = SPECS[spec_name]
    cache_path = _CACHE_DIR / f"{spec.name}.png"
    if cache_path.exists():
        return Image.open(cache_path).convert("RGB")
    if not _TESTSET2.exists():
        pytest.skip(f"testset2 not available at {_TESTSET2}")
    return _render_and_cache(spec, cache_path)


def _render_and_cache(spec: CropSpec, cache_path: Path) -> Image.Image:
    import fitz  # imported lazily so non-fixture tests do not pay the cost

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(_TESTSET2)
    pix = doc[0].get_pixmap(dpi=DPI)
    full = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    x, y, w, h = spec.bbox
    crop = full.crop((x, y, x + w, y + h)).convert("RGB")
    crop.save(cache_path)
    return crop
