"""Shared encoding helpers for source images.

The categorize and tiling approval gates both need to ship a downscaled
PNG of the ingest-time ``raster_probe`` to the frontend so the editor has
a backdrop to draw on. Both gates use the same downscale + base64
pipeline; this module is the single source of truth so the two gate
payload builders can't drift apart.

Lives under ``app/source`` rather than ``app/pipeline`` because it
operates on ``DrawingSource`` outputs, not pipeline state — the helper
is a property of the source layer's encoding, not of any one stage.
"""

from __future__ import annotations

import base64
from io import BytesIO

from PIL.Image import Image as PILImage

# Matches ``app.pipeline.assemble``'s ``_DISPLAY_MAX_LONG_EDGE_PX`` —
# kept here so SSE payloads stay modest even for high-DPI inputs and
# the same cap applies regardless of which gate ships the data URL.
_DEFAULT_MAX_LONG_EDGE_PX = 1600


def raster_probe_data_url(
    image: PILImage, *, max_long_edge: int = _DEFAULT_MAX_LONG_EDGE_PX
) -> str:
    """Encode ``image`` as a downscaled PNG ``data:`` URL.

    Downscale is applied only when the image's long edge exceeds
    ``max_long_edge``; smaller images are encoded at native size. The
    resize uses PIL's default filter — quality is fine for a preview
    backdrop and the size cap is the binding constraint on payload size.
    """
    long_edge = max(image.size)
    if long_edge > max_long_edge:
        scale = max_long_edge / long_edge
        new_size = (int(image.width * scale), int(image.height * scale))
        image = image.resize(new_size)
    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
