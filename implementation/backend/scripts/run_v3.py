"""V3 pipeline CLI — run end-to-end on a PDF/image with a config of color picks.

Usage:
    uv run python -m scripts.run_v3 \
        --pdf path/to/drawing.pdf \
        --config path/to/picks.yaml \
        [--out result.json] [--overlay overlay.png]

The picks YAML matches the V3PipelineConfig.picks shape (V3 §5.4):

    picks:
      - label: "Supply Air"
        pattern: outline      # or "centerline"
        kind: supply
        primary:
          h_lo: 100; h_hi: 130; s_lo: 80; s_hi: 255; v_lo: 80; v_hi: 255
        display_color_bgr: [255, 0, 0]
        # optional second_range: (for hue-wraparound colors like red)
        # second:
        #   h_lo: 170; h_hi: 180; s_lo: 80; s_hi: 255; v_lo: 80; v_hi: 255
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import cv2
import yaml

from app.api.deps import build_ocr
from app.pipeline.v3.config import ColorPick, HSVRange, V3PipelineConfig
from app.pipeline.v3.render import render_overlay
from app.pipeline.v3.runner import V3DetectionPipeline, V3Result


def _load_config(path: Path) -> V3PipelineConfig:
    raw = yaml.safe_load(path.read_text())
    picks = []
    for i, p in enumerate(raw.get("picks", [])):
        primary = p["primary"]
        primary_range = HSVRange(
            lo=(primary["h_lo"], primary["s_lo"], primary["v_lo"]),
            hi=(primary["h_hi"], primary["s_hi"], primary["v_hi"]),
        )
        secondary = None
        if "second" in p:
            s = p["second"]
            secondary = HSVRange(
                lo=(s["h_lo"], s["s_lo"], s["v_lo"]),
                hi=(s["h_hi"], s["s_hi"], s["v_hi"]),
            )
        display = p.get("display_color_bgr", [0, 200, 0])
        picks.append(ColorPick(
            label=p["label"],
            primary_range=primary_range,
            second_range=secondary,
            pattern=p.get("pattern", "outline"),
            kind=p.get("kind", "other"),
            display_color_bgr=tuple(display),
            system_id=p.get("system_id", f"sys_{i:02d}"),
        ))
    return V3PipelineConfig(picks=picks)




def main() -> int:
    p = argparse.ArgumentParser(description="Run V3 pipeline on one drawing.")
    p.add_argument("--pdf", required=True, type=Path)
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--out", type=Path, default=Path("v3_result.json"))
    p.add_argument("--overlay", type=Path, default=None,
                   help="If set, render an overlay PNG with segment markers")
    args = p.parse_args()

    config = _load_config(args.config)
    file_bytes = args.pdf.read_bytes()
    ocr = build_ocr()
    pipe = V3DetectionPipeline(ocr=ocr)
    result, artifacts = pipe.run_with_artifacts(file_bytes, args.pdf.name, config)

    # Print summary
    print(f"\n=== V3 result for {args.pdf.name} ===")
    print(f"  page: {result.width_px}x{result.height_px} @ {result.target_dpi} DPI, rot={result.rotation_applied}")
    print(f"  page unit: {result.page_unit}")
    print("  systems:")
    for s in result.systems:
        print(f"    {s.system_id:<24s} pat={s.pattern:<10s} mask_px={s.mask_pixels:>7d} "
              f"filled_px={s.filled_pixels:>7d} segs={s.n_segments}")
    print(f"  OCR: total={result.n_tokens_total}, dim_rect={result.n_dim_rect_tokens}, flow={result.n_flow_tokens}")
    print(f"  attributed dim_rect (in-mask): {result.n_attributed_rect} / {result.n_dim_rect_tokens}")
    print(f"  attributed flow (proximity):  {result.n_attributed_flow} / {result.n_flow_tokens}")
    cal = result.calibration
    print(f"  calibration: ppu={cal.ppu}  n_pairs={cal.n_pairs}  n_in_band={cal.n_in_band}")
    print(f"  segments: {len(result.segments)}")

    # Confidence distribution
    by_conf = {"high": 0, "medium": 0, "low": 0}
    for seg in result.segments:
        by_conf[seg.dim_confidence] += 1
    print(f"  confidence: {by_conf}")
    by_pc = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for seg in result.segments:
        by_pc[seg.pressure.value] += 1
    print(f"  pressure:   {by_pc}")

    # Distinct sizes recovered
    sizes = {}
    for seg in result.segments:
        if seg.dim_confidence == "high":
            key = (min(seg.visible_unit, seg.hidden_unit), max(seg.visible_unit, seg.hidden_unit))
            sizes.setdefault(key, 0)
            sizes[key] += 1
    print(f"  distinct duct sizes (high-confidence): {len(sizes)}")
    for (a, b), n in sorted(sizes.items(), key=lambda kv: -kv[1])[:10]:
        print(f"     {a}x{b:<3}  ×{n}")

    if result.errors:
        print(f"  errors: {result.errors}")

    # Serialize to JSON
    args.out.write_text(json.dumps(_to_jsonable(result), indent=2))
    print(f"\n  → JSON: {args.out}")

    # Overlay if requested — uses the pipeline's render artifacts so we
    # never re-render or re-mask. Coordinates baked into the PNG match
    # the JSON's skel_xy and are in rendered-page space.
    if args.overlay and artifacts is not None:
        overlay = render_overlay(artifacts.rendered_bgr, artifacts.system_masks, result)
        cv2.imwrite(str(args.overlay), overlay)
        print(f"  → overlay: {args.overlay}")
    elif args.overlay:
        print("  → overlay skipped (pipeline aborted before producing artifacts)")

    return 0


def _to_jsonable(result: V3Result) -> dict:
    """asdict() doesn't handle numpy / tuples-as-keys cleanly; do it explicitly."""
    return {
        "drawing_id": result.drawing_id,
        "width_px": result.width_px,
        "height_px": result.height_px,
        "rotation_applied": result.rotation_applied,
        "page_unit": result.page_unit,
        "ppu": result.ppu,
        "target_dpi": result.target_dpi,
        "rendered_size": list(result.rendered_size),
        "systems": [asdict(s) for s in result.systems],
        "segments": [
            {
                **{k: v for k, v in asdict(seg).items() if k != "pressure" and k != "skel_xy"},
                "skel_xy": list(seg.skel_xy),
                "pressure": asdict(seg.pressure),
            }
            for seg in result.segments
        ],
        "n_tokens_total": result.n_tokens_total,
        "n_dim_rect_tokens": result.n_dim_rect_tokens,
        "n_flow_tokens": result.n_flow_tokens,
        "n_attributed_rect": result.n_attributed_rect,
        "calibration": asdict(result.calibration),
        "errors": result.errors,
    }


if __name__ == "__main__":
    sys.exit(main())
