/**
 * Viewer — Figma-style infinite canvas (wheel zoom, drag pan, rotate) over the
 * raster + SVG overlay. Viewport state lives in `ResultView` so the result
 * top bar can drive zoom/reset/rotate; this component owns the interactions.
 *
 * The image is wrapped in a `viewer-content` div that receives a single
 * `transform: translate(tx) scale(s) rotate(r)`. The popover sits OUTSIDE the
 * transform so its size doesn't scale with zoom — its anchor is computed by
 * applying the same transform to the segment centroid.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { DrawingResult, Segment } from "../types/api";
import { Popover } from "./Popover";
import { SCALE_MAX, SCALE_MIN, type Viewport } from "./viewport";

interface Props {
  result: DrawingResult;
  selectedId: string | null;
  grayscale: boolean;
  viewport: Viewport;
  onViewportChange: (next: Viewport) => void;
  onSelect: (id: string | null) => void;
  onRotate: () => void;
  onZoomBy: (factor: number) => void;
}

export function Viewer({
  result,
  selectedId,
  grayscale,
  viewport,
  onViewportChange,
  onSelect,
  onRotate,
  onZoomBy,
}: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  // Drag-pan state held in a ref to avoid re-rendering on every mousemove.
  const dragRef = useRef<{ startX: number; startY: number; startTx: number; startTy: number } | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  // ── Wheel: zoom anchored to cursor ───────────────────────────────────────

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    function handleWheel(event: WheelEvent) {
      event.preventDefault();
      // Smooth multiplicative zoom; 1 click ≈ 0.1 deltaY at default OS speed.
      const factor = Math.exp(-event.deltaY * 0.0015);
      const newScale = clamp(viewport.scale * factor, SCALE_MIN, SCALE_MAX);
      if (newScale === viewport.scale) return;

      const cursor = cursorInStage(event, stage!);
      const next = scaleAroundPoint(viewport, newScale, cursor);
      onViewportChange(next);
    }

    stage.addEventListener("wheel", handleWheel, { passive: false });
    return () => stage.removeEventListener("wheel", handleWheel);
  }, [viewport, onViewportChange]);

  // ── Drag-pan ─────────────────────────────────────────────────────────────

  const onStageMouseDown = useCallback(
    (event: React.MouseEvent) => {
      // Only start a pan if the user pressed on background (not a segment).
      // Segments stopPropagation inside their own handlers.
      if (event.button !== 0) return;
      dragRef.current = {
        startX: event.clientX,
        startY: event.clientY,
        startTx: viewport.tx,
        startTy: viewport.ty,
      };
      setIsDragging(true);
    },
    [viewport.tx, viewport.ty],
  );

  useEffect(() => {
    if (!isDragging) return;

    function handleMove(event: MouseEvent) {
      const drag = dragRef.current;
      if (!drag) return;
      onViewportChange({
        ...viewport,
        tx: drag.startTx + (event.clientX - drag.startX),
        ty: drag.startTy + (event.clientY - drag.startY),
      });
    }

    function handleUp() {
      dragRef.current = null;
      setIsDragging(false);
    }

    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
  }, [isDragging, viewport, onViewportChange]);

  // ── Popover anchor — re-computed from transform + segment centroid ───────

  const selectedSegment = selectedId
    ? (result.segments.find((s) => s.id === selectedId) ?? null)
    : null;
  const popoverAnchor = selectedSegment
    ? computePopoverAnchor(
        selectedSegment,
        result.width_px,
        result.height_px,
        imgRef.current,
        stageRef.current,
        viewport,
      )
    : null;

  // ── Render ───────────────────────────────────────────────────────────────

  const transform = `translate(${viewport.tx}px, ${viewport.ty}px) scale(${viewport.scale}) rotate(${viewport.rotationDeg}deg)`;

  return (
    <section
      ref={stageRef}
      className={`viewer${isDragging ? " is-dragging" : ""}`}
      onMouseDown={onStageMouseDown}
      onClick={(event) => {
        if (event.target === event.currentTarget) onSelect(null);
      }}
    >
      <div ref={contentRef} className="viewer-content" style={{ transform }}>
        <img
          ref={imgRef}
          src={result.display_image_data_url}
          alt="HVAC drawing"
          className={`viewer-raster${grayscale ? " is-grayscale" : ""}`}
          draggable={false}
        />
        <svg
          className="viewer-overlay"
          viewBox={`0 0 ${result.width_px} ${result.height_px}`}
          preserveAspectRatio="xMidYMid meet"
          aria-label="duct detection overlay"
        >
          {result.segments.map((segment) => (
            <SegmentMark
              key={segment.id}
              segment={segment}
              isSelected={segment.id === selectedId}
              onSelect={() => onSelect(segment.id)}
            />
          ))}
        </svg>
      </div>

      {selectedSegment && popoverAnchor && (
        <Popover
          segment={selectedSegment}
          anchor={popoverAnchor}
          onClose={() => onSelect(null)}
        />
      )}

      <CanvasControls
        viewport={viewport}
        onRotate={onRotate}
        onZoomBy={onZoomBy}
      />
    </section>
  );
}

function CanvasControls({
  viewport,
  onRotate,
  onZoomBy,
}: {
  viewport: Viewport;
  onRotate: () => void;
  onZoomBy: (factor: number) => void;
}) {
  return (
    <div
      className="canvas-controls"
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        className="canvas-control-button"
        onClick={() => onZoomBy(0.8)}
        title="Zoom out"
        aria-label="Zoom out"
      >
        −
      </button>
      <span className="canvas-control-readout mono">
        {Math.round(viewport.scale * 100)}%
      </span>
      <button
        type="button"
        className="canvas-control-button"
        onClick={() => onZoomBy(1.25)}
        title="Zoom in"
        aria-label="Zoom in"
      >
        +
      </button>
      <span className="canvas-control-divider" aria-hidden="true" />
      <button
        type="button"
        className="canvas-control-button"
        onClick={onRotate}
        title="Rotate 90°"
        aria-label="Rotate 90 degrees"
      >
        <RotateIcon />
      </button>
      {viewport.rotationDeg !== 0 && (
        <span className="canvas-control-readout mono">
          {viewport.rotationDeg}°
        </span>
      )}
    </div>
  );
}

function RotateIcon() {
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

// ── Segment rendering ────────────────────────────────────────────────────────


function SegmentMark({
  segment,
  isSelected,
  onSelect,
}: {
  segment: Segment;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const stroke = pressureClassColor(segment.pressure_class.value);
  const dashed = segment.pressure_class.confidence !== "high";
  const widthBase = isSelected ? 5 : 2.5;
  const strokeWidth = widthBase * 4;

  const commonProps = {
    onMouseDown: (e: React.MouseEvent) => e.stopPropagation(),
    onClick: (e: React.MouseEvent) => {
      e.stopPropagation();
      onSelect();
    },
    stroke,
    strokeWidth,
    strokeDasharray: dashed ? `${strokeWidth * 3} ${strokeWidth * 2}` : undefined,
    fill: isSelected ? `${stroke}33` : "transparent",
    style: { cursor: "pointer" } as const,
  };

  if (segment.geometry.type === "polyline") {
    const points = segment.geometry.points.map(([x, y]) => `${x},${y}`).join(" ");
    return <polyline points={points} {...commonProps} />;
  }

  const [[x1, y1], [x2, y2]] = segment.geometry.points;
  return (
    <rect
      x={Math.min(x1, x2)}
      y={Math.min(y1, y2)}
      width={Math.abs(x2 - x1)}
      height={Math.abs(y2 - y1)}
      {...commonProps}
    />
  );
}

function pressureClassColor(value: Segment["pressure_class"]["value"]): string {
  switch (value) {
    case "LOW":
      return "#059669";
    case "MEDIUM":
      return "#ea580c";
    case "HIGH":
      return "#dc2626";
  }
}

// ── Transform math ───────────────────────────────────────────────────────────


function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function cursorInStage(event: WheelEvent, stage: HTMLElement): { x: number; y: number } {
  const rect = stage.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

/**
 * Adjust translate so the world point under `cursor` stays put when scale
 * changes. Standard cursor-anchored zoom math.
 */
function scaleAroundPoint(
  viewport: Viewport,
  newScale: number,
  cursor: { x: number; y: number },
): Viewport {
  const ratio = newScale / viewport.scale;
  return {
    ...viewport,
    scale: newScale,
    tx: cursor.x - (cursor.x - viewport.tx) * ratio,
    ty: cursor.y - (cursor.y - viewport.ty) * ratio,
  };
}

/**
 * Map a segment's centroid (drawing-pixel coords) to screen-space anchor
 * coords by replaying the same transform CSS applies to the content wrapper.
 */
function computePopoverAnchor(
  segment: Segment,
  drawingW: number,
  drawingH: number,
  img: HTMLImageElement | null,
  stage: HTMLDivElement | null,
  viewport: Viewport,
): { x: number; y: number } | null {
  if (!img || !stage) return null;

  // Centroid in drawing-pixel coords.
  const sumX = segment.geometry.points.reduce((acc, [x]) => acc + x, 0);
  const sumY = segment.geometry.points.reduce((acc, [, y]) => acc + y, 0);
  const cx = sumX / segment.geometry.points.length;
  const cy = sumY / segment.geometry.points.length;

  // The image is rendered with object-fit:contain inside .viewer-content at
  // its natural-fit size — that fitted size scales by `viewport.scale`. We
  // need the centroid in untransformed content-local coords, then apply the
  // transform manually so we end up in stage-screen coords.
  const fitWidth = img.clientWidth;
  const fitHeight = img.clientHeight;
  if (!fitWidth || !fitHeight) return null;

  // Centroid in image-fit coords.
  let px = (cx / drawingW) * fitWidth;
  let py = (cy / drawingH) * fitHeight;

  // Rotate around the image center.
  const cxImg = fitWidth / 2;
  const cyImg = fitHeight / 2;
  const rad = (viewport.rotationDeg * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const dx = px - cxImg;
  const dy = py - cyImg;
  px = cxImg + dx * cos - dy * sin;
  py = cyImg + dx * sin + dy * cos;

  // The content wrapper's transform-origin is its center; the wrapper itself
  // is centered in the stage by CSS grid (place-items: center). After we map
  // through scale + translate, we need the content wrapper's top-left in
  // stage coords too. Easier: derive from img.getBoundingClientRect() which
  // already reflects the active transform — but that's circular when zoom
  // changes mid-render. Instead read the stage rect and compute analytically:
  const stageRect = stage.getBoundingClientRect();
  const contentLeft = stageRect.width / 2 - fitWidth / 2 + viewport.tx;
  const contentTop = stageRect.height / 2 - fitHeight / 2 + viewport.ty;

  // Apply scale around image center.
  const sx = (px - cxImg) * viewport.scale + cxImg;
  const sy = (py - cyImg) * viewport.scale + cyImg;

  return { x: contentLeft + sx, y: contentTop + sy };
}
