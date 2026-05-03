/**
 * ApprovalPanel — renders an open HITL approval gate (V2 §5.8).
 *
 * Two gates today:
 *   • ``categorize`` (after page categorization). The panel renders the
 *     layout rects (plan_view, legend, schedule, title_block, notes) as
 *     an INTERACTIVE editor over the page raster — drag handles to
 *     resize, drag rect interiors to move, click the X to delete, use
 *     "+ Add" toolbar buttons to draw new rects.
 *   • ``tiling`` (after the tile grid is computed, before the per-tile
 *     VLM loop). The panel shows the tile rects overlaid on the page
 *     raster and lets the user adjust ``tile_px`` and ``overlap_pct``
 *     with a live preview. DPI is read-only — it's derived from
 *     probe_ocr's smallest-text measurement and exposing it as a knob
 *     would let the user override a measurement they don't have the
 *     data to second-guess.
 *
 * Approve in either case sends the (possibly empty) corrections via
 * POST /api/detect/{id}/approve/{gate}; the pipeline applies them
 * before continuing.
 *
 * The tiling gate was removed in 4e231aa because the previous read-
 * only stats panel offered no actionable choice. This revision restores
 * it as an editable surface — the actionable choice is "is the tile
 * grid going to read the duct callouts cleanly", and ``tile_px`` /
 * ``overlap_pct`` are the levers that answer it.
 *
 * Why an editor and not yet-another-VLM-prompt: small-VLM bbox
 * extraction is unreliable. The right answer isn't more prompt
 * iteration — it's letting the user fix the output in 5 seconds. The
 * editor closes that loop without changing the inference path.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  approveGate,
  cancelDetection,
  type CategorizeApprovalPayload,
  type CategorizeCorrections,
  type TilingApprovalPayload,
  type TilingCorrections,
} from "../api/client";

type Gate =
  | { gate: "categorize"; payload: CategorizeApprovalPayload }
  | { gate: "tiling"; payload: TilingApprovalPayload };

interface Props {
  drawingId: string;
  gate: Gate;
}

/** A 4-tuple rect in source-coord space (PDF points or pixels). */
type Rect = [number, number, number, number];

/** Editable layout — same shape as the wire payload. The editor mutates
 *  this; on Approve we send it as the corrections body. */
interface EditLayout {
  plan_view: Rect | null;
  legend: Rect | null;
  schedule: Rect | null;
  title_block: Rect | null;
  notes: Rect[];
}

/** Region kinds that can hold at most one rect (deleted ⇒ null). */
type SingletonKind = "plan_view" | "legend" | "schedule" | "title_block";

/** Region kinds that hold a list of rects (legend has multi-block sources
 *  but the layout schema unions to a single rect; only "notes" stays a
 *  list at runtime). */
type ListKind = "notes";

type RegionKind = SingletonKind | ListKind;

/** Identifies one editable rect in the layout. ``index`` is the position
 *  in the notes array for ``kind === "notes"``; ignored otherwise. */
interface RectRef {
  kind: RegionKind;
  index: number;
}

const REGION_COLORS: Record<RegionKind, string> = {
  plan_view: "#3B82F6",
  legend: "#F59E0B",
  schedule: "#10B981",
  title_block: "#A855F7",
  notes: "#6B7280",
};

const REGION_LABELS: Record<RegionKind, string> = {
  plan_view: "plan_view",
  legend: "legend",
  schedule: "schedule",
  title_block: "title_block",
  notes: "notes",
};

/** Eight handles per rect — four corners + four edge midpoints. The label
 *  encodes which corner / edge the handle controls. */
type HandleId = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
const HANDLES: HandleId[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

/** During a drag, this captures what the user is doing. ``move`` shifts
 *  the rect; ``resize`` moves one or two of the rect's edges. */
type DragKind = "move" | "resize";
interface DragState {
  ref: RectRef;
  kind: DragKind;
  /** Only set for resize drags — which handle is being driven. */
  handle: HandleId | null;
  /** Source-coord cursor position when the drag started. */
  startX: number;
  startY: number;
  /** Snapshot of the rect at drag start; deltas are applied to this so
   *  rounding doesn't accumulate during a long drag. */
  startRect: Rect;
}

/** During add-region mode, the user clicks-and-drags on the SVG to draw
 *  a new rect. ``drawing`` carries the in-progress rect. */
interface DrawState {
  kind: RegionKind;
  startX: number;
  startY: number;
  current: Rect;
}

export function ApprovalPanel({ drawingId, gate }: Props) {
  if (gate.gate === "categorize") {
    return (
      <aside className="approval-panel" role="dialog" aria-label="Approval required">
        <header className="approval-panel-head">
          <span className="eyebrow">Awaiting your approval</span>
          <h2 className="approval-panel-title">Confirm page categorization</h2>
          <p className="approval-panel-sub">
            Drag handles to resize, drag a rect to move, or click X to delete. Use the + buttons to add a missing region.
          </p>
          {gate.payload.rotation_applied !== 0 && (
            <div className="approval-rotation-banner">
              <RotateBadge />
              <span>
                Auto-rotated <strong>{gate.payload.rotation_applied}° CW</strong> at ingest —
                source content was landscape inside a portrait page. Cancel if the rotated
                preview below looks wrong.
              </span>
            </div>
          )}
        </header>

        <CategorizeEditor drawingId={drawingId} payload={gate.payload} />
      </aside>
    );
  }

  return (
    <aside className="approval-panel" role="dialog" aria-label="Approval required">
      <header className="approval-panel-head">
        <span className="eyebrow">Awaiting your approval</span>
        <h2 className="approval-panel-title">Confirm tile grid</h2>
        <p className="approval-panel-sub">
          The plan view will be cropped into the tiles below and each tile sent to the VLM. Adjust tile size or overlap to change the grid; smaller tiles read finer detail at the cost of more inference calls.
        </p>
      </header>

      <TilingEditor drawingId={drawingId} payload={gate.payload} />
    </aside>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// CATEGORIZE — interactive editor
// ─────────────────────────────────────────────────────────────────────────────

function emptyLayout(): EditLayout {
  return {
    plan_view: null,
    legend: null,
    schedule: null,
    title_block: null,
    notes: [],
  };
}

function payloadToEditLayout(
  payload: CategorizeApprovalPayload,
): EditLayout {
  const layout = payload.layout;
  if (!layout) return emptyLayout();
  return {
    plan_view: layout.plan_view,
    legend: layout.legend,
    schedule: layout.schedule,
    title_block: layout.title_block,
    notes: layout.notes.map((r) => [...r] as Rect),
  };
}

function CategorizeEditor({
  drawingId,
  payload,
}: {
  drawingId: string;
  payload: CategorizeApprovalPayload;
}) {
  const sourceSize = useSourceSize(payload);
  const original = useMemo(() => payloadToEditLayout(payload), [payload]);
  const [editLayout, setEditLayout] = useState<EditLayout>(original);
  const [drawMode, setDrawMode] = useState<RegionKind | null>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const [draw, setDraw] = useState<DrawState | null>(null);
  // Zoom + pan around the editor's SVG. ``zoom`` scales the visible
  // viewBox down (so content appears bigger); ``panX/Y`` translate it.
  // Both default to identity. Wheel zooms anchor on the cursor position.
  const [view, setView] = useState({ zoom: 1.0, panX: 0, panY: 0 });
  const svgRef = useRef<SVGSVGElement | null>(null);

  // Reset when a new gate payload arrives (e.g. rerun) — useMemo above
  // recomputes ``original`` and we mirror it into local state.
  useEffect(() => {
    setEditLayout(original);
    setDrawMode(null);
    setDrag(null);
    setDraw(null);
    setView({ zoom: 1.0, panX: 0, panY: 0 });
  }, [original]);

  /** Translate a pointer event to viewBox (source-coord) coordinates.
   *  ``getScreenCTM().inverse()`` maps screen → user space; we then read
   *  the resulting SVGPoint's x/y. Returns null only if the SVG isn't
   *  laid out yet (defensive — should not happen during pointer events). */
  const toViewBox = useCallback(
    (event: ReactPointerEvent<SVGElement>): [number, number] | null => {
      const svg = svgRef.current;
      if (!svg) return null;
      const ctm = svg.getScreenCTM();
      if (!ctm) return null;
      const pt = svg.createSVGPoint();
      pt.x = event.clientX;
      pt.y = event.clientY;
      const local = pt.matrixTransform(ctm.inverse());
      return [local.x, local.y];
    },
    [],
  );

  // ── Region read/write helpers (closures over editLayout setter). ──

  const getRect = useCallback(
    (ref: RectRef): Rect | null => {
      if (ref.kind === "notes") return editLayout.notes[ref.index] ?? null;
      return editLayout[ref.kind];
    },
    [editLayout],
  );

  const setRect = useCallback((ref: RectRef, rect: Rect) => {
    setEditLayout((prev) => {
      if (ref.kind === "notes") {
        const next = prev.notes.slice();
        next[ref.index] = rect;
        return { ...prev, notes: next };
      }
      return { ...prev, [ref.kind]: rect };
    });
  }, []);

  const deleteRect = useCallback((ref: RectRef) => {
    setEditLayout((prev) => {
      if (ref.kind === "notes") {
        const next = prev.notes.slice();
        next.splice(ref.index, 1);
        return { ...prev, notes: next };
      }
      return { ...prev, [ref.kind]: null };
    });
  }, []);

  const addRect = useCallback((kind: RegionKind, rect: Rect) => {
    setEditLayout((prev) => {
      if (kind === "notes") return { ...prev, notes: [...prev.notes, rect] };
      return { ...prev, [kind]: rect };
    });
  }, []);

  // ── Drag start: pointerdown on a rect interior or a handle. ──

  const onRectPointerDown = useCallback(
    (
      event: ReactPointerEvent<SVGElement>,
      ref: RectRef,
      kind: DragKind,
      handle: HandleId | null = null,
    ) => {
      if (drawMode !== null) return;
      const local = toViewBox(event);
      if (!local) return;
      const rect = getRect(ref);
      if (!rect) return;
      event.stopPropagation();
      (event.currentTarget as Element).setPointerCapture(event.pointerId);
      setDrag({
        ref,
        kind,
        handle,
        startX: local[0],
        startY: local[1],
        startRect: rect,
      });
    },
    [drawMode, getRect, toViewBox],
  );

  // ── Pointer move: route to drag handlers or draw handler. ──

  const onSvgPointerMove = useCallback(
    (event: ReactPointerEvent<SVGSVGElement>) => {
      const local = toViewBox(event);
      if (!local) return;
      const [x, y] = local;

      if (drag) {
        const dx = x - drag.startX;
        const dy = y - drag.startY;
        const [sx0, sy0, sx1, sy1] = drag.startRect;

        let next: Rect;
        if (drag.kind === "move") {
          next = [sx0 + dx, sy0 + dy, sx1 + dx, sy1 + dy];
        } else {
          // Resize: move one or two edges based on which handle is held.
          let nx0 = sx0;
          let ny0 = sy0;
          let nx1 = sx1;
          let ny1 = sy1;
          const h = drag.handle;
          if (h === "nw" || h === "w" || h === "sw") nx0 = sx0 + dx;
          if (h === "ne" || h === "e" || h === "se") nx1 = sx1 + dx;
          if (h === "nw" || h === "n" || h === "ne") ny0 = sy0 + dy;
          if (h === "sw" || h === "s" || h === "se") ny1 = sy1 + dy;
          // Maintain a positive size — flipping is allowed but we keep
          // x0 ≤ x1 / y0 ≤ y1 in the stored shape so downstream geometry
          // stays well-defined.
          if (nx1 < nx0) [nx0, nx1] = [nx1, nx0];
          if (ny1 < ny0) [ny0, ny1] = [ny1, ny0];
          next = [nx0, ny0, nx1, ny1];
        }
        setRect(drag.ref, next);
        return;
      }

      if (draw) {
        const x0 = Math.min(draw.startX, x);
        const y0 = Math.min(draw.startY, y);
        const x1 = Math.max(draw.startX, x);
        const y1 = Math.max(draw.startY, y);
        setDraw({ ...draw, current: [x0, y0, x1, y1] });
      }
    },
    [drag, draw, setRect, toViewBox],
  );

  const onSvgPointerUp = useCallback(() => {
    if (drag) setDrag(null);
    if (draw) {
      const [x0, y0, x1, y1] = draw.current;
      // Drop tiny accidental clicks (< 4 source units on either axis) —
      // a real rect needs to be visibly draggable.
      if (x1 - x0 >= 4 && y1 - y0 >= 4) {
        addRect(draw.kind, draw.current);
      }
      setDraw(null);
      setDrawMode(null);
    }
  }, [addRect, drag, draw]);

  // ── SVG-level pointerdown — only meaningful in draw mode. ──

  const onSvgPointerDown = useCallback(
    (event: ReactPointerEvent<SVGSVGElement>) => {
      if (drawMode === null) return;
      // Don't start a draw if the user clicked on a rect or handle —
      // those have their own handlers and stopPropagation up the tree.
      const local = toViewBox(event);
      if (!local) return;
      event.currentTarget.setPointerCapture(event.pointerId);
      setDraw({
        kind: drawMode,
        startX: local[0],
        startY: local[1],
        current: [local[0], local[1], local[0], local[1]],
      });
    },
    [drawMode, toViewBox],
  );

  // ── Toolbar handlers. ──

  const startDrawing = useCallback((kind: RegionKind) => {
    setDrawMode(kind);
  }, []);
  const reset = useCallback(() => {
    setEditLayout(original);
    setDrawMode(null);
  }, [original]);

  // Pending state for the approve / cancel buttons — once clicked we
  // optimistically dim the panel so the user sees an immediate response,
  // and if the POST fails we surface the error inline rather than
  // silently swallowing it (the previous void-promise pattern).
  const [submitting, setSubmitting] = useState<"approve" | "cancel" | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const onApprove = useCallback(async () => {
    const corrections: CategorizeCorrections = {
      layout: {
        plan_view: editLayout.plan_view,
        legend: editLayout.legend,
        schedule: editLayout.schedule,
        title_block: editLayout.title_block,
        notes: editLayout.notes,
      },
    };
    setSubmitting("approve");
    setSubmitError(null);
    try {
      await approveGate(drawingId, "categorize", corrections);
      // The reducer clears awaitingGate on the next stage_start event
      // (legend_parse fires within ms of approve). The panel unmounts
      // shortly after. We keep `submitting` set until then so the
      // buttons stay disabled and the user can't double-submit.
    } catch (err) {
      setSubmitting(null);
      setSubmitError(err instanceof Error ? err.message : "approve failed");
    }
  }, [drawingId, editLayout]);

  const onCancel = useCallback(async () => {
    setSubmitting("cancel");
    setSubmitError(null);
    try {
      await cancelDetection(drawingId);
      // Backend cancels the session, SSE stream errors, App switches
      // to the error view and unmounts ProcessingView entirely. Keep
      // the buttons disabled in the meantime.
    } catch (err) {
      setSubmitting(null);
      setSubmitError(err instanceof Error ? err.message : "cancel failed");
    }
  }, [drawingId]);

  if (!payload.raster_probe_data_url || sourceSize === null) {
    return (
      <>
        <div className="approval-panel-empty">
          No categorizer output available — degraded run; approving will use whole-page fallback.
        </div>
        {submitError && (
          <div className="approval-submit-error" role="alert">
            {submitError}
          </div>
        )}
        <footer className="approval-panel-foot">
          <button
            type="button"
            className="button button-ghost"
            onClick={onCancel}
            disabled={submitting !== null}
          >
            {submitting === "cancel" ? "Cancelling…" : "Cancel run"}
          </button>
          <button
            type="button"
            className="button button-primary"
            onClick={onApprove}
            disabled={submitting !== null}
          >
            {submitting === "approve" ? "Approving…" : "Approve categorization"}
          </button>
        </footer>
      </>
    );
  }

  // viewBox tracks zoom + pan. At zoom=1 it's the full source rect;
  // zooming halves the visible width/height per step and re-centers on
  // the current pan point so the wheel-anchored math (below) stays
  // intuitive.
  const [sourceW, sourceH] = sourceSize;
  const vbW = sourceW / view.zoom;
  const vbH = sourceH / view.zoom;
  const vbX = view.panX;
  const vbY = view.panY;
  const viewBoxStr = `${vbX} ${vbY} ${vbW} ${vbH}`;

  const zoomAt = useCallback(
    (factor: number, anchorX?: number, anchorY?: number) => {
      setView((prev) => {
        const newZoom = Math.max(0.5, Math.min(8.0, prev.zoom * factor));
        if (newZoom === prev.zoom) return prev;
        // Anchor the zoom on a viewBox-space point (cursor on wheel,
        // viewport centre on button click). Math: keep the anchor at
        // the same screen position by shifting pan so its fractional
        // distance into the visible viewBox is preserved.
        const f = newZoom / prev.zoom;
        const ax = anchorX ?? prev.panX + sourceW / prev.zoom / 2;
        const ay = anchorY ?? prev.panY + sourceH / prev.zoom / 2;
        return {
          zoom: newZoom,
          panX: ax - (ax - prev.panX) / f,
          panY: ay - (ay - prev.panY) / f,
        };
      });
    },
    [sourceW, sourceH],
  );
  const resetView = useCallback(() => {
    setView({ zoom: 1.0, panX: 0, panY: 0 });
  }, []);

  // Wheel zoom — must use a native non-passive listener so we can
  // preventDefault. React's onWheel is passive by default.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const handler = (event: WheelEvent) => {
      event.preventDefault();
      const factor = event.deltaY > 0 ? 0.85 : 1.18;
      const ctm = svg.getScreenCTM();
      if (!ctm) return;
      const pt = svg.createSVGPoint();
      pt.x = event.clientX;
      pt.y = event.clientY;
      const local = pt.matrixTransform(ctm.inverse());
      zoomAt(factor, local.x, local.y);
    };
    svg.addEventListener("wheel", handler, { passive: false });
    return () => svg.removeEventListener("wheel", handler);
  }, [zoomAt]);

  return (
    <>
      <Toolbar
        layout={editLayout}
        drawMode={drawMode}
        onStartDrawing={startDrawing}
        onReset={reset}
        zoom={view.zoom}
        onZoomIn={() => zoomAt(1.25)}
        onZoomOut={() => zoomAt(0.8)}
        onResetView={resetView}
      />
      <div className="approval-overlay-wrap">
        <svg
          ref={svgRef}
          className={`approval-overlay${drawMode !== null ? " approval-overlay--drawing" : ""}`}
          viewBox={viewBoxStr}
          preserveAspectRatio="xMidYMid meet"
          onPointerDown={onSvgPointerDown}
          onPointerMove={onSvgPointerMove}
          onPointerUp={onSvgPointerUp}
        >
          <image
            href={payload.raster_probe_data_url}
            width={sourceSize[0]}
            height={sourceSize[1]}
          />
          <RegionLayer
            kind="plan_view"
            rect={editLayout.plan_view}
            onPointerDown={onRectPointerDown}
            onDelete={deleteRect}
          />
          <RegionLayer
            kind="legend"
            rect={editLayout.legend}
            onPointerDown={onRectPointerDown}
            onDelete={deleteRect}
          />
          <RegionLayer
            kind="schedule"
            rect={editLayout.schedule}
            onPointerDown={onRectPointerDown}
            onDelete={deleteRect}
          />
          <RegionLayer
            kind="title_block"
            rect={editLayout.title_block}
            onPointerDown={onRectPointerDown}
            onDelete={deleteRect}
          />
          {editLayout.notes.map((rect, i) => (
            <RegionLayer
              key={`notes-${i}`}
              kind="notes"
              rect={rect}
              index={i}
              onPointerDown={onRectPointerDown}
              onDelete={deleteRect}
            />
          ))}
          {draw && (
            <rect
              className="bbox-draw-preview"
              x={draw.current[0]}
              y={draw.current[1]}
              width={draw.current[2] - draw.current[0]}
              height={draw.current[3] - draw.current[1]}
              fill={`${REGION_COLORS[draw.kind]}22`}
              stroke={REGION_COLORS[draw.kind]}
              strokeWidth={2}
              strokeDasharray="4 4"
              pointerEvents="none"
            />
          )}
        </svg>
        <CategorizeLegend layout={editLayout} />
      </div>
      {drawMode !== null && (
        <div className="draw-mode-banner" role="status">
          Drawing <strong>{REGION_LABELS[drawMode]}</strong> — click and drag on the page
          <button
            type="button"
            className="button button-ghost button-small"
            onClick={() => setDrawMode(null)}
          >
            Cancel
          </button>
        </div>
      )}
      {submitError && (
        <div className="approval-submit-error" role="alert">
          {submitError}
        </div>
      )}
      <footer className="approval-panel-foot">
        <button
          type="button"
          className="button button-ghost"
          onClick={onCancel}
          disabled={submitting !== null}
        >
          {submitting === "cancel" ? "Cancelling…" : "Cancel run"}
        </button>
        <button
          type="button"
          className="button button-primary"
          onClick={onApprove}
          disabled={submitting !== null}
        >
          {submitting === "approve" ? "Approving…" : "Approve categorization"}
        </button>
      </footer>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Per-region SVG group with handles + delete X.
// ─────────────────────────────────────────────────────────────────────────────

interface RegionLayerProps {
  kind: RegionKind;
  rect: Rect | null;
  index?: number;
  onPointerDown: (
    event: ReactPointerEvent<SVGElement>,
    ref: RectRef,
    kind: DragKind,
    handle?: HandleId | null,
  ) => void;
  onDelete: (ref: RectRef) => void;
}

function RegionLayer({
  kind,
  rect,
  index = 0,
  onPointerDown,
  onDelete,
}: RegionLayerProps) {
  if (!rect) return null;
  const [x0, y0, x1, y1] = rect;
  const w = x1 - x0;
  const h = y1 - y0;
  const stroke = REGION_COLORS[kind];
  const ref: RectRef = { kind, index };
  // Handle radius in source coords — scaled by the raster long edge so
  // it stays visually constant across page sizes. Tuned at ~0.8% of the
  // long edge — small enough not to occlude the rect, large enough to
  // grab.
  const handleR = Math.max(6, Math.max(w, h) * 0.008);
  // Label placed inside the rect, top-left corner.
  const labelText = kind === "notes" ? `notes[${index}]` : kind;
  return (
    <g data-region={kind} data-index={index}>
      <rect
        x={x0}
        y={y0}
        width={w}
        height={h}
        fill={`${stroke}22`}
        stroke={stroke}
        strokeWidth={2}
        onPointerDown={(e) => onPointerDown(e, ref, "move")}
        style={{ cursor: "move" }}
      />
      <text
        x={x0 + 4}
        y={y0 + 16}
        fontSize={12}
        fontFamily="ui-monospace, monospace"
        fill={stroke}
        pointerEvents="none"
      >
        {labelText}
      </text>
      {/* Eight resize handles. */}
      {HANDLES.map((h) => {
        const [hx, hy] = handlePosition(h, rect);
        return (
          <circle
            key={h}
            className={`bbox-handle bbox-handle--${
              h.length === 2 ? "corner" : "edge"
            }`}
            cx={hx}
            cy={hy}
            r={handleR}
            fill={stroke}
            stroke="#FFFFFF"
            strokeWidth={1.5}
            data-handle={h}
            onPointerDown={(e) => onPointerDown(e, ref, "resize", h)}
            style={{ cursor: cursorForHandle(h) }}
          />
        );
      })}
      {/* Delete X — top-right of the rect, just outside the stroke. */}
      <g
        className="bbox-delete-x"
        transform={`translate(${x1}, ${y0})`}
        onPointerDown={(e) => {
          e.stopPropagation();
          onDelete(ref);
        }}
        style={{ cursor: "pointer" }}
        data-action="delete"
      >
        <circle r={handleR * 1.2} fill="#DC2626" stroke="#FFFFFF" strokeWidth={1.5} />
        <line
          x1={-handleR * 0.5}
          y1={-handleR * 0.5}
          x2={handleR * 0.5}
          y2={handleR * 0.5}
          stroke="#FFFFFF"
          strokeWidth={1.5}
          strokeLinecap="round"
        />
        <line
          x1={-handleR * 0.5}
          y1={handleR * 0.5}
          x2={handleR * 0.5}
          y2={-handleR * 0.5}
          stroke="#FFFFFF"
          strokeWidth={1.5}
          strokeLinecap="round"
        />
      </g>
    </g>
  );
}

function handlePosition(h: HandleId, rect: Rect): [number, number] {
  const [x0, y0, x1, y1] = rect;
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  switch (h) {
    case "nw":
      return [x0, y0];
    case "n":
      return [cx, y0];
    case "ne":
      return [x1, y0];
    case "e":
      return [x1, cy];
    case "se":
      return [x1, y1];
    case "s":
      return [cx, y1];
    case "sw":
      return [x0, y1];
    case "w":
      return [x0, cy];
  }
}

function cursorForHandle(h: HandleId): string {
  switch (h) {
    case "nw":
    case "se":
      return "nwse-resize";
    case "ne":
    case "sw":
      return "nesw-resize";
    case "n":
    case "s":
      return "ns-resize";
    case "e":
    case "w":
      return "ew-resize";
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Toolbar: + Add buttons + Reset.
// ─────────────────────────────────────────────────────────────────────────────

function Toolbar({
  layout,
  drawMode,
  onStartDrawing,
  onReset,
  zoom,
  onZoomIn,
  onZoomOut,
  onResetView,
}: {
  layout: EditLayout;
  drawMode: RegionKind | null;
  onStartDrawing: (kind: RegionKind) => void;
  onReset: () => void;
  zoom: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetView: () => void;
}) {
  // Singletons only show "Add" when the slot is currently null. Lists
  // (notes, legend block) always show — multiple entries are allowed.
  // Per spec: legend is treated as list-typed in the Add toolbar even
  // though the layout schema unions to one rect — clicking "+ Add legend
  // block" replaces the existing legend rect (singleton in the runtime
  // edit state).
  const buttons: Array<{ kind: RegionKind; label: string; show: boolean }> = [
    {
      kind: "title_block",
      label: "+ Add title_block",
      show: layout.title_block === null,
    },
    {
      kind: "legend",
      label: "+ Add legend block",
      show: true,
    },
    {
      kind: "schedule",
      label: "+ Add schedule",
      show: layout.schedule === null,
    },
    {
      kind: "notes",
      label: "+ Add notes block",
      show: true,
    },
  ];
  return (
    <div className="approval-toolbar">
      {buttons
        .filter((b) => b.show)
        .map((b) => (
          <button
            key={b.kind}
            type="button"
            className={`approval-toolbar-button${
              drawMode === b.kind ? " approval-toolbar-button--active" : ""
            }`}
            onClick={() => onStartDrawing(b.kind)}
            data-add-kind={b.kind}
          >
            {b.label}
          </button>
        ))}
      <span className="approval-toolbar-spacer" />
      <div className="approval-toolbar-zoom">
        <button
          type="button"
          className="approval-toolbar-button"
          onClick={onZoomOut}
          aria-label="Zoom out"
          title="Zoom out (or scroll wheel)"
        >
          −
        </button>
        <span className="approval-toolbar-zoom-readout mono">
          {Math.round(zoom * 100)}%
        </span>
        <button
          type="button"
          className="approval-toolbar-button"
          onClick={onZoomIn}
          aria-label="Zoom in"
          title="Zoom in (or scroll wheel)"
        >
          +
        </button>
        <button
          type="button"
          className="approval-toolbar-button"
          onClick={onResetView}
          title="Fit to page"
        >
          Fit
        </button>
      </div>
      <button
        type="button"
        className="approval-toolbar-button"
        onClick={onReset}
      >
        Reset edits
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Sidebar legend (status of each region kind).
// ─────────────────────────────────────────────────────────────────────────────

function CategorizeLegend({ layout }: { layout: EditLayout }) {
  const items: Array<[string, string, boolean]> = [
    ["plan_view", REGION_COLORS.plan_view, layout.plan_view !== null],
    ["legend", REGION_COLORS.legend, layout.legend !== null],
    ["schedule", REGION_COLORS.schedule, layout.schedule !== null],
    ["title_block", REGION_COLORS.title_block, layout.title_block !== null],
    ["notes", REGION_COLORS.notes, layout.notes.length > 0],
  ];
  return (
    <ul className="approval-overlay-legend">
      {items.map(([label, color, present]) => (
        <li key={label}>
          <span className="approval-overlay-swatch" style={{ background: color }} />
          <span>{label}</span>
          <span className={present ? "approval-overlay-present" : "approval-overlay-absent"}>
            {present ? "edited" : "absent"}
          </span>
        </li>
      ))}
    </ul>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// TILING — adjustable tile grid editor
// ─────────────────────────────────────────────────────────────────────────────

/** Clamp ranges mirror the backend's ``_apply_tiling_corrections``. The
 *  slider/number-input inputs already enforce these so the typical path
 *  sends in-range values; the backend clamps defensively if anything
 *  slips through. */
const TILE_PX_MIN = 600;
const TILE_PX_MAX = 2000;
const TILE_PX_STEP = 50;
const OVERLAP_MIN = 0.05;
const OVERLAP_MAX = 0.40;
const OVERLAP_STEP = 0.01;
/** Rough wall-clock estimate per tile (VLM call) — used only for the
 *  "estimated time" readout in the side panel. Real calls vary 5–25 s
 *  on cloud Ollama; 10 s is the median we've observed on benchmark 01. */
const SECONDS_PER_TILE = 10;

interface TileRect {
  rect: [number, number, number, number];
  row: number;
  col: number;
  totalRows: number;
  totalCols: number;
}

/** TS port of the backend's ``_compute_tiles`` math. Same algorithm,
 *  same row-major traversal order — kept in sync so the live preview
 *  matches what the pipeline will actually iterate over. */
function computeTiles(
  planView: [number, number, number, number],
  coordSpace: "pdf_points" | "pixels",
  dpi: number,
  tilePx: number,
  overlapPct: number,
): TileRect[] {
  const [x0, y0, x1, y1] = planView;
  const width = x1 - x0;
  const height = y1 - y0;
  if (width <= 0 || height <= 0) return [];

  const tileSize =
    coordSpace === "pdf_points"
      ? (tilePx / Math.max(dpi, 1)) * 72
      : tilePx;
  const overlap = tileSize * overlapPct;
  const step = tileSize - overlap;
  if (step <= 0) {
    return [
      {
        rect: [x0, y0, x1, y1],
        row: 0,
        col: 0,
        totalRows: 1,
        totalCols: 1,
      },
    ];
  }

  const totalCols =
    width > tileSize
      ? Math.max(1, Math.ceil((width - overlap) / step))
      : 1;
  const totalRows =
    height > tileSize
      ? Math.max(1, Math.ceil((height - overlap) / step))
      : 1;

  const tiles: TileRect[] = [];
  for (let row = 0; row < totalRows; row += 1) {
    for (let col = 0; col < totalCols; col += 1) {
      const tx0 = x0 + col * step;
      const ty0 = y0 + row * step;
      const tx1 = Math.min(tx0 + tileSize, x1);
      const ty1 = Math.min(ty0 + tileSize, y1);
      if (tx1 - tx0 <= 0 || ty1 - ty0 <= 0) continue;
      tiles.push({
        rect: [tx0, ty0, tx1, ty1],
        row,
        col,
        totalRows,
        totalCols,
      });
    }
  }
  return tiles;
}

function clampTilePx(value: number): number {
  if (Number.isNaN(value)) return TILE_PX_MIN;
  return Math.max(TILE_PX_MIN, Math.min(TILE_PX_MAX, Math.round(value)));
}

function clampOverlap(value: number): number {
  if (Number.isNaN(value)) return OVERLAP_MIN;
  return Math.max(OVERLAP_MIN, Math.min(OVERLAP_MAX, value));
}

function TilingEditor({
  drawingId,
  payload,
}: {
  drawingId: string;
  payload: TilingApprovalPayload;
}) {
  const [tilePx, setTilePx] = useState<number>(() =>
    clampTilePx(payload.tile_px),
  );
  const [overlapPct, setOverlapPct] = useState<number>(() =>
    clampOverlap(payload.overlap_pct),
  );

  // Reset locally-edited values when a fresh gate payload arrives — keeps
  // the editor in sync if the backend re-fires the gate (e.g. on retry).
  useEffect(() => {
    setTilePx(clampTilePx(payload.tile_px));
    setOverlapPct(clampOverlap(payload.overlap_pct));
  }, [payload.tile_px, payload.overlap_pct]);

  // Live tile recompute. We don't trust the server's payload.tiles list
  // beyond the initial render — every adjustment flows through this so
  // the SVG overlay always reflects the values the user is about to
  // submit. Memoised so the SVG render stays cheap on slider drag.
  const tiles = useMemo(
    () =>
      computeTiles(
        payload.plan_view,
        payload.coord_space,
        payload.dpi,
        tilePx,
        overlapPct,
      ),
    [payload.plan_view, payload.coord_space, payload.dpi, tilePx, overlapPct],
  );
  const tileCount = tiles.length;
  const estimatedSec = tileCount * SECONDS_PER_TILE;

  const [submitting, setSubmitting] = useState<"approve" | "cancel" | null>(
    null,
  );
  const [submitError, setSubmitError] = useState<string | null>(null);

  const onApprove = useCallback(async () => {
    // Only ship corrections when the user actually changed something —
    // an empty body keeps the stage defaults, which is the cheaper
    // backend path. The clamps guarantee in-range values.
    const corrections: TilingCorrections = {};
    if (tilePx !== payload.tile_px) corrections.tile_px = tilePx;
    if (overlapPct !== payload.overlap_pct) corrections.overlap_pct = overlapPct;
    setSubmitting("approve");
    setSubmitError(null);
    try {
      await approveGate(drawingId, "tiling", corrections);
      // The reducer clears awaitingGate on the next tile_start event
      // (the first tile fires within ms of approve, before the long
      // VLM call returns), so the panel unmounts shortly after. Keep
      // submitting set until then so the user can't double-submit.
    } catch (err) {
      setSubmitting(null);
      setSubmitError(err instanceof Error ? err.message : "approve failed");
    }
  }, [drawingId, overlapPct, payload.overlap_pct, payload.tile_px, tilePx]);

  const onCancel = useCallback(async () => {
    setSubmitting("cancel");
    setSubmitError(null);
    try {
      await cancelDetection(drawingId);
    } catch (err) {
      setSubmitting(null);
      setSubmitError(err instanceof Error ? err.message : "cancel failed");
    }
  }, [drawingId]);

  const [sourceW, sourceH] = payload.source_size;
  // viewBox spans the whole source so the plan_view + tile rects sit in
  // the same coord system as the data URL. No zoom controls in v1 of
  // the tiling editor — the plan_view is usually the dominant content
  // already and the side panel carries the read-only stats.
  const viewBoxStr = `0 0 ${sourceW} ${sourceH}`;
  const [px0, py0, px1, py1] = payload.plan_view;
  // Label font scales with the source long-edge so it stays legible
  // across page sizes — same heuristic as the categorize editor.
  const labelFontSize = Math.max(10, Math.max(sourceW, sourceH) * 0.012);

  return (
    <>
      <div className="approval-overlay-wrap">
        <svg
          className="approval-overlay tiling-editor-svg"
          viewBox={viewBoxStr}
          preserveAspectRatio="xMidYMid meet"
        >
          <image href={payload.raster_probe_data_url} width={sourceW} height={sourceH} />
          {/* plan_view outline — read-only context, drawn so the user
              sees which area the tile grid is covering. */}
          <rect
            x={px0}
            y={py0}
            width={px1 - px0}
            height={py1 - py0}
            fill="none"
            stroke="#3B82F6"
            strokeWidth={2}
            strokeDasharray="6 4"
            pointerEvents="none"
          />
          {tiles.map((tile) => {
            const [tx0, ty0, tx1, ty1] = tile.rect;
            return (
              <g
                key={`tile-${tile.row}-${tile.col}`}
                data-tile-row={tile.row}
                data-tile-col={tile.col}
              >
                <rect
                  className="tiling-tile"
                  x={tx0}
                  y={ty0}
                  width={tx1 - tx0}
                  height={ty1 - ty0}
                  fill="#0EA5E933"
                  stroke="#0EA5E9"
                  strokeWidth={1.5}
                  pointerEvents="none"
                />
                <text
                  className="tiling-tile-label"
                  x={tx0 + 6}
                  y={ty0 + labelFontSize + 2}
                  fontSize={labelFontSize}
                  fontFamily="ui-monospace, monospace"
                  fill="#0369A1"
                  pointerEvents="none"
                >
                  ({tile.row},{tile.col})
                </text>
              </g>
            );
          })}
        </svg>
        <TilingControls
          tilePx={tilePx}
          overlapPct={overlapPct}
          dpi={payload.dpi}
          tileCount={tileCount}
          estimatedSec={estimatedSec}
          planView={payload.plan_view}
          coordSpace={payload.coord_space}
          onTilePxChange={(v) => setTilePx(clampTilePx(v))}
          onOverlapChange={(v) => setOverlapPct(clampOverlap(v))}
          disabled={submitting !== null}
        />
      </div>
      {submitError && (
        <div className="approval-submit-error" role="alert">
          {submitError}
        </div>
      )}
      <footer className="approval-panel-foot">
        <button
          type="button"
          className="button button-ghost"
          onClick={onCancel}
          disabled={submitting !== null}
        >
          {submitting === "cancel" ? "Cancelling…" : "Cancel run"}
        </button>
        <button
          type="button"
          className="button button-primary"
          onClick={onApprove}
          disabled={submitting !== null}
        >
          {submitting === "approve" ? "Approving…" : "Approve tile grid"}
        </button>
      </footer>
    </>
  );
}

function TilingControls({
  tilePx,
  overlapPct,
  dpi,
  tileCount,
  estimatedSec,
  planView,
  coordSpace,
  onTilePxChange,
  onOverlapChange,
  disabled,
}: {
  tilePx: number;
  overlapPct: number;
  dpi: number;
  tileCount: number;
  estimatedSec: number;
  planView: [number, number, number, number];
  coordSpace: "pdf_points" | "pixels";
  onTilePxChange: (value: number) => void;
  onOverlapChange: (value: number) => void;
  disabled: boolean;
}) {
  const planRectStr = planView.map((v) => v.toFixed(1)).join(", ");
  const estimatedMin = Math.floor(estimatedSec / 60);
  const estimatedSecRemainder = estimatedSec % 60;
  const estimatedLabel =
    estimatedMin > 0
      ? `~${estimatedMin}m ${estimatedSecRemainder}s`
      : `~${estimatedSec}s`;
  return (
    <div className="tiling-controls">
      <div className="tiling-control-row">
        <label htmlFor="tiling-tile-px">Tile size (px)</label>
        <input
          id="tiling-tile-px"
          type="number"
          className="tiling-control-input"
          min={TILE_PX_MIN}
          max={TILE_PX_MAX}
          step={TILE_PX_STEP}
          value={tilePx}
          disabled={disabled}
          onChange={(e) => onTilePxChange(Number(e.target.value))}
          data-control="tile-px"
        />
        <span className="tiling-control-help">
          {TILE_PX_MIN}–{TILE_PX_MAX}; smaller = finer detail, more calls
        </span>
      </div>
      <div className="tiling-control-row">
        <label htmlFor="tiling-overlap">Overlap</label>
        <div className="tiling-overlap-pair">
          <input
            id="tiling-overlap"
            type="range"
            className="tiling-control-slider"
            min={OVERLAP_MIN}
            max={OVERLAP_MAX}
            step={OVERLAP_STEP}
            value={overlapPct}
            disabled={disabled}
            onChange={(e) => onOverlapChange(Number(e.target.value))}
            data-control="overlap-pct"
          />
          <span className="tiling-control-readout mono">
            {(overlapPct * 100).toFixed(0)}%
          </span>
        </div>
        <span className="tiling-control-help">
          {Math.round(OVERLAP_MIN * 100)}–{Math.round(OVERLAP_MAX * 100)}%; higher
          = safer at edges, more redundant compute
        </span>
      </div>
      <dl className="tiling-stats">
        <div>
          <dt>DPI</dt>
          <dd className="mono" data-readonly="dpi">
            {dpi}
          </dd>
        </div>
        <div>
          <dt>Tile count</dt>
          <dd className="mono" data-readonly="tile-count">
            {tileCount}
          </dd>
        </div>
        <div>
          <dt>Estimated time</dt>
          <dd className="mono">{estimatedLabel}</dd>
        </div>
        <div>
          <dt>plan_view ({coordSpace})</dt>
          <dd className="mono tiling-stats-rect">[{planRectStr}]</dd>
        </div>
      </dl>
    </div>
  );
}

function RotateBadge() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path
        d="M11 7 A 4 4 0 1 1 7 3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        fill="none"
      />
      <path
        d="M7 1.5 L7 3.5 L9 3.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
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
