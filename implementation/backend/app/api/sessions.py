"""Per-drawing pipeline sessions for human-in-the-loop approval gates.

The base pipeline runs synchronously to completion; this module adds a
session layer so the pipeline can pause at named gates and wait for the
client to POST an approval. Today there are two gates:

  • ``categorize`` — between page_categorize and legend_parse, so the user
    can confirm the categorizer's plan_view / legend / heading rects.
  • ``tiling`` — inside duct_detect_tiled after tile rects are computed
    but before any VLM call, so the user can confirm the tile grid (size,
    DPI, count) before paying the inference cost.

Sessions are kept in process memory keyed by drawing_id. Single-instance
backend; no horizontal scaling. A simple ``threading.Event`` per gate
gives the pipeline thread a place to block; the API thread calls
``session.approve(gate)`` to release it. ``cancel()`` raises a
``SessionCancelled`` exception inside the pipeline thread on the next
gate or sweep.

Session entries auto-expire after ``_SESSION_TTL_S`` so a client that
disconnects mid-pause doesn't leak. The TTL is generous (30 min) — the
pipeline owns the session for as long as it runs and a 15-min run with
two human pauses fits comfortably.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.pipeline.runner_v4 import run_v4
from app.schemas import OperationalVars, ScaleInfo, V4Result

logger = logging.getLogger(__name__)

# Gate identifiers. Keep this small and explicit — adding a gate is a
# pipeline change, not a config change.
GateName = Literal["categorize", "tiling"]
_GATE_NAMES: tuple[GateName, ...] = ("categorize", "tiling")

# Sessions older than this are considered abandoned and reclaimed on the
# next sweep. 30 min is well above the worst-case wall-clock of a
# benchmark run including human approval pauses.
_SESSION_TTL_S = 30 * 60


class SessionCancelled(Exception):  # noqa: N818 — domain term, not a generic Error
    """Raised inside the pipeline thread when the client cancels.

    The runner catches this at the top level and reports it as a
    pipeline error (status 499 — client closed request) via the SSE
    bridge.
    """


@dataclass
class Session:
    """One in-flight detection request with HITL approval gates."""

    drawing_id: str
    created_at: float = field(default_factory=time.monotonic)
    cancelled: bool = False
    # Per-gate approval Event. ``set`` ⇒ the gate has been passed.
    # Pre-creating Events for every gate avoids races where the pipeline
    # reaches a gate before the API has registered it.
    _gates: dict[GateName, threading.Event] = field(
        default_factory=lambda: {name: threading.Event() for name in _GATE_NAMES}
    )
    # ``approval_payloads`` carry optional corrections submitted with an
    # approve POST. v1 of HITL is approve-only — corrections are reserved
    # for the future and ignored here. The dict shape stays stable so
    # adding a corrections protocol later doesn't break callers.
    approval_payloads: dict[GateName, dict] = field(default_factory=dict)

    def wait_for_approval(self, gate: GateName, timeout: float | None = None) -> bool:
        """Block until the gate is approved or the session is cancelled.

        Returns True on approval, False on timeout. Raises
        ``SessionCancelled`` if the session was cancelled while waiting.
        """
        event = self._gates[gate]
        # Loop with a small wait granularity so cancellation is responsive.
        # ``wait(0.5)`` returns True on set, False on timeout — we re-check
        # the cancelled flag between waits.
        deadline = None if timeout is None else (time.monotonic() + timeout)
        while True:
            if self.cancelled:
                raise SessionCancelled(f"session {self.drawing_id} cancelled")
            remaining = 0.5 if deadline is None else min(0.5, deadline - time.monotonic())
            if remaining <= 0:
                return False
            if event.wait(timeout=remaining):
                if self.cancelled:
                    raise SessionCancelled(f"session {self.drawing_id} cancelled")
                return True

    def approve(self, gate: GateName, payload: dict | None = None) -> None:
        """Mark a gate as approved. Idempotent — re-approving is a no-op."""
        if payload is not None:
            self.approval_payloads[gate] = payload
        self._gates[gate].set()

    def cancel(self) -> None:
        """Cancel the session. Wakes any pipeline thread waiting on a gate."""
        self.cancelled = True
        # Wake every gate so the pipeline thread sees the cancelled flag
        # on its next check. Spurious wake-ups are safe — the wait loop
        # re-reads ``cancelled`` before continuing.
        for event in self._gates.values():
            event.set()

    def is_expired(self, now: float) -> bool:
        return now - self.created_at > _SESSION_TTL_S


class SessionRegistry:
    """Process-wide session store. Thread-safe via a single lock.

    The registry is a singleton in practice (see module-level ``registry``).
    Tests can construct their own instances for isolation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}

    def create(self, drawing_id: str) -> Session:
        with self._lock:
            self._sweep_expired_locked()
            if drawing_id in self._sessions:
                # Drawing IDs are uuid4 — collision is effectively
                # impossible. If it happens we treat it as a programming
                # error rather than silently overwriting.
                raise ValueError(f"session {drawing_id} already exists")
            session = Session(drawing_id=drawing_id)
            self._sessions[drawing_id] = session
            logger.info("session: created drawing_id=%s", drawing_id)
            return session

    def get(self, drawing_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(drawing_id)

    def remove(self, drawing_id: str) -> None:
        with self._lock:
            if drawing_id in self._sessions:
                del self._sessions[drawing_id]
                logger.info("session: removed drawing_id=%s", drawing_id)

    def _sweep_expired_locked(self) -> None:
        """Reclaim expired sessions. Caller must hold ``self._lock``."""
        now = time.monotonic()
        expired = [d for d, s in self._sessions.items() if s.is_expired(now)]
        for drawing_id in expired:
            logger.warning("session: reaping expired drawing_id=%s", drawing_id)
            self._sessions[drawing_id].cancel()
            del self._sessions[drawing_id]


# Singleton — the API layer and the SSE bridge share this.
registry = SessionRegistry()


# ---------------------------------------------------------------------------
# V4 endpoint — additive over V3 (SOLUTION-DESIGN-V4 §4 “api/sessions.py”).
# The V4 path runs to completion synchronously; no HITL gates today, so it
# does not interact with the SessionRegistry above. The router lives here to
# match the design's module map.
# ---------------------------------------------------------------------------

router = APIRouter()

_V4_PDF_SUFFIXES = {".pdf"}


@router.post("/sessions", response_model=V4Result)
async def create_v4_session(
    file: Annotated[UploadFile, File(...)],
    op_vars: Annotated[str | None, Form()] = None,
    scale_override: Annotated[str | None, Form()] = None,
    source_node_id: Annotated[str | None, Form()] = None,
    debug: Annotated[bool, Form()] = False,
    min_aspect_ratio: Annotated[float | None, Form()] = None,
    min_white_pct: Annotated[float | None, Form()] = None,
    enable_oversized: Annotated[bool, Form()] = True,
    enable_aspect_ratio: Annotated[bool, Form()] = False,
    enable_interior: Annotated[bool, Form()] = False,
    enable_content: Annotated[bool, Form()] = False,
    enable_rectangle: Annotated[bool, Form()] = True,
    epsilon_frac: Annotated[float | None, Form()] = None,
    max_corner_cos: Annotated[float | None, Form()] = None,
    crop_x: Annotated[int | None, Form()] = None,
    crop_y: Annotated[int | None, Form()] = None,
    crop_w: Annotated[int | None, Form()] = None,
    crop_h: Annotated[int | None, Form()] = None,
    stop_after: Annotated[str | None, Form()] = None,
    dpi: Annotated[int | None, Form()] = None,
    max_vlm_crops: Annotated[int | None, Form()] = None,
    enable_vlm_ocr: Annotated[bool, Form()] = False,
    ink_threshold: Annotated[int | None, Form()] = None,
    rect_dpi: Annotated[int, Form()] = 100,
    ocr_dpi: Annotated[int, Form()] = 600,
    enable_min_ink: Annotated[bool, Form()] = True,
    min_ink_pct: Annotated[float | None, Form()] = None,
    enable_max_ink: Annotated[bool, Form()] = True,
    max_ink_pct: Annotated[float | None, Form()] = None,
    enable_squarish: Annotated[bool, Form()] = True,
    min_duct_aspect: Annotated[float | None, Form()] = None,
    enable_circle: Annotated[bool, Form()] = False,
    min_circularity: Annotated[float | None, Form()] = None,
    enable_divider: Annotated[bool, Form()] = False,
    min_divider_ink_pct: Annotated[float | None, Form()] = None,
    enable_three_digit: Annotated[bool, Form()] = False,
) -> V4Result:
    """Run the V4 pipeline on an uploaded PDF and return the typed result.

    ``op_vars`` and ``scale_override`` are JSON strings (multipart fields are
    text-only). Empty / missing → defaults from the runner.
    """
    filename = file.filename or "uploaded.pdf"
    suffix = Path(filename).suffix.lower()
    if suffix not in _V4_PDF_SUFFIXES:
        raise HTTPException(status_code=400, detail="V4 accepts PDF uploads only")

    parsed_vars = _parse_op_vars(op_vars)
    parsed_scale = _parse_scale_override(scale_override)
    source = source_node_id or None

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty upload")

    with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)
    try:
        crop = _crop_tuple(crop_x, crop_y, crop_w, crop_h)
        return run_v4(
            tmp_path,
            op_vars=parsed_vars,
            scale_override=parsed_scale,
            source_node_id=source,
            debug=debug,
            min_aspect_ratio=min_aspect_ratio,
            min_white_pct=min_white_pct,
            enable_oversized=enable_oversized,
            enable_aspect_ratio=enable_aspect_ratio,
            enable_interior=enable_interior,
            enable_content=enable_content,
            enable_rectangle=enable_rectangle,
            epsilon_frac=epsilon_frac,
            max_corner_cos=max_corner_cos,
            crop_area=crop,
            stop_after=stop_after,
            dpi_override=dpi,
            max_vlm_crops=max_vlm_crops,
            enable_vlm_ocr=enable_vlm_ocr,
            ink_threshold=ink_threshold,
            rect_dpi=rect_dpi,
            ocr_dpi=ocr_dpi,
            enable_min_ink=enable_min_ink,
            min_ink_pct=min_ink_pct,
            enable_max_ink=enable_max_ink,
            max_ink_pct=max_ink_pct,
            enable_squarish=enable_squarish,
            min_duct_aspect=min_duct_aspect,
            enable_circle=enable_circle,
            min_circularity=min_circularity,
            enable_divider=enable_divider,
            min_divider_ink_pct=min_divider_ink_pct,
            enable_three_digit=enable_three_digit,
        )
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/sessions/stream")
async def create_v4_session_stream(
    file: Annotated[UploadFile, File(...)],
    op_vars: Annotated[str | None, Form()] = None,
    scale_override: Annotated[str | None, Form()] = None,
    source_node_id: Annotated[str | None, Form()] = None,
    debug: Annotated[bool, Form()] = False,
    min_aspect_ratio: Annotated[float | None, Form()] = None,
    min_white_pct: Annotated[float | None, Form()] = None,
    enable_oversized: Annotated[bool, Form()] = True,
    enable_aspect_ratio: Annotated[bool, Form()] = False,
    enable_interior: Annotated[bool, Form()] = False,
    enable_content: Annotated[bool, Form()] = False,
    enable_rectangle: Annotated[bool, Form()] = True,
    epsilon_frac: Annotated[float | None, Form()] = None,
    max_corner_cos: Annotated[float | None, Form()] = None,
    crop_x: Annotated[int | None, Form()] = None,
    crop_y: Annotated[int | None, Form()] = None,
    crop_w: Annotated[int | None, Form()] = None,
    crop_h: Annotated[int | None, Form()] = None,
    stop_after: Annotated[str | None, Form()] = None,
    dpi: Annotated[int | None, Form()] = None,
    max_vlm_crops: Annotated[int | None, Form()] = None,
    enable_vlm_ocr: Annotated[bool, Form()] = False,
    ink_threshold: Annotated[int | None, Form()] = None,
    rect_dpi: Annotated[int, Form()] = 100,
    ocr_dpi: Annotated[int, Form()] = 600,
    enable_min_ink: Annotated[bool, Form()] = True,
    min_ink_pct: Annotated[float | None, Form()] = None,
    enable_max_ink: Annotated[bool, Form()] = True,
    max_ink_pct: Annotated[float | None, Form()] = None,
    enable_squarish: Annotated[bool, Form()] = True,
    min_duct_aspect: Annotated[float | None, Form()] = None,
    enable_circle: Annotated[bool, Form()] = False,
    min_circularity: Annotated[float | None, Form()] = None,
    enable_divider: Annotated[bool, Form()] = False,
    min_divider_ink_pct: Annotated[float | None, Form()] = None,
    enable_three_digit: Annotated[bool, Form()] = False,
) -> StreamingResponse:
    """Run V4 and stream NDJSON progress events; final 'done' carries V4Result."""
    filename = file.filename or "uploaded.pdf"
    suffix = Path(filename).suffix.lower()
    if suffix not in _V4_PDF_SUFFIXES:
        raise HTTPException(status_code=400, detail="V4 accepts PDF uploads only")

    parsed_vars = _parse_op_vars(op_vars)
    parsed_scale = _parse_scale_override(scale_override)
    source = source_node_id or None

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty upload")

    crop = _crop_tuple(crop_x, crop_y, crop_w, crop_h)
    return StreamingResponse(
        _stream_v4(
            file_bytes, parsed_vars, parsed_scale, source, debug,
            min_aspect_ratio, min_white_pct,
            enable_oversized, enable_aspect_ratio, enable_interior, enable_content,
            enable_rectangle, epsilon_frac, max_corner_cos,
            crop, stop_after, dpi,
            max_vlm_crops, enable_vlm_ocr, ink_threshold,
            rect_dpi, ocr_dpi,
            enable_min_ink, min_ink_pct, enable_max_ink, max_ink_pct,
            enable_squarish, min_duct_aspect,
            enable_circle, min_circularity,
            enable_divider, min_divider_ink_pct,
            enable_three_digit,
        ),
        media_type="application/x-ndjson",
    )


def _crop_tuple(
    x: int | None, y: int | None, w: int | None, h: int | None,
) -> tuple[int, int, int, int] | None:
    """Bundle crop form fields into a tuple, or None if any are missing/zero."""
    if None in (x, y, w, h) or (w or 0) <= 0 or (h or 0) <= 0:
        return None
    return (int(x or 0), int(y or 0), int(w or 0), int(h or 0))


_STREAM_SENTINEL = object()


async def _stream_v4(
    pdf_bytes: bytes,
    op_vars: OperationalVars | None,
    scale_override: ScaleInfo | None,
    source_node_id: str | None,
    debug: bool = False,
    min_aspect_ratio: float | None = None,
    min_white_pct: float | None = None,
    enable_oversized: bool = True,
    enable_aspect_ratio: bool = False,
    enable_interior: bool = False,
    enable_content: bool = False,
    enable_rectangle: bool = True,
    epsilon_frac: float | None = None,
    max_corner_cos: float | None = None,
    crop_area: tuple[int, int, int, int] | None = None,
    stop_after: str | None = None,
    dpi_override: int | None = None,
    max_vlm_crops: int | None = None,
    enable_vlm_ocr: bool = False,
    ink_threshold: int | None = None,
    rect_dpi: int = 100,
    ocr_dpi: int = 600,
    enable_min_ink: bool = True,
    min_ink_pct: float | None = None,
    enable_max_ink: bool = True,
    max_ink_pct: float | None = None,
    enable_squarish: bool = True,
    min_duct_aspect: float | None = None,
    enable_circle: bool = False,
    min_circularity: float | None = None,
    enable_divider: bool = False,
    min_divider_ink_pct: float | None = None,
    enable_three_digit: bool = False,
) -> AsyncIterator[bytes]:
    """Bridge the sync runner's progress callback to an async NDJSON stream."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[object] = asyncio.Queue()

    def on_progress(_stage: str, payload: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, payload)

    with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = Path(tmp.name)

    async def runner_task() -> None:
        try:
            result = await asyncio.to_thread(
                run_v4,
                tmp_path,
                op_vars=op_vars,
                scale_override=scale_override,
                source_node_id=source_node_id,
                progress=on_progress,
                debug=debug,
                min_aspect_ratio=min_aspect_ratio,
                min_white_pct=min_white_pct,
                enable_oversized=enable_oversized,
                enable_aspect_ratio=enable_aspect_ratio,
                enable_interior=enable_interior,
                enable_content=enable_content,
                enable_rectangle=enable_rectangle,
                epsilon_frac=epsilon_frac,
                max_corner_cos=max_corner_cos,
                crop_area=crop_area,
                stop_after=stop_after,
                dpi_override=dpi_override,
                max_vlm_crops=max_vlm_crops,
                enable_vlm_ocr=enable_vlm_ocr,
                ink_threshold=ink_threshold,
                rect_dpi=rect_dpi,
                ocr_dpi=ocr_dpi,
                enable_min_ink=enable_min_ink,
                min_ink_pct=min_ink_pct,
                enable_max_ink=enable_max_ink,
                max_ink_pct=max_ink_pct,
                enable_squarish=enable_squarish,
                min_duct_aspect=min_duct_aspect,
                enable_circle=enable_circle,
                min_circularity=min_circularity,
                enable_divider=enable_divider,
                min_divider_ink_pct=min_divider_ink_pct,
                enable_three_digit=enable_three_digit,
            )
            queue.put_nowait({
                "stage": "result",
                "payload": json.loads(result.model_dump_json()),
            })
        except Exception as exc:  # surfaced to the client as a final error event
            logger.exception("v4: stream pipeline failed")
            queue.put_nowait({"stage": "error", "message": str(exc)})
        finally:
            tmp_path.unlink(missing_ok=True)
            queue.put_nowait(_STREAM_SENTINEL)

    task = asyncio.create_task(runner_task())
    try:
        result_payload: dict | None = None
        error_payload: dict | None = None
        while True:
            item = await queue.get()
            if item is _STREAM_SENTINEL:
                break
            assert isinstance(item, dict)
            stage = item.get("stage")
            if stage == "result":
                result_payload = item["payload"]
                continue
            if stage == "error":
                error_payload = item
                continue
            if stage == "done":
                # Stream bridge emits its own terminal 'done' carrying the
                # V4Result; suppress the runner's progress 'done' so the
                # client sees exactly one terminal event.
                continue
            yield (json.dumps(item) + "\n").encode("utf-8")

        if error_payload is not None:
            yield (json.dumps(error_payload) + "\n").encode("utf-8")
            return
        if result_payload is None:
            yield (json.dumps({"stage": "error",
                               "message": "pipeline ended without result"}) + "\n").encode("utf-8")
            return
        # The 'done' event carries the full V4Result so the client doesn't
        # need a second request.
        done_event = {"stage": "done", "message": "pipeline complete", "result": result_payload}
        yield (json.dumps(done_event) + "\n").encode("utf-8")
    finally:
        await task


def _parse_op_vars(raw: str | None) -> OperationalVars | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"op_vars malformed: {exc}") from exc
    try:
        return OperationalVars(**payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"op_vars invalid: {exc}") from exc


def _parse_scale_override(raw: str | None) -> ScaleInfo | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"scale_override malformed: {exc}") from exc
    try:
        return ScaleInfo(**payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail=f"scale_override invalid: {exc}"
        ) from exc
