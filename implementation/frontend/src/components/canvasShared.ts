/**
 * Shared helpers for the raster + PDF canvases. Lives in a `.ts` module
 * (no JSX) so React Fast Refresh isn't disrupted by mixing components and
 * non-components in a single file (see comment in viewport.ts).
 *
 * The PDF.js path uses PDF-point space (`drawingW = page_size_pt[0]`); the
 * raster path uses image-pixel space (`drawingW = result.width_px`). The
 * popover-anchor math is identical in both — the inputs are in whatever
 * coord space the SVG's viewBox uses.
 */

import type { Segment } from "../types/api";
import type { Viewport } from "./viewport";

export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export function pressureClassColor(
  value: Segment["pressure_class"]["value"],
): string {
  switch (value) {
    case "LOW":
      return "#059669";
    case "MEDIUM":
      return "#ea580c";
    case "HIGH":
      return "#dc2626";
  }
}

export function cursorInStage(
  event: WheelEvent,
  stage: HTMLElement,
): { x: number; y: number } {
  const rect = stage.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

/**
 * Adjust translate so the world point under `cursor` stays put when scale
 * changes. Standard cursor-anchored zoom math.
 */
export function scaleAroundPoint(
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
 * Map a segment's centroid (in `drawingW × drawingH` coord space) to
 * stage-screen anchor coords by replaying the same transform CSS applies to
 * the content wrapper.
 *
 * `fitWidth`/`fitHeight` are the rendered (object-fit:contain or canvas
 * client) size of the base layer — image for raster, canvas for PDF.js.
 */
export function computePopoverAnchor(
  segment: Segment,
  drawingW: number,
  drawingH: number,
  fitWidth: number,
  fitHeight: number,
  stage: HTMLElement | null,
  viewport: Viewport,
): { x: number; y: number } | null {
  if (!stage) return null;
  if (!fitWidth || !fitHeight) return null;
  if (!drawingW || !drawingH) return null;

  // Centroid in drawing coords.
  const sumX = segment.geometry.points.reduce((acc, [x]) => acc + x, 0);
  const sumY = segment.geometry.points.reduce((acc, [, y]) => acc + y, 0);
  const cx = sumX / segment.geometry.points.length;
  const cy = sumY / segment.geometry.points.length;

  // Centroid in fit-coords (the untransformed content layout).
  let px = (cx / drawingW) * fitWidth;
  let py = (cy / drawingH) * fitHeight;

  // Rotate around the content center.
  const cxImg = fitWidth / 2;
  const cyImg = fitHeight / 2;
  const rad = (viewport.rotationDeg * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const dx = px - cxImg;
  const dy = py - cyImg;
  px = cxImg + dx * cos - dy * sin;
  py = cyImg + dx * sin + dy * cos;

  // Apply scale around content center, then translate by viewport.t{x,y}.
  const stageRect = stage.getBoundingClientRect();
  const contentLeft = stageRect.width / 2 - fitWidth / 2 + viewport.tx;
  const contentTop = stageRect.height / 2 - fitHeight / 2 + viewport.ty;

  const sx = (px - cxImg) * viewport.scale + cxImg;
  const sy = (py - cyImg) * viewport.scale + cyImg;

  return { x: contentLeft + sx, y: contentTop + sy };
}
