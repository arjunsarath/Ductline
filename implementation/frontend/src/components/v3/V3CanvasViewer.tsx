/**
 * V3 canvas viewer. Layered raster (page underneath, transparent overlay
 * on top) + SVG hit-target circles for clickable segments. Reuses V1's
 * viewport math + CanvasControls so behaviour matches: wheel-to-zoom
 * anchored at cursor, drag-to-pan, rotate button, zoom buttons.
 *
 * Grayscale toggle applies to the page layer only — the overlay's mask
 * tint, contours, and segment markers stay color so the user can
 * compare detection against the desaturated page.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { CanvasControls } from "../CanvasControls";
import {
  clamp,
  cursorInStage,
  scaleAroundPoint,
} from "../canvasShared";
import { SCALE_MAX, SCALE_MIN, type Viewport } from "../viewport";
import { V3Popover } from "./V3Popover";
import { V3PageCanvas } from "./V3PageCanvas";
import type { V3Segment } from "../../types/v3";

interface Props {
  /** Page render PNG (data URL or base64). Always present as the
   *  fallback for non-PDF inputs. */
  pageSrc: string;
  /** Transparent overlay PNG (data URL or base64). */
  overlaySrc: string | null;
  /** Original uploaded file. Used to vector-render PDFs at any zoom
   *  (PDF.js dynamic re-rasterization). For raster sources we ignore
   *  this and just use the baked PNG. */
  file: File;
  /** Clockwise rotation the backend baked into the result coord space. */
  rotationApplied: number;
  drawingW: number;
  drawingH: number;
  segments: V3Segment[];
  selectedId: string | null;
  grayscale: boolean;
  viewport: Viewport;
  onViewportChange: (next: Viewport) => void;
  onSelect: (id: string | null) => void;
  onRotate: () => void;
  onZoomBy: (factor: number) => void;
}

export function V3CanvasViewer({
  pageSrc,
  overlaySrc,
  file,
  rotationApplied,
  drawingW,
  drawingH,
  segments,
  selectedId,
  grayscale,
  viewport,
  onViewportChange,
  onSelect,
  onRotate,
  onZoomBy,
}: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const pageImgRef = useRef<HTMLImageElement | null>(null);

  const isPdf = useMemo(
    () => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"),
    [file],
  );

  // Match .viewer-raster's CSS sizing (max-width: 80vw, max-height: 100vh-200px,
  // object-fit: contain) so the canvas path lays out identically to the img
  // path. Recomputed on resize via setLayoutTick.
  const [winSize, setWinSize] = useState({
    w: typeof window !== "undefined" ? window.innerWidth : 1200,
    h: typeof window !== "undefined" ? window.innerHeight : 900,
  });
  const fit = useMemo(() => {
    if (!drawingW || !drawingH) return { w: 0, h: 0 };
    const maxW = winSize.w * 0.8;
    const maxH = Math.max(200, winSize.h - 200);
    const aspect = drawingW / drawingH;
    let w = maxW;
    let h = w / aspect;
    if (h > maxH) {
      h = maxH;
      w = h * aspect;
    }
    return { w, h };
  }, [winSize, drawingW, drawingH]);

  // Bump on image load + on window resize so the marker layer
  // re-renders once we know the page's layout dimensions.
  // ``setLayoutTick`` is used as a render trigger; the value isn't read.
  const [, setLayoutTick] = useState(0);
  useEffect(() => {
    const onResize = () => {
      setWinSize({ w: window.innerWidth, h: window.innerHeight });
      setLayoutTick((t) => t + 1);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const dragRef = useRef<{
    startX: number;
    startY: number;
    startTx: number;
    startTy: number;
  } | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  // Wheel zoom anchored at cursor — same math as V1 RasterCanvas
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

  const selectedSegment = selectedId
    ? (segments.find((s) => s.id === selectedId) ?? null)
    : null;

  // Popover anchor in viewport coords. We read the selected SVG group's
  // bounding rect in screen space — that already accounts for the full
  // transform chain (translate + scale + rotate) the parent applies, so
  // we don't need to re-derive it from the viewport state. Recompute on
  // every viewport change so the popover follows the marker.
  const [popoverAnchor, setPopoverAnchor] = useState<{ x: number; y: number } | null>(null);
  useLayoutEffect(() => {
    if (!selectedSegment) {
      setPopoverAnchor(null);
      return;
    }
    const stage = stageRef.current;
    if (!stage) return;
    const stageRect = stage.getBoundingClientRect();
    // Find the SVG group for this segment
    const groupEl = stage.querySelector<SVGGElement>(
      `[data-seg-id="${selectedSegment.id}"]`,
    );
    if (!groupEl) {
      setPopoverAnchor(null);
      return;
    }
    const r = groupEl.getBoundingClientRect();
    setPopoverAnchor({
      x: r.left + r.width / 2 - stageRect.left,
      y: r.top + r.height / 2 - stageRect.top,
    });
  }, [selectedSegment, viewport]);

  const transform = `translate(${viewport.tx}px, ${viewport.ty}px) scale(${viewport.scale}) rotate(${viewport.rotationDeg}deg)`;

  // Compute the screen position of each segment's marker by replaying
  // the same translate/scale/rotate the parent .viewer-content layer
  // applies to the page raster. We render markers in a sibling SVG that
  // sits OUTSIDE the transform stack — so circles and text stay a
  // constant on-screen size at any zoom, like UI annotations rather
  // than scaled page content.
  //
  // The visible-page layout is: image natural size laid out at top-left
  // of .viewer-content; .viewer-content has transform-origin at center
  // (50% 50%, the CSS default) and transform = translate(tx, ty)
  // scale(s) rotate(deg). We invert that here for each segment.
  // Fit size for the page element. For PDFs we drive both the canvas
  // sizing and overlay sizing from this single computed value so marker
  // math is identical to the img path.
  const fitWidth = isPdf ? fit.w : (pageImgRef.current?.clientWidth ?? 0);
  const fitHeight = isPdf ? fit.h : (pageImgRef.current?.clientHeight ?? 0);

  // Constant on-screen sizes — picked to match the sidebar segment-card
  // visual weight. Ring width is the click target; the inner dot is the
  // visible cue that something is there.
  const RING_R = 13;
  const DOT_R = 4;

  const stageWidth = stageRef.current?.clientWidth ?? 0;
  const stageHeight = stageRef.current?.clientHeight ?? 0;

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
        {isPdf && fit.w > 0 ? (
          <V3PageCanvas
            file={file}
            rotation={(rotationApplied % 360) as 0 | 90 | 180 | 270}
            viewportScale={viewport.scale}
            fitWidth={fit.w}
            fitHeight={fit.h}
            grayscale={grayscale}
          />
        ) : (
          <img
            ref={pageImgRef}
            src={pageSrc}
            alt="rendered drawing"
            className={`viewer-raster${grayscale ? " is-grayscale" : ""}`}
            draggable={false}
            onLoad={() => setLayoutTick((t) => t + 1)}
          />
        )}
        {overlaySrc && (
          <img
            src={overlaySrc}
            alt="detection overlay"
            className="viewer-overlay-img"
            draggable={false}
          />
        )}
      </div>

      {/* Marker layer — rendered OUTSIDE the transform stack so markers
       *  stay constant size at any zoom. Positions are computed in screen
       *  pixels from the segment's page-pixel coords + viewport state. */}
      <svg
        className="v3-marker-layer"
        aria-label="duct segment markers"
      >
        {fitWidth > 0 && fitHeight > 0 && stageWidth > 0 && segments.map((seg) => {
          const screen = pageToViewer(
            seg.skel_xy[0], seg.skel_xy[1],
            drawingW, drawingH,
            fitWidth, fitHeight,
            stageWidth, stageHeight,
            viewport,
          );
          const isSel = seg.id === selectedId;
          const conf = seg.dim_confidence;
          return (
            <g
              key={seg.id}
              data-seg-id={seg.id}
              className={`v3-segment-mark${isSel ? " is-selected" : ""}`}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onSelect(seg.id);
              }}
            >
              {/* Outer ring is the click target + visible affordance */}
              <circle cx={screen.x} cy={screen.y} r={RING_R}
                      className={`v3-ring v3-ring-${conf}`} />
              {/* Inner filled dot — the dot says "here" without a label */}
              <circle cx={screen.x} cy={screen.y} r={DOT_R}
                      className={`v3-dot v3-dot-${conf}`} />
            </g>
          );
        })}
      </svg>

      {selectedSegment && popoverAnchor && (
        <V3Popover
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

/**
 * Map a segment's page-pixel coords to viewer-relative screen pixel coords,
 * replaying the transform applied to ``.viewer-content``:
 *   transform: translate(tx, ty) scale(s) rotate(deg)
 *   transform-origin: 50% 50%   (the CSS default for transform)
 *
 * The image is laid out at the viewer-content's top-left at its fit size
 * (fitWidth × fitHeight). transform-origin = center means scaling and
 * rotation pivot around the image's center, not its top-left. We invert
 * that here so the SVG marker layer (sibling of .viewer-content, not
 * inside the transform) can place markers at the correct on-screen spot.
 *
 * Returned coords are relative to the .viewer element's top-left, which
 * is also the SVG layer's coord system because the layer is positioned
 * at inset:0 inside .viewer.
 */
function pageToViewer(
  pageX: number,
  pageY: number,
  drawingW: number,
  drawingH: number,
  fitWidth: number,
  fitHeight: number,
  stageWidth: number,
  stageHeight: number,
  viewport: Viewport,
): { x: number; y: number } {
  // ``.viewer`` uses ``display: grid; place-items: center``, so the
  // unscaled .viewer-content's top-left sits at the centering offset
  // (stage center − content center). Transforms then scale + translate
  // the content from there.
  const contentLeft = (stageWidth - fitWidth) / 2;
  const contentTop = (stageHeight - fitHeight) / 2;

  // Page coords → fit-coords (image at scale=1)
  const fx = (pageX / drawingW) * fitWidth;
  const fy = (pageY / drawingH) * fitHeight;

  // Apply rotation around image center
  const cx = fitWidth / 2;
  const cy = fitHeight / 2;
  const rad = (viewport.rotationDeg * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const rdx = (fx - cx) * cos - (fy - cy) * sin;
  const rdy = (fx - cx) * sin + (fy - cy) * cos;

  // Apply scale around image center, then translate
  const dx = rdx * viewport.scale + viewport.tx;
  const dy = rdy * viewport.scale + viewport.ty;
  return {
    x: contentLeft + cx + dx,
    y: contentTop + cy + dy,
  };
}
