/**
 * PdfCanvas — vector PDF base layer rendered by PDF.js, with the same
 * SVG overlay + popover behaviour as RasterCanvas.
 *
 * V2 §5.7: vector PDFs render natively; the SVG viewBox is in PDF point
 * space (the `coord_space === "pdf_points"` contract) so segment geometry
 * needs no client-side conversion.
 *
 * Render strategy: render once at a base DPI sized to fit the stage on
 * first paint; the existing CSS-transform zoom/pan/rotate from the v1
 * Viewer is reused on top. The PDF.js worker is loaded as a Vite asset
 * via `?url`, the canonical Vite pattern (no public/ vendoring needed).
 */

import {
  GlobalWorkerOptions,
  getDocument,
  type PDFDocumentProxy,
  type PDFPageProxy,
  type RenderTask,
} from "pdfjs-dist";
import pdfjsWorkerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
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

// One-shot worker setup at module scope; idempotent.
if (!GlobalWorkerOptions.workerSrc) {
  GlobalWorkerOptions.workerSrc = pdfjsWorkerSrc;
}

interface Props {
  file: File;
  pageSizePt: [number, number];
  result: DrawingResult;
  selectedId: string | null;
  grayscale: boolean;
  viewport: Viewport;
  onViewportChange: (next: Viewport) => void;
  onSelect: (id: string | null) => void;
  onRotate: () => void;
  onZoomBy: (factor: number) => void;
}

export function PdfCanvas({
  file,
  pageSizePt,
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
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const docRef = useRef<PDFDocumentProxy | null>(null);
  const pageRef = useRef<PDFPageProxy | null>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);

  const [canvasSize, setCanvasSize] = useState<{ w: number; h: number } | null>(
    null,
  );
  const [pdfReady, setPdfReady] = useState(false);

  const dragRef = useRef<{
    startX: number;
    startY: number;
    startTx: number;
    startTy: number;
  } | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  // ── Load PDF + render page 1 once at a stage-fit DPI ─────────────────────
  useEffect(() => {
    let cancelled = false;
    setPdfReady(false);

    (async () => {
      const buffer = await file.arrayBuffer();
      if (cancelled) return;

      const loadingTask = getDocument({ data: buffer });
      const doc = await loadingTask.promise;
      if (cancelled) {
        await doc.destroy();
        return;
      }
      docRef.current = doc;

      const page = await doc.getPage(1);
      if (cancelled) return;
      pageRef.current = page;

      // Pick a base scale that fits the stage on first paint. Page units are
      // PDF points (72 / inch); stage is in CSS pixels. We bias toward height
      // since drawings are typically wider than the sidebar-narrowed stage.
      const stage = stageRef.current;
      const stageW = stage?.clientWidth ?? 1100;
      const stageH = stage?.clientHeight ?? 700;
      const fitScale = Math.min(
        stageW / pageSizePt[0],
        stageH / pageSizePt[1],
      );
      // CSS-pixel viewport at fit; multiply by devicePixelRatio for sharp text.
      const dpr = window.devicePixelRatio || 1;
      const viewportPdf = page.getViewport({ scale: fitScale * dpr });

      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.width = Math.round(viewportPdf.width);
      canvas.height = Math.round(viewportPdf.height);
      const cssW = Math.round(viewportPdf.width / dpr);
      const cssH = Math.round(viewportPdf.height / dpr);
      canvas.style.width = `${cssW}px`;
      canvas.style.height = `${cssH}px`;

      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      // Cancel any in-flight render before starting a new one.
      renderTaskRef.current?.cancel();
      const renderTask = page.render({
        canvasContext: ctx,
        canvas,
        viewport: viewportPdf,
      });
      renderTaskRef.current = renderTask;

      try {
        await renderTask.promise;
      } catch (err) {
        // RenderingCancelledException is expected on rapid re-renders.
        if ((err as { name?: string })?.name !== "RenderingCancelledException") {
          console.error("PdfCanvas render failed", err);
        }
        return;
      }
      if (cancelled) return;
      setCanvasSize({ w: cssW, h: cssH });
      setPdfReady(true);
    })().catch((err: unknown) => {
      console.error("PdfCanvas load failed", err);
    });

    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      const doc = docRef.current;
      docRef.current = null;
      pageRef.current = null;
      doc?.destroy().catch(() => {});
    };
  }, [file, pageSizePt]);

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
      onViewportChange(scaleAroundPoint(viewport, newScale, cursor));
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

  // ── Anchor + transform ───────────────────────────────────────────────────
  const selectedSegment = selectedId
    ? (result.segments.find((s) => s.id === selectedId) ?? null)
    : null;

  const popoverAnchor =
    selectedSegment && canvasSize
      ? computePopoverAnchor(
          selectedSegment,
          pageSizePt[0],
          pageSizePt[1],
          canvasSize.w,
          canvasSize.h,
          stageRef.current,
          viewport,
        )
      : null;

  const transform = `translate(${viewport.tx}px, ${viewport.ty}px) scale(${viewport.scale}) rotate(${viewport.rotationDeg}deg)`;

  // SVG strokes are in PDF-point units (~600×800) vs raster pixels (~6000×
  // 8000). Scale the stroke base down so on-screen weight matches.
  const strokeBase = 0.5;

  return (
    <section
      ref={stageRef}
      className={`viewer${isDragging ? " is-dragging" : ""}`}
      onMouseDown={onStageMouseDown}
      onClick={(event) => {
        if (event.target === event.currentTarget) onSelect(null);
      }}
    >
      <div className="viewer-content" style={{ transform }}>
        <canvas
          ref={canvasRef}
          className={`viewer-raster${grayscale ? " is-grayscale" : ""}`}
          aria-label="HVAC drawing"
        />
        {pdfReady && (
          <svg
            className="viewer-overlay"
            viewBox={`0 0 ${pageSizePt[0]} ${pageSizePt[1]}`}
            preserveAspectRatio="xMidYMid meet"
            aria-label="duct detection overlay"
          >
            {result.segments.map((segment) => (
              <SegmentMark
                key={segment.id}
                segment={segment}
                isSelected={segment.id === selectedId}
                onSelect={() => onSelect(segment.id)}
                strokeBase={strokeBase}
              />
            ))}
          </svg>
        )}
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
