"use client";

import { useEffect, useRef } from "react";
import { type Element, TYPE_COLORS } from "@/lib/extract";

type Props = {
  elements: Element[];
  scale: number;
  pageWidth: number;
  pageHeight: number;
  showLabels: boolean;
  highlightedId: string | null;
  hoveredId: string | null;
};

// Drawing all elements as SVG nodes melts the main thread above ~5k items.
// Canvas does the heavy paint in one pass; the active element gets a separate
// SVG overlay so hover/highlight doesn't trigger a full canvas redraw.
export default function ElementOverlay({
  elements,
  scale,
  pageWidth,
  pageHeight,
  showLabels,
  highlightedId,
  hoveredId,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Crisper output when the user CSS-zooms into the page.
    const dpr = 2;
    canvas.width = Math.max(1, Math.round(pageWidth * dpr));
    canvas.height = Math.max(1, Math.round(pageHeight * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, pageWidth, pageHeight);

    ctx.lineCap = "butt";
    ctx.lineJoin = "miter";

    // globalAlpha=1 lets the per-colour rgba fill alphas (set in TYPE_COLORS)
    // compound naturally when rectangles overlap, so stacked elements render
    // darker and become visually distinguishable.
    ctx.globalAlpha = 1;

    for (const el of elements) {
      const color = TYPE_COLORS[el.type];
      const x = el.x0 * scale;
      const y = el.top * scale;
      const w = Math.max(1, (el.x1 - el.x0) * scale);
      const h = Math.max(1, (el.bottom - el.top) * scale);

      if (el.type === "line") {
        ctx.strokeStyle = color.stroke;
        ctx.lineWidth = Math.max(1, el.linewidth * scale);
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(el.x0 * scale, el.top * scale);
        ctx.lineTo(el.x1 * scale, el.bottom * scale);
        ctx.stroke();
      } else if (el.type === "rect_partial") {
        // Draw the actual U-shape, not the bbox — the bbox over-claims.
        ctx.strokeStyle = color.stroke;
        ctx.lineWidth = 1.5;
        ctx.setLineDash([]);
        if (el.points.length > 1) {
          ctx.beginPath();
          ctx.moveTo(el.points[0][0] * scale, el.points[0][1] * scale);
          for (let i = 1; i < el.points.length; i++) {
            ctx.lineTo(el.points[i][0] * scale, el.points[i][1] * scale);
          }
          ctx.stroke();
        }
      } else if (el.type === "inferred_rect") {
        // Inferred from a pair of partials — dashed to flag "not in the PDF".
        ctx.strokeStyle = color.stroke;
        ctx.fillStyle = color.fill;
        ctx.lineWidth = 1;
        ctx.fillRect(x, y, w, h);
        ctx.setLineDash([4, 3]);
        ctx.strokeRect(x, y, w, h);
        ctx.setLineDash([]);
      } else if (el.type === "curve") {
        ctx.strokeStyle = color.stroke;
        ctx.fillStyle = color.fill;
        ctx.lineWidth = 1;
        ctx.setLineDash([]);
        ctx.fillRect(x, y, w, h);
        ctx.strokeRect(x, y, w, h);
        if (el.points.length > 1) {
          ctx.beginPath();
          ctx.moveTo(el.points[0][0] * scale, el.points[0][1] * scale);
          for (let i = 1; i < el.points.length; i++) {
            ctx.lineTo(el.points[i][0] * scale, el.points[i][1] * scale);
          }
          ctx.stroke();
        }
      } else {
        ctx.strokeStyle = color.stroke;
        ctx.fillStyle = color.fill;
        ctx.lineWidth = 1;
        ctx.setLineDash([]);
        ctx.fillRect(x, y, w, h);
        ctx.strokeRect(x, y, w, h);
      }
    }

    if (showLabels) {
      ctx.globalAlpha = 1;
      ctx.font = "9px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.textBaseline = "bottom";
      for (const el of elements) {
        const color = TYPE_COLORS[el.type];
        ctx.fillStyle = color.stroke;
        ctx.fillText(el.id, el.x0 * scale + 2, el.top * scale - 1);
      }
    }
  }, [elements, scale, pageWidth, pageHeight, showLabels]);

  const activeId = highlightedId ?? hoveredId;
  const active = activeId ? elements.find((e) => e.id === activeId) : null;

  return (
    <>
      <canvas
        ref={canvasRef}
        className="pointer-events-none absolute left-0 top-0"
        style={{ width: pageWidth, height: pageHeight }}
      />
      {active && (
        <svg
          className="pointer-events-none absolute left-0 top-0"
          width={pageWidth}
          height={pageHeight}
          viewBox={`0 0 ${pageWidth} ${pageHeight}`}
        >
          <ActiveMark element={active} scale={scale} />
        </svg>
      )}
    </>
  );
}

function ActiveMark({ element, scale }: { element: Element; scale: number }) {
  const color = TYPE_COLORS[element.type];
  const x = element.x0 * scale;
  const y = element.top * scale;
  const w = Math.max(2, (element.x1 - element.x0) * scale);
  const h = Math.max(2, (element.bottom - element.top) * scale);

  if (element.type === "line") {
    return (
      <line
        x1={element.x0 * scale}
        y1={element.top * scale}
        x2={element.x1 * scale}
        y2={element.bottom * scale}
        stroke={color.stroke}
        strokeWidth={3}
      />
    );
  }
  return (
    <rect
      x={x - 2}
      y={y - 2}
      width={w + 4}
      height={h + 4}
      fill="none"
      stroke={color.stroke}
      strokeWidth={2}
      strokeDasharray="4 2"
    />
  );
}
