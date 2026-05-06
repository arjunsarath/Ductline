/**
 * SVG overlay layer for the V4 viewer. Renders segment centerline polylines
 * and terminal circles in page-pixel space using a viewBox sized to the
 * drawing — the parent .viewer-content transform handles zoom/pan/rotation
 * uniformly with the underlying page raster, so screen coordinates stay in
 * sync without hand-rolled inverse maths.
 *
 * Hit-testing: SVG handles point-in-stroke for polylines (with a fixed
 * `pointer-events: stroke` and a thick invisible hit polyline drawn on top
 * of the visible one) and point-in-circle for terminals natively. Per design
 * §7 + brief: closest polyline within ~8 screen px, terminal within ~12 px.
 * Stroke widths are scaled inversely by viewport scale so the hit tolerance
 * stays roughly constant in screen space at any zoom.
 */

import type { V4Segment, V4Terminal } from "../../types/v4";

export type V4Selection =
  | { kind: "segment"; id: string }
  | { kind: "terminal"; id: string }
  | null;

interface Props {
  drawingW: number;
  drawingH: number;
  segments: V4Segment[];
  terminals: V4Terminal[];
  selection: V4Selection;
  /** Inverse of viewport.scale so visual stroke widths stay constant in
   *  screen pixels when the parent transform scales the SVG. */
  inverseScale: number;
  onSelect: (next: V4Selection) => void;
}

const HIT_STROKE_PX = 16; // ≈ 8 px hit tolerance on either side of centerline
const VISIBLE_STROKE_PX = 2.5;
const TERMINAL_HIT_RADIUS_BONUS_PX = 4;

export function V4Overlay({
  drawingW,
  drawingH,
  segments,
  terminals,
  selection,
  inverseScale,
  onSelect,
}: Props) {
  const hitStroke = HIT_STROKE_PX * inverseScale;
  const visStroke = VISIBLE_STROKE_PX * inverseScale;
  const termBonus = TERMINAL_HIT_RADIUS_BONUS_PX * inverseScale;

  return (
    <svg
      className="v4-overlay-svg"
      viewBox={`0 0 ${drawingW} ${drawingH}`}
      preserveAspectRatio="xMinYMin meet"
      aria-label="V4 duct overlay"
    >
      {segments.map((seg) => {
        const isSel = selection?.kind === "segment" && selection.id === seg.id;
        const points = seg.polygon
          .map(([x, y]) => `${x},${y}`)
          .join(" ");
        const klass = `v4-seg pc-${seg.pressure.smacna_class.toLowerCase()}${
          isSel ? " is-selected" : ""
        }`;
        return (
          <g
            key={seg.id}
            className={klass}
            onClick={(event) => {
              event.stopPropagation();
              onSelect({ kind: "segment", id: seg.id });
            }}
          >
            {/* Visible centerline */}
            <polyline
              points={points}
              fill="none"
              strokeWidth={visStroke}
              vectorEffect="non-scaling-stroke"
              className="v4-seg-line"
            />
            {/* Wide invisible hit target — kept separate so the visible line
             *  can stay thin while clicks remain forgiving. */}
            <polyline
              points={points}
              fill="none"
              stroke="transparent"
              strokeWidth={hitStroke}
              strokeLinecap="round"
              strokeLinejoin="round"
              className="v4-seg-hit"
            />
          </g>
        );
      })}

      {terminals.map((t) => {
        const isSel = selection?.kind === "terminal" && selection.id === t.id;
        return (
          <g
            key={t.id}
            className={`v4-term${isSel ? " is-selected" : ""}`}
            onClick={(event) => {
              event.stopPropagation();
              onSelect({ kind: "terminal", id: t.id });
            }}
          >
            <circle
              cx={t.center[0]}
              cy={t.center[1]}
              r={t.radius}
              className="v4-term-circle"
              strokeWidth={visStroke}
              vectorEffect="non-scaling-stroke"
            />
            {/* Hit target — radius padded for the 12-px screen-space tolerance. */}
            <circle
              cx={t.center[0]}
              cy={t.center[1]}
              r={t.radius + termBonus}
              fill="transparent"
            />
          </g>
        );
      })}
    </svg>
  );
}
