"""V4 pipeline CLI — run end-to-end on a PDF and print result JSON to stdout.

Mirrors ``scripts/run_v3.py`` so the V4 acceptance run uses the same surface
as the API. JSON shape is the V4Result Pydantic model dumped via
``model_dump_json``; downstream tooling (notebooks, docs) parses it with
``json.loads`` like the V3 output.

Usage::

    .venv/bin/python scripts/run_v4.py --pdf path/to/drawing.pdf > result.json
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.pipeline.runner_v4 import run_v4


def main() -> int:
    parser = argparse.ArgumentParser(description="Run V4 pipeline on one drawing.")
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument(
        "--log-level",
        default="WARNING",
        help="root logger level (DEBUG/INFO/WARNING/ERROR)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if not args.pdf.exists():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 2

    result = run_v4(args.pdf)
    sys.stdout.write(result.model_dump_json(indent=2))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
