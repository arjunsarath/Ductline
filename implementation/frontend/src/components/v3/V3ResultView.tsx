/**
 * V3 result view. Rebuilds the V1/V2 viewer affordances on top of the
 * V3 deterministic pipeline output:
 *   • Wheel zoom anchored at cursor + drag-pan + rotate (CanvasControls)
 *   • Reset view, Grayscale toggle, Sidebar toggle in the top bar
 *   • Click any segment → V3Popover with dim, pressure-class disclosure,
 *     and provenance (chosen ppu, attribution rule)
 *   • Keyboard: Esc clear, ←/→ cycle, g grayscale, s sidebar
 *
 * The page render and the (transparent) overlay PNG are layered in the
 * canvas so the grayscale filter only desaturates the page underneath.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { clamp } from "../canvasShared";
import { type Viewport, INITIAL_VIEWPORT, SCALE_MAX, SCALE_MIN } from "../viewport";
import type { V3DetectResponse, V3Segment } from "../../types/v3";
import { V3CanvasViewer } from "./V3CanvasViewer";

interface Props {
  filename: string;
  file: File;
  response: V3DetectResponse;
  onReset: () => void;
}

type ConfidenceFilter = "all" | "high" | "medium" | "low";

export function V3ResultView({ filename, file, response, onReset }: Props) {
  const { result, page_png_base64, overlay_png_base64 } = response;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [grayscale, setGrayscale] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [viewport, setViewport] = useState<Viewport>(INITIAL_VIEWPORT);
  const [confFilter, setConfFilter] = useState<ConfidenceFilter>("all");

  const zoomBy = useCallback((factor: number) => {
    setViewport((v) => ({
      ...v,
      scale: clamp(v.scale * factor, SCALE_MIN, SCALE_MAX),
    }));
  }, []);

  const resetViewport = useCallback(
    () => setViewport(INITIAL_VIEWPORT),
    [],
  );

  const rotate = useCallback(() => {
    setViewport((v) => ({ ...v, rotationDeg: (v.rotationDeg + 90) % 360 }));
    setSelectedId(null);
  }, []);

  const segmentIds = useMemo(
    () => result.segments.map((s) => s.id),
    [result.segments],
  );

  // Keyboard shortcuts — same vocabulary as V1 ResultView
  useEffect(() => {
    function handler(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.tagName === "INPUT" || target?.tagName === "TEXTAREA") return;
      if (event.key === "Escape") return setSelectedId(null);
      if (event.key === "g" || event.key === "G")
        return setGrayscale((g) => !g);
      if (event.key === "s" || event.key === "S")
        return setSidebarCollapsed((c) => !c);
      if (event.key === "ArrowRight" || (event.key === "Tab" && !event.shiftKey)) {
        setSelectedId((cur) => stepSelection(segmentIds, cur, +1));
        event.preventDefault();
      }
      if (event.key === "ArrowLeft" || (event.key === "Tab" && event.shiftKey)) {
        setSelectedId((cur) => stepSelection(segmentIds, cur, -1));
        event.preventDefault();
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [segmentIds]);

  const filteredSegs = useMemo(
    () =>
      result.segments.filter(
        (s) => confFilter === "all" || s.dim_confidence === confFilter,
      ),
    [result.segments, confFilter],
  );

  const counts = useMemo(() => {
    const byConf = { high: 0, medium: 0, low: 0 };
    const byPC = { LOW: 0, MEDIUM: 0, HIGH: 0 };
    for (const s of result.segments) {
      byConf[s.dim_confidence] += 1;
      byPC[s.pressure.value] += 1;
    }
    return { byConf, byPC };
  }, [result.segments]);

  const pageSrc = page_png_base64
    ? `data:image/png;base64,${page_png_base64}`
    : null;
  const overlaySrc = overlay_png_base64
    ? `data:image/png;base64,${overlay_png_base64}`
    : null;

  return (
    <main className="result-view">
      <header className="result-topbar">
        <div className="result-topbar-left">
          <div className="brand">Ductline · V3</div>
          <button type="button" className="button-ghost" onClick={onReset}>
            ← New drawing
          </button>
          <div className="result-topbar-meta">
            <span className="mono">{filename}</span>
            <span>·</span>
            <strong>{result.segments.length} segments</strong>
            <span>·</span>
            <span>
              ppu{" "}
              {result.ppu !== null ? result.ppu.toFixed(2) : "—"} px/
              {result.page_unit}
            </span>
          </div>
        </div>
        <div className="result-topbar-spacer" />
        <div className="result-topbar-right">
          <button
            type="button"
            className="button-ghost"
            onClick={resetViewport}
            title="Reset zoom + pan + rotation"
          >
            Reset view
          </button>
          <button
            type="button"
            className="button-ghost"
            aria-pressed={grayscale}
            onClick={() => setGrayscale((g) => !g)}
            title="Toggle grayscale on the underlying drawing"
          >
            Grayscale
            <kbd className="kbd-hint">G</kbd>
          </button>
          <button
            type="button"
            className="button-ghost"
            aria-pressed={!sidebarCollapsed}
            onClick={() => setSidebarCollapsed((c) => !c)}
            title="Hide / show the right panel"
          >
            Sidebar
            <kbd className="kbd-hint">S</kbd>
          </button>
        </div>
      </header>

      <div className="result-body">
        {pageSrc ? (
          <V3CanvasViewer
            pageSrc={pageSrc}
            overlaySrc={overlaySrc}
            file={file}
            rotationApplied={result.rotation_applied}
            drawingW={result.width_px}
            drawingH={result.height_px}
            segments={result.segments}
            selectedId={selectedId}
            grayscale={grayscale}
            viewport={viewport}
            onViewportChange={setViewport}
            onSelect={setSelectedId}
            onRotate={rotate}
            onZoomBy={zoomBy}
          />
        ) : (
          <div className="v3-overlay-empty">
            No page render available — pipeline aborted before producing artifacts.
          </div>
        )}
        {!sidebarCollapsed && (
          <V3Sidebar
            segments={result.segments}
            filtered={filteredSegs}
            counts={counts}
            confFilter={confFilter}
            onFilterChange={setConfFilter}
            selectedId={selectedId}
            onSelect={setSelectedId}
            errors={result.errors}
            ppu={result.ppu}
            pageUnit={result.page_unit}
            metrics={{
              tokensTotal: result.n_tokens_total,
              dimRect: result.n_dim_rect_tokens,
              flow: result.n_flow_tokens,
              attrRect: result.n_attributed_rect,
              attrFlow: result.n_attributed_flow,
              calBand: result.calibration.n_in_band,
              calPairs: result.calibration.n_pairs,
            }}
          />
        )}
      </div>
    </main>
  );
}

interface SidebarProps {
  segments: V3Segment[];
  filtered: V3Segment[];
  counts: { byConf: Record<string, number>; byPC: Record<string, number> };
  confFilter: ConfidenceFilter;
  onFilterChange: (next: ConfidenceFilter) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  errors: string[];
  ppu: number | null;
  pageUnit: string;
  metrics: {
    tokensTotal: number; dimRect: number; flow: number;
    attrRect: number; attrFlow: number; calBand: number; calPairs: number;
  };
}

function V3Sidebar({
  segments,
  filtered,
  counts,
  confFilter,
  onFilterChange,
  selectedId,
  onSelect,
  errors,
  ppu,
  pageUnit,
  metrics,
}: SidebarProps) {
  return (
    <aside className="v3-result-panel">
      <div className="v3-overlay-summary">
        <strong>{segments.length}</strong> segments ·{" "}
        <span className="ok">{counts.byConf.high}</span> high ·{" "}
        <span className="warn">{counts.byConf.medium}</span> medium ·{" "}
        <span className="bad">{counts.byConf.low}</span> low
        <br />
        Pressure: {counts.byPC.LOW} L / {counts.byPC.MEDIUM} M / {counts.byPC.HIGH} H
        {ppu !== null && (
          <>
            <br />ppu {ppu.toFixed(2)} px/{pageUnit}
          </>
        )}
      </div>
      <div className="v3-result-controls">
        <select
          value={confFilter}
          onChange={(e) => onFilterChange(e.target.value as ConfidenceFilter)}
        >
          <option value="all">All ({segments.length})</option>
          <option value="high">High ({counts.byConf.high})</option>
          <option value="medium">Medium ({counts.byConf.medium})</option>
          <option value="low">Low ({counts.byConf.low})</option>
        </select>
      </div>
      <div className="v3-segments-list">
        {filtered.map((s) => (
          <SegmentCard
            key={s.id}
            segment={s}
            isSelected={s.id === selectedId}
            onClick={() =>
              onSelect(s.id === selectedId ? null : s.id)
            }
          />
        ))}
      </div>
      {errors.length > 0 && (
        <div className="v3-errors">
          <strong>Pipeline notes:</strong>
          <ul>
            {errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      <details className="v3-debug">
        <summary>Pipeline metrics</summary>
        <table>
          <tbody>
            <tr><td>OCR tokens</td><td>{metrics.tokensTotal}</td></tr>
            <tr><td>dim_rect tokens</td><td>{metrics.dimRect}</td></tr>
            <tr><td>flow tokens</td><td>{metrics.flow}</td></tr>
            <tr><td>dim attributed (in-mask)</td><td>{metrics.attrRect}</td></tr>
            <tr><td>flow attributed (in-mask)</td><td>{metrics.attrFlow}</td></tr>
            <tr><td>calibration in-band / total</td>
              <td>{metrics.calBand} / {metrics.calPairs * 2}</td></tr>
          </tbody>
        </table>
      </details>
    </aside>
  );
}

function SegmentCard({
  segment,
  isSelected,
  onClick,
}: {
  segment: V3Segment;
  isSelected: boolean;
  onClick: () => void;
}) {
  const dim = segment.shape === "round"
    ? `${segment.visible_unit}″ Ø`
    : `${segment.visible_unit}×${segment.hidden_unit} ${segment.page_unit}`;
  const pcSourceShort =
    segment.pressure.source === "extracted" ? "extracted" : "size-only";
  return (
    <button
      type="button"
      className={`segment-card${isSelected ? " is-selected" : ""}`}
      onClick={onClick}
    >
      <div className="segment-card-row">
        <span className="segment-id mono">{segment.id}</span>
        <span className={`pill conf-${segment.dim_confidence}`}>
          {segment.dim_confidence}
        </span>
      </div>
      <div className="segment-card-row segment-card-main">
        <span className="segment-dim">{dim}</span>
        <span className={`pill pc-${segment.pressure.value.toLowerCase()}`}>
          {segment.pressure.value}
        </span>
      </div>
      <div className="segment-card-row segment-card-meta">
        <span>OCR: <span className="mono">{segment.token_text}</span></span>
        <span>Δ {segment.delta_pct >= 0 ? "+" : ""}{segment.delta_pct.toFixed(1)}%</span>
      </div>
      <div className="segment-card-row segment-card-meta">
        <span>pressure: {pcSourceShort}</span>
        {segment.pressure.flow_value !== null && (
          <span>{segment.pressure.flow_value} {segment.pressure.flow_unit}</span>
        )}
      </div>
    </button>
  );
}

function stepSelection(
  ids: string[],
  current: string | null,
  delta: number,
): string | null {
  if (ids.length === 0) return null;
  if (current === null) return delta > 0 ? ids[0] : ids[ids.length - 1];
  const index = ids.indexOf(current);
  return ids[(index + delta + ids.length) % ids.length];
}
