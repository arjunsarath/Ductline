/**
 * Click-to-reveal OCR overlay. Each token is a hit-target rectangle; clicking
 * notifies the parent so an HTML inspector panel can render the crop image
 * alongside the text. Selection state lives in the parent.
 */

import type { DebugOcrMatch } from "../../types/v4";

interface Props {
  drawingW: number;
  drawingH: number;
  matches: DebugOcrMatch[];
  selectedIdx: number | null;
  linkedIdx?: number | null;
  shadeByPressure?: boolean;
  onSelect: (idx: number | null) => void;
}

export function V4OcrOverlay({
  drawingW, drawingH, matches, selectedIdx, linkedIdx,
  shadeByPressure, onSelect,
}: Props) {
  if (drawingW <= 0 || drawingH <= 0 || matches.length === 0) return null;

  return (
    <svg
      className={`v4-ocr-svg${shadeByPressure ? " v4-shade" : ""}`}
      viewBox={`0 0 ${drawingW} ${drawingH}`}
      preserveAspectRatio="xMinYMin meet"
    >
      {matches.map((m, idx) => {
        const [x, y, w, h] = m.bbox;
        const isActive = selectedIdx === idx;
        const isLinked = linkedIdx === idx;
        const points = m.oriented_corners
          ? m.oriented_corners.map(([px, py]) => `${px},${py}`).join(" ")
          : null;
        const shadeCls = shadeByPressure && m.smacna_class
          ? ` v4-shade-${m.smacna_class.toLowerCase()}`
          : "";
        const cls =
          "v4-ocr-match"
          + (isActive ? " is-active" : "")
          + (isLinked ? " is-linked" : "")
          + shadeCls;
        return (
          <g
            key={idx}
            className={cls}
            onClick={(e) => {
              e.stopPropagation();
              onSelect(isActive ? null : idx);
            }}
          >
            {points ? (
              <polygon points={points} className="v4-ocr-rect" />
            ) : (
              <rect x={x} y={y} width={w} height={h} className="v4-ocr-rect" />
            )}
          </g>
        );
      })}
    </svg>
  );
}
