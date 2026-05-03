/**
 * ApprovalPanel — renders an open HITL approval gate (V2 §5.8).
 *
 * Two gates today: ``categorize`` (after page categorization) and
 * ``tiling`` (after tile-grid computation, before the first VLM call).
 * Both render an overlay on the page raster:
 *
 *  • categorize: layout rects (plan_view, legend, schedule, title_block, notes)
 *  • tiling: tile grid with row/col labels
 *
 * v1 of HITL is approve-only — no inline correction UI yet. The panel
 * shows the proposed regions / tile plan, and the user clicks Approve to
 * continue or Cancel to abort the run.
 */

import { useMemo } from "react";
import {
  approveGate,
  cancelDetection,
  type CategorizeApprovalPayload,
  type TilingApprovalPayload,
} from "../api/client";

type Gate =
  | { gate: "categorize"; payload: CategorizeApprovalPayload }
  | { gate: "tiling"; payload: TilingApprovalPayload };

interface Props {
  drawingId: string;
  gate: Gate;
}

export function ApprovalPanel({ drawingId, gate }: Props) {
  return (
    <aside className="approval-panel" role="dialog" aria-label="Approval required">
      <header className="approval-panel-head">
        <span className="eyebrow">Awaiting your approval</span>
        <h2 className="approval-panel-title">
          {gate.gate === "categorize"
            ? "Confirm page categorization"
            : "Confirm tile grid"}
        </h2>
        <p className="approval-panel-sub">
          {gate.gate === "categorize"
            ? "Review what the system thinks is the plan view, legend, and headings before we parse the legend and tile the drawing."
            : "Review the tile grid + DPI before we send each tile to the model. ~10 s per tile."}
        </p>
      </header>

      {gate.gate === "categorize" ? (
        <CategorizeBody payload={gate.payload} />
      ) : (
        <TilingBody payload={gate.payload} />
      )}

      <footer className="approval-panel-foot">
        <button
          type="button"
          className="button button-ghost"
          onClick={() => {
            void cancelDetection(drawingId);
          }}
        >
          Cancel run
        </button>
        <button
          type="button"
          className="button button-primary"
          onClick={() => {
            void approveGate(drawingId, gate.gate);
          }}
        >
          Approve {gate.gate === "categorize" ? "categorization" : "tile plan"}
        </button>
      </footer>
    </aside>
  );
}

function CategorizeBody({ payload }: { payload: CategorizeApprovalPayload }) {
  const { layout, raster_probe_data_url } = payload;
  const sourceSize = useSourceSize(payload);
  if (!raster_probe_data_url || sourceSize === null) {
    return (
      <div className="approval-panel-empty">
        No categorizer output available — degraded run; approving will use whole-page fallback.
      </div>
    );
  }
  return (
    <div className="approval-overlay-wrap">
      <svg
        className="approval-overlay"
        viewBox={`0 0 ${sourceSize[0]} ${sourceSize[1]}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <image
          href={raster_probe_data_url}
          width={sourceSize[0]}
          height={sourceSize[1]}
        />
        {layout?.plan_view && (
          <RectOverlay rect={layout.plan_view} stroke="#3B82F6" label="plan_view" />
        )}
        {layout?.legend && (
          <RectOverlay rect={layout.legend} stroke="#F59E0B" label="legend" />
        )}
        {layout?.schedule && (
          <RectOverlay rect={layout.schedule} stroke="#10B981" label="schedule" />
        )}
        {layout?.title_block && (
          <RectOverlay
            rect={layout.title_block}
            stroke="#A855F7"
            label="title_block"
          />
        )}
        {layout?.notes.map((rect, i) => (
          <RectOverlay
            key={i}
            rect={rect}
            stroke="#6B7280"
            label={`notes[${i}]`}
          />
        ))}
      </svg>
      <CategorizeLegend layout={layout} />
    </div>
  );
}

function CategorizeLegend({
  layout,
}: {
  layout: CategorizeApprovalPayload["layout"];
}) {
  const items: Array<[string, string, boolean]> = [
    ["plan_view", "#3B82F6", layout?.plan_view != null],
    ["legend", "#F59E0B", layout?.legend != null],
    ["schedule", "#10B981", layout?.schedule != null],
    ["title_block", "#A855F7", layout?.title_block != null],
    ["notes", "#6B7280", (layout?.notes.length ?? 0) > 0],
  ];
  return (
    <ul className="approval-overlay-legend">
      {items.map(([label, color, present]) => (
        <li key={label}>
          <span className="approval-overlay-swatch" style={{ background: color }} />
          <span>{label}</span>
          <span className={present ? "approval-overlay-present" : "approval-overlay-absent"}>
            {present ? "found" : "not identified"}
          </span>
        </li>
      ))}
    </ul>
  );
}

function TilingBody({ payload }: { payload: TilingApprovalPayload }) {
  return (
    <div className="approval-tiling">
      <dl className="approval-tiling-stats">
        <div>
          <dt>Tile size</dt>
          <dd className="mono">{payload.tile_px}px @ {payload.dpi}DPI</dd>
        </div>
        <div>
          <dt>Overlap</dt>
          <dd className="mono">{Math.round(payload.overlap_pct * 100)}%</dd>
        </div>
        <div>
          <dt>Tile count</dt>
          <dd className="mono">{payload.tile_count}</dd>
        </div>
        <div>
          <dt>Plan view rect</dt>
          <dd className="mono approval-tiling-rect">
            {payload.plan_view.map((v) => v.toFixed(0)).join(", ")}
          </dd>
        </div>
        <div>
          <dt>Estimated VLM time</dt>
          <dd className="mono">~{Math.round(payload.tile_count * 10)}s ({payload.tile_count} × 10s)</dd>
        </div>
      </dl>
    </div>
  );
}

function RectOverlay({
  rect,
  stroke,
  label,
}: {
  rect: [number, number, number, number];
  stroke: string;
  label: string;
}) {
  const [x0, y0, x1, y1] = rect;
  const w = x1 - x0;
  const h = y1 - y0;
  return (
    <g>
      <rect
        x={x0}
        y={y0}
        width={w}
        height={h}
        fill={`${stroke}22`}
        stroke={stroke}
        strokeWidth={2}
      />
      <text
        x={x0 + 4}
        y={y0 + 16}
        fontSize={12}
        fontFamily="ui-monospace, monospace"
        fill={stroke}
      >
        {label}
      </text>
    </g>
  );
}

/** Pick the source-coordinate size the SVG viewBox should use.
 *
 *  • coord_space === "pdf_points" → `page_size_pt`
 *  • coord_space === "pixels"     → `raster_probe_size`
 */
function useSourceSize(
  payload: CategorizeApprovalPayload,
): [number, number] | null {
  return useMemo(() => {
    if (payload.coord_space === "pdf_points") return payload.page_size_pt;
    return payload.raster_probe_size;
  }, [payload.coord_space, payload.page_size_pt, payload.raster_probe_size]);
}
