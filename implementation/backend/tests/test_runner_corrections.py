"""Runner-level inline-correction plumbing for the categorize gate.

The categorize approval gate now accepts an optional ``layout`` correction
payload (an edited PageLayout from the frontend's interactive editor).
This module exercises the runner's ``_apply_layout_corrections`` helper
in isolation — the integration path (SSE bridge → session → POST body →
runner) is covered by ``test_sessions.py``; here we lock the unit
behaviour:

  • Approving with a corrections dict mutates ``ctx.layout`` to match
    the user's edits.
  • A null ``plan_view`` in corrections falls back to the whole-page
    rect — same posture as the §7 categorizer-failed path.
"""

from __future__ import annotations

from PIL import Image

from app.pipeline.base import PipelineContext
from app.pipeline.layout import PageLayout
from app.pipeline.runner import _apply_layout_corrections
from app.source.base import DrawingSource


def _ctx_with_layout(layout: PageLayout | None) -> PipelineContext:
    """PipelineContext with a raster source so the whole-page fallback
    resolves to a known size."""
    img = Image.new("RGB", (800, 600), color="white")
    src = DrawingSource(
        kind="raster_image",
        pdf_doc=None,
        page=None,
        page_size_pt=None,
        raster_probe=img,
    )
    ctx = PipelineContext(drawing_id="t", original_filename="t.png")
    ctx.source = src
    ctx.layout = layout
    ctx.width_px, ctx.height_px = src.raster_probe.size
    return ctx


def test_runner_applies_layout_corrections() -> None:
    """A corrections dict on the categorize gate replaces ctx.layout fields.

    The frontend editor produces a corrections payload that arrives at
    the runner via the approve POST body. Each known PageLayout field
    in the payload replaces the categorizer's value before legend_parse
    runs.
    """
    initial = PageLayout(
        plan_view=(0.0, 0.0, 800.0, 600.0),
        legend=None,
        schedule=None,
        title_block=None,
        notes=[],
    )
    ctx = _ctx_with_layout(initial)

    corrections = {
        "plan_view": [50.0, 60.0, 700.0, 540.0],
        "legend": [710.0, 60.0, 790.0, 300.0],
        "schedule": [50.0, 550.0, 700.0, 590.0],
        "title_block": [600.0, 0.0, 790.0, 50.0],
        "notes": [
            [710.0, 310.0, 790.0, 400.0],
            [710.0, 410.0, 790.0, 500.0],
        ],
    }

    _apply_layout_corrections(ctx, corrections)

    assert ctx.layout is not None
    assert ctx.layout.plan_view == (50.0, 60.0, 700.0, 540.0)
    assert ctx.layout.legend == (710.0, 60.0, 790.0, 300.0)
    assert ctx.layout.schedule == (50.0, 550.0, 700.0, 590.0)
    assert ctx.layout.title_block == (600.0, 0.0, 790.0, 50.0)
    assert ctx.layout.notes == [
        (710.0, 310.0, 790.0, 400.0),
        (710.0, 410.0, 790.0, 500.0),
    ]


def test_runner_corrections_plan_view_null_falls_back_to_whole_page() -> None:
    """User-deleted plan_view → whole-page rect, mirroring §7 fallback.

    The pipeline's downstream contract is that ``layout.plan_view`` is
    non-None — tiled detect runs against it unconditionally. If the
    user deletes the plan_view rect in the editor (signalling "I don't
    know"), the runner substitutes the whole-page rect rather than
    propagating None.
    """
    initial = PageLayout(plan_view=(100.0, 100.0, 700.0, 500.0))
    ctx = _ctx_with_layout(initial)

    _apply_layout_corrections(ctx, {"plan_view": None})

    assert ctx.layout is not None
    # Raster source: whole-page rect = raster_probe size in pixel coords.
    assert ctx.layout.plan_view == (0.0, 0.0, 800.0, 600.0)


