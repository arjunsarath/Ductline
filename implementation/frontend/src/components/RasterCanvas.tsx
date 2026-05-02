/**
 * RasterCanvas — Figma-style infinite canvas over a raster <img> + SVG overlay.
 *
 * Behaviour-preserving extraction of the v1 Viewer body. Used for `coord_space:
 * "pixels"` (PNG / JPG / raster_pdf inputs). The PDF.js path lives in
 * PdfCanvas.tsx and the parent Viewer routes between them.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { DrawingResult } from "../types/api";
import { CanvasControls } from "./CanvasControls";
import { Popover } from "./Popover";
import { SegmentMark } from "./SegmentMark";
import {
  clamp,
  computePopoverAnchor,
  cursorInStage,
  scaleAroundPoint,
} from "./canvasShared";
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

export function RasterCanvas({
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

  const dragRef = useRef<{
    startX: number;
    startY: number;
    startTx: number;
    startTy: number;
  } | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  // ── Wheel: zoom anchored to cursor ───────────────────────────────────────

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    function handleWheel(event: WheelEvent) {
      event.preventDefault();
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

  const selectedSegment = selectedId
    ? (result.segments.find((s) => s.id === selectedId) ?? null)
    : null;

  const popoverAnchor = selectedSegment
    ? computePopoverAnchor(
        selectedSegment,
        result.width_px,
        result.height_px,
        imgRef.current?.clientWidth ?? 0,
        imgRef.current?.clientHeight ?? 0,
        stageRef.current,
        viewport,
      )
    : null;

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
