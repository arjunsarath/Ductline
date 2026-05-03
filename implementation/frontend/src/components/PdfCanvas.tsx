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
  /** CW rotation baked into segment coords. PDF.js must apply the same so
   *  the canvas content matches the overlay's coordinate space. */
  rotation: 0 | 90 | 180 | 270;
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
  rotation,
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
  // Logical scale (PDF-points → CSS-pixels) the canvas was last rasterized at.
  // Compared against the live viewport.scale to decide when to re-render at a
  // higher DPI for lossless zoom-in.
  const renderedScaleRef = useRef<number>(0);
  const fitScaleRef = useRef<number>(1);

  const dragRef = useRef<{
    startX: number;
    startY: number;
    startTx: number;
    startTy: number;
  } | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  // Re-rasterize the page at ``logicalScale`` (PDF-pt → CSS-px). Keeps the
  // canvas's CSS box at fit size (so layout / overlay alignment never moves)
  // but pushes the internal pixel buffer up so zoom-in stays sharp.
  const renderAtScale = useCallback(
    async (logicalScale: number) => {
      const page = pageRef.current;
      const canvas = canvasRef.current;
      if (!page || !canvas) return;

      const dpr = window.devicePixelRatio || 1;
      // Cap the canvas to avoid OOM at extreme zooms. 16K px on the long edge
      // covers ~10–12× zoom on a typical drawing without throwing.
      const MAX_INTERNAL_PX = 16000;
      const longEdgePt = Math.max(pageSizePt[0], pageSizePt[1]);
      const maxLogicalScale = MAX_INTERNAL_PX / (longEdgePt * dpr);
      const safeScale = Math.min(logicalScale, maxLogicalScale);

      const viewportPdf = page.getViewport({
        scale: safeScale * dpr,
        rotation,
      });
      canvas.width = Math.round(viewportPdf.width);
      canvas.height = Math.round(viewportPdf.height);
      const cssW = Math.round(pageSizePt[0] * fitScaleRef.current);
      const cssH = Math.round(pageSizePt[1] * fitScaleRef.current);
      canvas.style.width = `${cssW}px`;
      canvas.style.height = `${cssH}px`;

      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      renderTaskRef.current?.cancel();
      const task = page.render({
        canvasContext: ctx,
        canvas,
        viewport: viewportPdf,
      });
      renderTaskRef.current = task;
      try {
        await task.promise;
      } catch (err) {
        if ((err as { name?: string })?.name !== "RenderingCancelledException") {
          console.error("PdfCanvas render failed", err);
        }
        return;
      }
      renderedScaleRef.current = safeScale;
      setCanvasSize({ w: cssW, h: cssH });
    },
    [pageSizePt, rotation],
  );

  // ── Load PDF + initial render at fit-to-stage DPI ────────────────────────
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

      const stage = stageRef.current;
      const stageW = stage?.clientWidth ?? 1100;
      const stageH = stage?.clientHeight ?? 700;
      const fitScale = Math.min(
        stageW / pageSizePt[0],
        stageH / pageSizePt[1],
      );
      fitScaleRef.current = fitScale;

      // TEMP diag — verify rotation alignment with backend.
      console.log("[PdfCanvas]", {
        rotation,
        pageSizePt,
        intrinsicPdfRotate: page.rotate,
        firstSeg: result.segments[0]?.geometry.points?.[0],
      });

      await renderAtScale(fitScale);
      if (cancelled) return;
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
  }, [file, pageSizePt, rotation, renderAtScale]);

  // ── Re-rasterize on zoom-in for lossless rendering ───────────────────────
  // viewport.scale is the CSS post-multiplier on top of the canvas's CSS box.
  // Lossless when (canvas internal pixels) >= (on-screen pixels), i.e. when
  // renderedScale >= fitScale * viewport.scale. We re-render with 30% headroom
  // and a small debounce so a continuous zoom gesture only triggers one job.
  useEffect(() => {
    if (!pdfReady) return;
    const required = fitScaleRef.current * Math.max(1, viewport.scale);
    if (required <= renderedScaleRef.current * 1.05) return;
    const timer = setTimeout(() => {
      void renderAtScale(required * 1.3);
    }, 80);
    return () => clearTimeout(timer);
  }, [viewport.scale, pdfReady, renderAtScale]);

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
