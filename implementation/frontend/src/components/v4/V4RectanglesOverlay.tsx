/**
 * Rectangle-stage debug overlay. Each rectangle is drawn with a class that
 * reflects its filter outcome — kept rects in green, oversized drops in red,
 * non-duct-text drops in amber — so the operator can see what each filter ate.
 */

import type { DebugRectangle } from "../../types/v4";

interface Props {
  drawingW: number;
  drawingH: number;
  rectangles: DebugRectangle[];
  showDropped?: boolean;
}

export function V4RectanglesOverlay({
  drawingW, drawingH, rectangles, showDropped = true,
}: Props) {
  if (drawingW <= 0 || drawingH <= 0 || rectangles.length === 0) return null;
  return (
    <svg
      className="v4-rects-svg"
      viewBox={`0 0 ${drawingW} ${drawingH}`}
      preserveAspectRatio="xMinYMin meet"
    >
      {rectangles.map((rect, idx) => {
        if (!rect.kept && !showDropped) return null;
        const cls = rect.kept
          ? "v4-rect v4-rect--kept"
          : `v4-rect v4-rect--dropped v4-rect--${rect.drop_reason ?? "unknown"}`;
        return (
          <polygon
            key={idx}
            className={cls}
            points={rect.corners.map(([x, y]) => `${x},${y}`).join(" ")}
          />
        );
      })}
    </svg>
  );
}
