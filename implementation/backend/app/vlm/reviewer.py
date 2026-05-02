"""Reviewer seam — second-look agent boundary (SOLUTION-DESIGN-V2 §5.6, §6.1).

Stage 8 (`ReviewerStage`) calls into a ``ReviewerClient`` to judge each draft
segment against MEP domain priors and the legend conventions of the specific
drawing under analysis. Implementations are free to choose their own transport
— the only contract is a typed ``ReviewerVerdict`` back from ``review_segment``
and a discrete-only output (no continuous confidence scores; small models
fabricate them, see ADR-0009 §2).

The runtime shape ``ReviewerVerdict`` is intentionally an alias of the wire-
format ``ReviewSegmentTool`` (in ``app.vlm.tools``) so callers don't see two
near-identical pydantic models. The wire schema lives next to the rest of the
typed VLM tools; the Protocol lives here to keep the agent seams symmetric
with ``VLMClient`` (``app.vlm.base``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.vlm.tools import ReviewSegmentTool

if TYPE_CHECKING:
    from PIL.Image import Image

    from app.pipeline.base import VLMSegmentDraft
    from app.pipeline.legend import Legend


# Runtime alias — the same shape as the wire-format tool. Keeping a separate
# name here documents the boundary ("the reviewer returns a ReviewerVerdict")
# without duplicating the schema definition.
ReviewerVerdict = ReviewSegmentTool


class ReviewerClient(Protocol):
    """Per-segment reviewer agent.

    Mirrors the ``VLMClient`` Protocol layout: implementations decide how to
    talk to the underlying model (Ollama JSON-mode, Claude tool-use, a stub
    for tests). The Protocol guarantees only the typed contract.
    """

    def review_segment(
        self,
        crop: Image,
        segment: VLMSegmentDraft,
        legend: Legend | None,
    ) -> ReviewerVerdict:
        """Judge one segment crop. Discrete verdict + one-sentence reason.

        ``crop`` is a high-DPI render of the segment bbox + padding (rendered
        fresh from the source by the caller, not cached). ``segment`` is the
        draft as produced by tiled detect (geometry, shape_hint, nearby_text,
        any reasoning so far). ``legend`` is the drawing-specific symbol /
        line-style mapping; None means "use defaults".

        Implementations MAY raise; the calling stage catches per-segment
        exceptions and degrades that segment to "not_reviewed" without
        affecting other segments.
        """
        ...


__all__ = ["ReviewerClient", "ReviewerVerdict"]
