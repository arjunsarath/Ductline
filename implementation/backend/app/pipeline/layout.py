"""PageLayout — output of the Page Categorizer (SOLUTION-DESIGN-V2 §5.3, §6.1).

A drawing is decomposed into named regions. ``plan_view`` is always populated
— the categorizer falls back to the whole page when no plan view is identified
so downstream stages always have a non-None rect to tile against.

Coordinate space matches the source: PDF points (RectPt) for ``vector_pdf``
inputs, pixel rects expressed as the same RectPt tuple for ``raster_image``
and ``raster_pdf`` inputs. There is no separate pixel-rect type — see
``app.source.base.RectPt``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.source.base import RectPt


class PageLayout(BaseModel):
    """Named regions on a single drawing page (SOLUTION-DESIGN-V2 §6.1)."""

    title_block: RectPt | None = None
    schedule: RectPt | None = None
    legend: RectPt | None = None
    notes: list[RectPt] = Field(default_factory=list)
    # Always populated. Whole-page rect on categorizer-failed fallback so
    # tiled detect (PR-5) can run unconditionally — see §7 edge cases.
    plan_view: RectPt
