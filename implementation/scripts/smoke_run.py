"""Smoke run — POST every drawing in implementation/drawings/ to /detect and
print a summary line per drawing. Used for the day-1 benchmark sweep
(SOLUTION-DESIGN §10 day-1 hour 7:15–8:00 + §10 day-2 hour 4:00–5:00).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

DRAWINGS = Path(__file__).parent.parent / "drawings"
ENDPOINT = "http://localhost:8000/detect"


def main() -> int:
    files = sorted(DRAWINGS.glob("*.pdf"))
    if not files:
        print("No drawings found in", DRAWINGS, file=sys.stderr)
        return 1

    print(f"running {len(files)} drawings against {ENDPOINT}\n")
    for path in files:
        run_one(path)
    return 0


def run_one(path: Path) -> None:
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=600.0) as client:
            with path.open("rb") as fh:
                response = client.post(
                    ENDPOINT, files={"file": (path.name, fh, "application/pdf")}
                )
        elapsed = time.perf_counter() - started

        if response.status_code != 200:
            print(f"❌ {path.name:<35} HTTP {response.status_code}: {response.text[:120]}")
            return

        body = response.json()
        agg = body["aggregate"]
        quality = body["quality"]["overall"]
        errors = body.get("errors") or []
        size_kb = len(response.content) // 1024

        print(
            f"✓ {path.name:<35} "
            f"segments={agg['total']:>3}  "
            f"PC={agg['by_pressure_class']}  "
            f"conf={agg['by_confidence']}  "
            f"quality={quality}  "
            f"{elapsed:5.1f}s  "
            f"resp={size_kb}KB"
        )
        for err in errors:
            print(f"    ↳ {err}")
    except Exception as exc:  # noqa: BLE001 — surface any failure
        elapsed = time.perf_counter() - started
        print(f"❌ {path.name:<35} {type(exc).__name__}: {exc} ({elapsed:.1f}s)")


if __name__ == "__main__":
    sys.exit(main())
