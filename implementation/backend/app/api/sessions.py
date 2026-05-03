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

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

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
