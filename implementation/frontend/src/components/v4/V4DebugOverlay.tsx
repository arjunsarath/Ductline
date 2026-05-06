/**
 * Debug overlay rendered behind the kept-segment overlay when the user
 * toggles "Show all detections". Shows every polygon `detect_duct_polygons`
 * returned, colour-coded by classification so the operator can see what
 * survived each filter and why others did not.
 */

import type { DebugPolygon, V4Segment } from "../../types/v4";

interface Props {
  drawingW: number;
  drawingH: number;
  polygons: DebugPolygon[];
  segments: V4Segment[];
  inverseScale: number;
}

const STROKE_PX = 1.5;
const DASH_PX = 4;
const LABEL_OFFSET_PX = 4;
const LABEL_FONT_PX = 10;

interface ClassStyle {
  stroke: string;
  dash: string | undefined;
  className: string;
}

const CLASS_STYLES: Record<"kept" | NonNullable<DebugPolygon["drop_reason"]>, ClassStyle> = {
  kept: { stroke: "#16a34a", dash: undefined, className: "v4-dbg-kept" },
  shape_unknown: { stroke: "#9ca3af", dash: "dashed", className: "v4-dbg-shape" },
  diameter_out_of_range: { stroke: "#dc2626", dash: "dashed", className: "v4-dbg-dia" },
  no_label: { stroke: "#d97706", dash: "dashed", className: "v4-dbg-label" },
};

function styleFor(p: DebugPolygon): ClassStyle {
  if (p.kept) return CLASS_STYLES.kept;
  return CLASS_STYLES[p.drop_reason ?? "no_label"];
}

function pointsAttr(poly: [number, number][]): string {
  return poly.map(([x, y]) => `${x},${y}`).join(" ");
}

function labelText(p: DebugPolygon, parsed: string | null): string {
  if (parsed) return `${p.id} ${parsed}`;
  if (p.est_diameter_in != null) return `${p.id} ø${p.est_diameter_in}"`;
  return `${p.id} ?`;
}

export function V4DebugOverlay({
  drawingW,
  drawingH,
  polygons,
  segments,
  inverseScale,
}: Props) {
  const stroke = STROKE_PX * inverseScale;
  const dashLen = DASH_PX * inverseScale;
  const labelOffset = LABEL_OFFSET_PX * inverseScale;
  const fontPx = LABEL_FONT_PX * inverseScale;
  const segDimById = new Map(segments.map((s) => [s.id, s.dimension]));

  return (
    <svg
      className="v4-debug-overlay-svg"
      viewBox={`0 0 ${drawingW} ${drawingH}`}
      preserveAspectRatio="xMinYMin meet"
      aria-label="V4 debug detections overlay"
    >
      {polygons.map((p) => {
        const s = styleFor(p);
        const dashArray = s.dash ? `${dashLen} ${dashLen}` : undefined;
        const parsedDim = segDimById.get(p.id) ?? null;
        const [bx, by] = p.bbox;
        return (
          <g key={p.id} className={`v4-dbg ${s.className}`}>
            <polygon
              points={pointsAttr(p.polygon)}
              fill="none"
              stroke={s.stroke}
              strokeWidth={stroke}
              strokeDasharray={dashArray}
              vectorEffect="non-scaling-stroke"
            />
            <text
              x={bx}
              y={by - labelOffset}
              fontSize={fontPx}
              fill={s.stroke}
              className="v4-dbg-label-text"
            >
              {labelText(p, parsedDim)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function V4DebugLegend() {
  return (
    <div className="v4-debug-legend" aria-label="Debug overlay legend">
      <div className="v4-debug-legend-row">
        <span className="v4-debug-swatch" style={{ borderColor: "#16a34a" }} />
        <span>kept</span>
      </div>
      <div className="v4-debug-legend-row">
        <span
          className="v4-debug-swatch v4-debug-swatch-dashed"
          style={{ borderColor: "#9ca3af" }}
        />
        <span>shape unknown</span>
      </div>
      <div className="v4-debug-legend-row">
        <span
          className="v4-debug-swatch v4-debug-swatch-dashed"
          style={{ borderColor: "#dc2626" }}
        />
        <span>ø out of range</span>
      </div>
      <div className="v4-debug-legend-row">
        <span
          className="v4-debug-swatch v4-debug-swatch-dashed"
          style={{ borderColor: "#d97706" }}
        />
        <span>no label</span>
      </div>
    </div>
  );
}
