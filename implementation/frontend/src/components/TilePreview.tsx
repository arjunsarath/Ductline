/**
 * TilePreview — shows the active tile rendered at 100% (V2 §5.8).
 *
 * The user wants to see exactly what the model sees on each call: are
 * the duct lines readable at the per-tile DPI, or are they too small
 * (model misses callouts) or too large (we waste tokens)? The tile
 * crop is rendered client-side via PDF.js when the source is a vector
 * PDF, or cropped from the raster_probe data URL when it's raster.
 *
 * Source rect comes from the awaiting_tiling_approval payload; the
 * frontend tracks which tile is "active" via the tile_start /
 * tile_done events in processingProgress.ts.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import type { ActiveTile } from "./processingProgress";
import type { TilingApprovalPayload } from "../api/client";

// PDF.js worker — registered once at module scope. Same pattern as
// PdfCanvas.tsx so we don't double-register.
if (typeof pdfjs.GlobalWorkerOptions !== "undefined") {
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
}

interface Props {
  /** Original uploaded File — needed to render PDF tiles via PDF.js. */
  file: File | null;
  /** Tile plan from awaiting_tiling_approval (frozen once approved). */
  plan: TilingApprovalPayload | null;
  /** Currently-active tile from progress.activeTile. */
  active: ActiveTile | null;
  /** coord_space for interpreting tile rects ("pdf_points" or "pixels"). */
  coordSpace: "pdf_points" | "pixels";
  /** Display image data URL for the raster path (when coord_space is pixels). */
  rasterDataUrl: string | null;
}

export function TilePreview({
  file,
  plan,
  active,
  coordSpace,
  rasterDataUrl,
}: Props) {
  const tileRect = useMemo(() => {
    if (!active || !plan) return null;
    const match = plan.tiles.find(
      (t) => t.row === active.row && t.col === active.col,
    );
    return match?.rect ?? null;
  }, [active, plan]);

  if (!active || !plan || !tileRect) {
    return (
      <aside className="tile-preview tile-preview-empty">
        <span className="eyebrow">Live tile preview</span>
        <p>Waiting for the next tile…</p>
      </aside>
    );
  }

  return (
    <aside className="tile-preview" aria-label="Live tile preview">
      <header className="tile-preview-head">
        <span className="eyebrow">Tile preview · 100%</span>
        <div className="tile-preview-meta mono">
          ({active.row}, {active.col}) of ({plan.tiles[plan.tiles.length - 1].total_rows},{" "}
          {plan.tiles[plan.tiles.length - 1].total_cols})
          {" · "}
          {Math.round(tileRect[2] - tileRect[0])}×
          {Math.round(tileRect[3] - tileRect[1])}{" "}
          {coordSpace === "pdf_points" ? "pt" : "px"}
          {coordSpace === "pdf_points" ? ` · ${plan.dpi} DPI` : ""}
        </div>
        <div className="tile-preview-status">
          {active.segmentsFound == null
            ? "calling model…"
            : `${active.segmentsFound} segment${active.segmentsFound === 1 ? "" : "s"} returned`}
        </div>
      </header>

      {coordSpace === "pdf_points" && file ? (
        <PdfTileCanvas
          file={file}
          tileRect={tileRect}
          dpi={plan.dpi}
          rotation={plan.rotation_applied}
        />
      ) : rasterDataUrl ? (
        <RasterTileCrop dataUrl={rasterDataUrl} tileRect={tileRect} />
      ) : (
        <div className="tile-preview-empty">
          No source available to render tile preview.
        </div>
      )}
    </aside>
  );
}

/**
 * Render a PDF tile rect to a canvas at the per-tile DPI. Re-renders only
 * when the rect or DPI changes. Cancels the in-flight render on unmount /
 * tile change to avoid stale paints.
 */
function PdfTileCanvas({
  file,
  tileRect,
  dpi,
  rotation,
}: {
  file: File;
  tileRect: [number, number, number, number];
  dpi: number;
  rotation: 0 | 90 | 180 | 270;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let renderTask: { cancel: () => void; promise: Promise<void> } | null = null;

    async function render() {
      try {
        const arrayBuffer = await file.arrayBuffer();
        if (cancelled) return;
        const pdf = await pdfjs.getDocument({ data: arrayBuffer }).promise;
        if (cancelled) return;
        const page = await pdf.getPage(1);
        if (cancelled) return;

        // dpi/72 = pixels per PDF point. Rotation must match the backend's
        // baked-in rotation so tile rects (in rotated-page coords) line up.
        const scale = dpi / 72;
        const [x0, y0, x1, y1] = tileRect;
        const viewport = page.getViewport({ scale, rotation });

        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;

        // Render the full page into an offscreen canvas, then crop the
        // tile rect into the visible canvas. Less efficient than
        // page.getViewport({clip:...}) but PDF.js doesn't expose clip
        // in the public API for 5.x; the offscreen approach is portable.
        const offscreen = document.createElement("canvas");
        offscreen.width = viewport.width;
        offscreen.height = viewport.height;
        const offCtx = offscreen.getContext("2d");
        if (!offCtx) return;

        renderTask = page.render({ canvasContext: offCtx, viewport, canvas: offscreen });
        await renderTask.promise;
        if (cancelled) return;

        const cropX = Math.round(x0 * scale);
        const cropY = Math.round(y0 * scale);
        const cropW = Math.max(1, Math.round((x1 - x0) * scale));
        const cropH = Math.max(1, Math.round((y1 - y0) * scale));

        canvas.width = cropW;
        canvas.height = cropH;
        ctx.drawImage(
          offscreen,
          cropX,
          cropY,
          cropW,
          cropH,
          0,
          0,
          cropW,
          cropH,
        );
      } catch (exc) {
        if (!cancelled) {
          setError(exc instanceof Error ? exc.message : "render failed");
        }
      }
    }

    void render();
    return () => {
      cancelled = true;
      if (renderTask) {
        try {
          renderTask.cancel();
        } catch {
          /* ignore */
        }
      }
    };
  }, [file, tileRect, dpi, rotation]);

  if (error) {
    return <div className="tile-preview-error">{error}</div>;
  }
  return <canvas className="tile-preview-canvas" ref={canvasRef} />;
}

/** Crop a tile rect from the raster_probe data URL onto a canvas. */
function RasterTileCrop({
  dataUrl,
  tileRect,
}: {
  dataUrl: string;
  tileRect: [number, number, number, number];
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      if (cancelled) return;
      const [x0, y0, x1, y1] = tileRect;
      const w = Math.max(1, x1 - x0);
      const h = Math.max(1, y1 - y0);
      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.drawImage(img, x0, y0, w, h, 0, 0, w, h);
    };
    img.src = dataUrl;
    return () => {
      cancelled = true;
    };
  }, [dataUrl, tileRect]);

  return <canvas className="tile-preview-canvas" ref={canvasRef} />;
}
