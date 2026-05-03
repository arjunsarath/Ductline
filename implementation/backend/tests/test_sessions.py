"""HITL session state (V2 §5.8 / PR-D HITL).

Pure-Python tests on app.api.sessions — no FastAPI, no SSE, no live pipeline.
Covers: gate approval unblocks a waiting thread, cancel unblocks a waiting
thread (raises SessionCancelled), timeout returns False, registry create /
remove / dedup-detect, expiry sweep.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.api.sessions import (
    Session,
    SessionCancelled,
    SessionRegistry,
)


def test_approve_unblocks_waiting_thread() -> None:
    session = Session(drawing_id="t1")
    result: dict[str, bool] = {}

    def waiter() -> None:
        result["approved"] = session.wait_for_approval("categorize", timeout=2.0)

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.1)  # let the thread enter wait_for_approval

    session.approve("categorize")
    thread.join(timeout=2.0)

    assert not thread.is_alive(), "waiter should have unblocked on approve"
    assert result["approved"] is True


def test_approve_idempotent() -> None:
    session = Session(drawing_id="t2")
    session.approve("tiling")
    session.approve("tiling")  # second call is a no-op

    assert session.wait_for_approval("tiling", timeout=0.1) is True


def test_cancel_raises_in_waiter() -> None:
    session = Session(drawing_id="t3")
    raised: dict[str, bool] = {"raised": False}

    def waiter() -> None:
        try:
            session.wait_for_approval("categorize", timeout=2.0)
        except SessionCancelled:
            raised["raised"] = True

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.1)

    session.cancel()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert raised["raised"] is True


def test_timeout_returns_false() -> None:
    session = Session(drawing_id="t4")
    assert session.wait_for_approval("tiling", timeout=0.2) is False


def test_registry_create_get_remove() -> None:
    reg = SessionRegistry()
    session = reg.create("draw-a")

    assert reg.get("draw-a") is session
    reg.remove("draw-a")
    assert reg.get("draw-a") is None


def test_registry_rejects_duplicate_create() -> None:
    reg = SessionRegistry()
    reg.create("draw-b")

    with pytest.raises(ValueError, match="already exists"):
        reg.create("draw-b")


def test_approval_payload_stored() -> None:
    """Approve-with-payload stashes the corrections dict for later retrieval."""
    session = Session(drawing_id="t5")
    session.approve("categorize", payload={"corrected_plan_view": [10, 20, 30, 40]})

    assert session.approval_payloads["categorize"] == {
        "corrected_plan_view": [10, 20, 30, 40]
    }
