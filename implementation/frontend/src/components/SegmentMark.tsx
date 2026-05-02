/**
 * SVG mark for one detected duct segment. Geometry points are in whatever
 * coord space the parent <svg viewBox> uses — pixels for the raster canvas,
 * PDF points for the PdfCanvas. The component does not convert; it draws.
 */

import type { Segment } from "../types/api";
import { pressureClassColor } from "./canvasShared";

interface Props {
  segment: Segment;
  isSelected: boolean;
  onSelect: () => void;
  /**
   * Stroke base width in viewBox units. The raster canvas uses 2.5 (default);
   * PDF-point space is much smaller (~600 × 800 vs ~6000 × 8000 px) so the
   * caller can scale this down to keep the on-screen stroke the same.
   */
  strokeBase?: number;
}

export function SegmentMark({
  segment,
  isSelected,
  onSelect,
  strokeBase = 2.5,
}: Props) {
  const stroke = pressureClassColor(segment.pressure_class.value);
  const dashed = segment.pressure_class.confidence !== "high";
  const widthBase = isSelected ? strokeBase * 2 : strokeBase;
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
    const points = segment.geometry.points
      .map(([x, y]) => `${x},${y}`)
      .join(" ");
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
