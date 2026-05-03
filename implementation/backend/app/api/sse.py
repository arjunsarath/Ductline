"""SSE bridge for streaming pipeline progress (PR-D).

The pipeline runs synchronously; the FastAPI endpoint needs an async
generator to drive ``StreamingResponse``. This module bridges the two:

  • ``stream_detect()`` returns an async generator that yields SSE-formatted
    bytes — one event per pipeline progress callback, then a terminal
    ``result`` or ``error`` event.

  • The pipeline runs in a worker thread (``asyncio.to_thread``) and posts
    progress events to an ``asyncio.Queue`` via ``loop.call_soon_threadsafe``.
    The generator awaits items from the queue and yields them as SSE.

Why a queue and not a callback that yields directly: generators can't yield
from inside a function call (the progress callback is a function reference,
not a generator). A queue is the simplest thread-safe handoff.

The SSE event vocabulary:

  • ``progress`` — pipeline emitted a stage / tile / segment event. ``data``
    is ``{event: <name>, ...payload}``.
  • ``preliminary_result`` — detect → assemble finished, but the reviewer
    phase has not. ``data`` is ``{result: <DrawingResult>}`` with all
    segments at ``review_verdict: "not_reviewed"``. The stream stays open
    while the reviewer runs and emits ``segment_reviewed`` progress events.
  • ``result`` — pipeline (including the reviewer phase) finished. ``data``
    is the serialised final DrawingResult. Always preceded by exactly one
    ``preliminary_result`` event in a successful run.
  • ``error`` — pipeline raised. ``data`` is ``{message, status}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from app.api.sessions import SessionCancelled, registry
from app.pipeline.base import PipelineError
from app.pipeline.runner import DetectionPipeline

logger = logging.getLogger(__name__)

# How long the pipeline thread will block waiting for an approval before
# giving up and degrading the run. Generous — matches the session TTL so a
# distracted user has the same window as a disconnected client.
_APPROVAL_TIMEOUT_S = 30 * 60


def _sse_event(name: str, data: dict[str, Any]) -> bytes:
    """Format a single SSE event. ``\\n\\n`` is the SSE record terminator."""
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n".encode()


async def stream_detect(
    pipeline: DetectionPipeline,
    file_bytes: bytes,
    original_filename: str,
) -> AsyncGenerator[bytes, None]:
    """Run the pipeline in a worker thread, stream progress as SSE bytes.

    Yields a sequence of SSE records in this order:
      1. zero or more ``progress`` events (stage / tile / segment events,
         and ``awaiting_*`` events when a HITL gate is open)
      2. exactly one terminal event — either ``result`` (success) or
         ``error`` (a ``PipelineError`` propagated up; ``SessionCancelled``
         maps to status 499; non-pipeline exceptions surface as 500).

    A session is registered up-front so the API layer can resolve the
    drawing_id from approve/cancel POSTs before the pipeline reaches its
    first gate. The session is removed in ``finally`` regardless of how
    the run ends.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    drawing_id = str(uuid4())
    session = registry.create(drawing_id)

    # Sentinel value pushed once the worker thread completes — lets the
    # generator know there will be no more progress events to await.
    done_sentinel = ("__done__", {})

    def progress_callback(event: str, payload: dict[str, Any]) -> None:
        # Called from the worker thread. ``call_soon_threadsafe`` is the
        # documented way to bridge a sync thread → asyncio loop.
        loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

    def approval_gate(gate: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """HITL pause point — emit awaiting_* event, block until approved.

        Returns the corrections payload the client POSTed to the approve
        endpoint (empty dict if none) on approval, or ``None`` on timeout.
        Cancellation surfaces as ``SessionCancelled`` from
        ``wait_for_approval`` — re-raised so the pipeline runner sees it.
        """
        progress_callback(f"awaiting_{gate}_approval", payload)
        try:
            approved = session.wait_for_approval(  # type: ignore[arg-type]
                gate,  # type: ignore[arg-type]
                timeout=_APPROVAL_TIMEOUT_S,
            )
        except SessionCancelled:
            # Surface as a regular Python exception inside the pipeline
            # thread — the runner catches it, marks ctx.errors, and the
            # main except below converts it to an SSE error event.
            raise
        if not approved:
            return None
        # Approved — the client may have submitted inline corrections via
        # the POST body. ``approval_payloads`` was always reserved for
        # this; v1 of HITL kept it None, v2 (this PR) populates it. An
        # approve with no body stores nothing → empty dict back.
        return session.approval_payloads.get(gate, {})  # type: ignore[arg-type]

    def run_pipeline() -> Any:
        try:
            return pipeline.run(
                file_bytes,
                original_filename=original_filename,
                progress=progress_callback,
                approval_gate=approval_gate,
                drawing_id=drawing_id,
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, done_sentinel)

    # Schedule the pipeline on a worker thread. We don't await the future
    # yet — we drain the queue first so the client sees progress events.
    pipeline_task = asyncio.create_task(asyncio.to_thread(run_pipeline))

    try:
        try:
            while True:
                event, payload = await queue.get()
                if event == "__done__":
                    break
                # ``preliminary_result`` is hoisted to its own SSE event
                # type so the frontend can switch to the result view as
                # soon as detection finishes — the reviewer phase keeps
                # running and emits ``segment_reviewed`` progress events
                # over the same stream (SOLUTION-DESIGN-V2 §5.6).
                if event == "preliminary_result":
                    yield _sse_event("preliminary_result", payload)
                else:
                    yield _sse_event("progress", {"event": event, **payload})
        except asyncio.CancelledError:
            # Client disconnected mid-stream — cancel the session so the
            # pipeline thread unblocks any pending approval wait, then
            # re-raise so the StreamingResponse cleanup path runs.
            session.cancel()
            pipeline_task.cancel()
            raise

        try:
            result = await pipeline_task
            yield _sse_event("result", {"result": result.model_dump()})
        except SessionCancelled as exc:
            yield _sse_event("error", {"message": str(exc), "status": 499})
        except PipelineError as exc:
            logger.warning("stream_detect: pipeline error: %s", exc)
            yield _sse_event("error", {
                "message": str(exc),
                "status": exc.http_status,
            })
        except Exception as exc:  # noqa: BLE001 — surface as 500 SSE error
            logger.exception("stream_detect: unexpected error")
            yield _sse_event("error", {"message": str(exc), "status": 500})
    finally:
        registry.remove(drawing_id)
