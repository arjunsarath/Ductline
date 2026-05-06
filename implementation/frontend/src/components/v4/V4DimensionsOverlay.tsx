/**
 * Debug overlay highlighting OCR matches that parse as duct cross-section
 * dimensions (`N"ø` or `WxH`). Round in indigo, rectangular in teal. The
 * highlight is for troubleshooting only — not part of the production overlay.
 */

import type { DebugDimension } from "../../types/v4";

interface Props {
  drawingW: number;
  drawingH: number;
  dimensions: DebugDimension[];
}

export function V4DimensionsOverlay({ drawingW, drawingH, dimensions }: Props) {
  if (drawingW <= 0 || drawingH <= 0 || dimensions.length === 0) return null;
  return (
    <svg
      className="v4-dims-svg"
      viewBox={`0 0 ${drawingW} ${drawingH}`}
      preserveAspectRatio="xMinYMin meet"
    >
      {dimensions.map((d, idx) => {
        const [x, y, w, h] = d.bbox;
        return (
          <g key={idx} className={`v4-dim v4-dim--${d.kind}`}>
            <rect x={x} y={y} width={w} height={h} className="v4-dim-rect" />
            <text x={x} y={y - 4} className="v4-dim-text">
              {d.text}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
