/**
 * Vector-PDF base layer for the V3 viewer.
 *
 * Renders the PDF page to a `<canvas>` using PDF.js, sized to match the
 * <img> layout the V3 raster path uses (so the SVG marker layer's
 * coordinate math is unchanged). When the user zooms in, we re-rasterize
 * the page at higher DPI so glyphs and lines stay vector-sharp instead
 * of upscaling the original render.
 *
 * Direct port of V1's PdfCanvas render strategy (canonical Vite asset
 * import for the worker; cap internal pixels to avoid OOM at extreme
 * zooms; debounce so a continuous zoom gesture only fires one job).
 */

import {
  GlobalWorkerOptions,
  getDocument,
  type PDFDocumentProxy,
  type PDFPageProxy,
  type RenderTask,
} from "pdfjs-dist";
import pdfjsWorkerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";

if (!GlobalWorkerOptions.workerSrc) {
  GlobalWorkerOptions.workerSrc = pdfjsWorkerSrc;
}

interface Props {
  file: File;
  /** Clockwise rotation baked into the V3 result's coord space — must
   *  match what the backend's pipeline rotated to. */
  rotation: 0 | 90 | 180 | 270;
  /** The viewport's CSS scale post-multiplier. Drives re-rasterization
   *  decisions: when (canvas internal pixels) < (CSS layout × scale × dpr),
   *  the canvas would be visually upscaled, so we re-render at the
   *  higher logical scale. */
  viewportScale: number;
  /** CSS layout width to display the canvas at — matches the V3 raster
   *  path's <img> layout (object-fit: contain inside the viewer). The
   *  parent picks this and we honour it so the marker overlay's
   *  coordinate math is identical for canvas and image paths. */
  fitWidth: number;
  fitHeight: number;
  grayscale: boolean;
  className?: string;
}

export interface V3PageCanvasHandle {
  /** Pixel width currently displayed (canvas.clientWidth). */
  clientWidth: number;
  clientHeight: number;
}

export const V3PageCanvas = forwardRef<V3PageCanvasHandle, Props>(
  function V3PageCanvas(
    { file, rotation, viewportScale, fitWidth, fitHeight, grayscale, className },
    ref,
  ) {
    const canvasRef = useRef<HTMLCanvasElement | null>(null);
    const docRef = useRef<PDFDocumentProxy | null>(null);
    const pageRef = useRef<PDFPageProxy | null>(null);
    const renderTaskRef = useRef<RenderTask | null>(null);
    const renderedScaleRef = useRef<number>(0);
    const [pdfReady, setPdfReady] = useState(false);

    useImperativeHandle(ref, () => ({
      get clientWidth() { return canvasRef.current?.clientWidth ?? 0; },
      get clientHeight() { return canvasRef.current?.clientHeight ?? 0; },
    }));

    // The page's intrinsic size in PDF points after the requested
    // rotation. Used to compute the logicalScale for any given fitWidth.
    const pagePtRef = useRef<[number, number] | null>(null);

    const renderAtScale = useCallback(
      async (logicalScale: number) => {
        const page = pageRef.current;
        const canvas = canvasRef.current;
        const pagePt = pagePtRef.current;
        if (!page || !canvas || !pagePt) return;

        const dpr = window.devicePixelRatio || 1;
        // Cap internal canvas pixels to ~16K on the long edge —
        // beyond that browsers OOM or the GPU rejects.
        const MAX_INTERNAL_PX = 16000;
        const longEdgePt = Math.max(pagePt[0], pagePt[1]);
        const maxLogicalScale = MAX_INTERNAL_PX / (longEdgePt * dpr);
        const safeScale = Math.min(logicalScale, maxLogicalScale);

        const viewport = page.getViewport({
          scale: safeScale * dpr,
          rotation,
        });
        canvas.width = Math.round(viewport.width);
        canvas.height = Math.round(viewport.height);
        canvas.style.width = `${fitWidth}px`;
        canvas.style.height = `${fitHeight}px`;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;

        renderTaskRef.current?.cancel();
        const task = page.render({
          canvasContext: ctx,
          canvas,
          viewport,
        });
        renderTaskRef.current = task;
        try {
          await task.promise;
        } catch (err) {
          if ((err as { name?: string })?.name !== "RenderingCancelledException") {
            // eslint-disable-next-line no-console
            console.error("V3PageCanvas render failed", err);
          }
          return;
        }
        renderedScaleRef.current = safeScale;
      },
      [rotation, fitWidth, fitHeight],
    );

    // Load PDF once + initial render at fit scale.
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
        // Page size in PDF points after the rotation we'll render at.
        const baseViewport = page.getViewport({ scale: 1, rotation });
        pagePtRef.current = [baseViewport.width, baseViewport.height];
        // Initial render: fit-to-display logical scale.
        const fitScale = fitWidth / baseViewport.width;
        await renderAtScale(fitScale);
        if (cancelled) return;
        setPdfReady(true);
      })().catch((err: unknown) => {
        // eslint-disable-next-line no-console
        console.error("V3PageCanvas load failed", err);
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
    }, [file, rotation, fitWidth, renderAtScale]);

    // Re-rasterize on zoom-in. Lossless when canvas internal px ≥
    // CSS-displayed-px, i.e., when renderedScale ≥ fitScale × viewportScale.
    // 30 % headroom plus an 80 ms debounce avoids re-rendering on every
    // tick of a continuous wheel-zoom.
    useEffect(() => {
      if (!pdfReady) return;
      const pagePt = pagePtRef.current;
      if (!pagePt) return;
      const fitScale = fitWidth / pagePt[0];
      const required = fitScale * Math.max(1, viewportScale);
      if (required <= renderedScaleRef.current * 1.05) return;
      const timer = setTimeout(() => {
        void renderAtScale(required * 1.3);
      }, 80);
      return () => clearTimeout(timer);
    }, [viewportScale, pdfReady, fitWidth, renderAtScale]);

    return (
      <canvas
        ref={canvasRef}
        className={`viewer-raster${grayscale ? " is-grayscale" : ""}${className ? ` ${className}` : ""}`}
        aria-label="HVAC drawing"
      />
    );
  },
);
