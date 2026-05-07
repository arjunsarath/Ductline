/**
 * V4 viewer stage. PDF page rendered via the existing V3PageCanvas, with
 * the V4 SVG overlay (segments + terminals) layered on top inside the same
 * .viewer-content transform — so zoom, pan, and rotation stay synchronised
 * across the two layers without any per-marker screen-space maths.
 *
 * Pan/zoom/rotate behaviour mirrors V3CanvasViewer: wheel zoom anchored at
 * cursor, drag to pan, rotate button. Picking is delegated to the SVG
 * overlay (see V4Overlay).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CanvasControls } from "../CanvasControls";
import {
  clamp,
  cursorInStage,
  scaleAroundPoint,
} from "../canvasShared";
import { SCALE_MAX, SCALE_MIN, type Viewport } from "../viewport";
import { V3PageCanvas } from "../v3/V3PageCanvas";
import { V4Overlay, type V4Selection } from "./V4Overlay";
import { V4DebugLegend, V4DebugOverlay } from "./V4DebugOverlay";
import { V4OcrOverlay } from "./V4OcrOverlay";
import { V4OcrInspector } from "./V4OcrInspector";
import type { V4Result } from "../../types/v4";

interface Props {
  file: File;
  result: V4Result;
  /** Drawing pixel size — V4Result has no width/height field of its own,
   *  so the parent computes it from PDF.js page dimensions. */
  drawingW: number;
  drawingH: number;
  /** Display-space fit dimensions for the page raster. */
  fitWidth: number;
  fitHeight: number;
  selection: V4Selection;
  viewport: Viewport;
  backgroundOpacity: number;
  shadeByPressure: boolean;
  onViewportChange: (next: Viewport) => void;
  onSelect: (next: V4Selection) => void;
  onRotate: () => void;
  onZoomBy: (factor: number) => void;
}

export function V4Viewer({
  file,
  result,
  drawingW,
  drawingH,
  fitWidth,
  fitHeight,
  selection,
  viewport,
  backgroundOpacity,
  shadeByPressure,
  onViewportChange,
  onSelect,
  onRotate,
  onZoomBy,
}: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [selectedOcrIdx, setSelectedOcrIdx] = useState<number | null>(null);

  // When a duct is selected, find its linked terminal in the matches list
  // by bbox identity so the overlay can render both with highlight style.
  const selectedMatch =
    selectedOcrIdx !== null && result.debug_ocr
      ? result.debug_ocr[selectedOcrIdx]
      : null;
  const linkedOcrIdx = (() => {
    if (!selectedMatch?.adjacent_terminal_bbox || !result.debug_ocr) return null;
    const [tx, ty, tw, th] = selectedMatch.adjacent_terminal_bbox;
    const idx = result.debug_ocr.findIndex(
      (m) =>
        m.bbox[0] === tx && m.bbox[1] === ty
        && m.bbox[2] === tw && m.bbox[3] === th,
    );
    return idx >= 0 ? idx : null;
  })();
  const dragRef = useRef<{
    startX: number;
    startY: number;
    startTx: number;
    startTy: number;
  } | null>(null);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    function handleWheel(event: WheelEvent) {
      event.preventDefault();
      const factor = Math.exp(-event.deltaY * 0.0015);
      const newScale = clamp(viewport.scale * factor, SCALE_MIN, SCALE_MAX);
      if (newScale === viewport.scale) return;
      const cursor = cursorInStage(event, stage!);
      onViewportChange(scaleAroundPoint(viewport, newScale, cursor));
    }
    stage.addEventListener("wheel", handleWheel, { passive: false });
    return () => stage.removeEventListener("wheel", handleWheel);
  }, [viewport, onViewportChange]);

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

  const transform = useMemo(
    () =>
      `translate(${viewport.tx}px, ${viewport.ty}px) scale(${viewport.scale}) rotate(${viewport.rotationDeg}deg)`,
    [viewport],
  );

  // Inverse-scale stroke widths so the visible centerline + hit polyline
  // stay roughly constant in screen pixels at any zoom.
  const inverseScale = 1 / Math.max(0.0001, viewport.scale);

  return (
    <section
      ref={stageRef}
      className={`viewer${isDragging ? " is-dragging" : ""}`}
      onMouseDown={onStageMouseDown}
      onClick={(event) => {
        if (event.target === event.currentTarget) onSelect(null);
      }}
    >
      <div className="viewer-content v4-viewer-content" style={{ transform }}>
        {fitWidth > 0 && fitHeight > 0 && (
          <div
            className="v4-overlay-wrap"
            style={{
              width: fitWidth, height: fitHeight, position: "relative",
            }}
          >
            <div
              className="v4-viewer__underlay"
              style={{ opacity: backgroundOpacity }}
            >
              {result.stage_image_data_url ? (
                <img
                  src={result.stage_image_data_url}
                  alt={`stage: ${result.stage_stopped_after ?? "intermediate"}`}
                  style={{ width: fitWidth, height: fitHeight, display: "block" }}
                />
              ) : (
                <V3PageCanvas
                  file={file}
                  rotation={result.page_dims.rotation}
                  viewportScale={viewport.scale}
                  fitWidth={fitWidth}
                  fitHeight={fitHeight}
                  grayscale={false}
                />
              )}
            </div>
            {result.debug && (
              <V4DebugOverlay
                drawingW={drawingW}
                drawingH={drawingH}
                polygons={result.debug.polygons}
                segments={result.segments}
                inverseScale={inverseScale}
              />
            )}
            {result.segments?.length > 0 && (
              <V4Overlay
                drawingW={drawingW}
                drawingH={drawingH}
                segments={result.segments}
                terminals={result.terminals}
                selection={selection}
                inverseScale={inverseScale}
                onSelect={onSelect}
              />
            )}
            {result.debug_ocr && result.debug_ocr.length > 0 && (
              <V4OcrOverlay
                drawingW={drawingW}
                drawingH={drawingH}
                matches={result.debug_ocr}
                selectedIdx={selectedOcrIdx}
                linkedIdx={linkedOcrIdx}
                shadeByPressure={shadeByPressure}
                onSelect={setSelectedOcrIdx}
              />
            )}
          </div>
        )}
      </div>

      <CanvasControls
        viewport={viewport}
        onRotate={onRotate}
        onZoomBy={onZoomBy}
      />
      {result.debug && <V4DebugLegend />}
      {selectedOcrIdx !== null && result.debug_ocr?.[selectedOcrIdx] && (
        <V4OcrInspector
          match={result.debug_ocr[selectedOcrIdx]}
          onClose={() => setSelectedOcrIdx(null)}
        />
      )}
    </section>
  );
}
