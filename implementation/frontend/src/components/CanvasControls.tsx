/**
 * Zoom / rotate control overlay used by both the raster and PDF canvases.
 * Extracted from the original Viewer so the two canvases share one widget.
 */

import type { Viewport } from "./viewport";

interface Props {
  viewport: Viewport;
  onRotate: () => void;
  onZoomBy: (factor: number) => void;
}

export function CanvasControls({ viewport, onRotate, onZoomBy }: Props) {
  return (
    <div
      className="canvas-controls"
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        className="canvas-control-button"
        onClick={() => onZoomBy(0.8)}
        title="Zoom out"
        aria-label="Zoom out"
      >
        −
      </button>
      <span className="canvas-control-readout mono">
        {Math.round(viewport.scale * 100)}%
      </span>
      <button
        type="button"
        className="canvas-control-button"
        onClick={() => onZoomBy(1.25)}
        title="Zoom in"
        aria-label="Zoom in"
      >
        +
      </button>
      <span className="canvas-control-divider" aria-hidden="true" />
      <button
        type="button"
        className="canvas-control-button"
        onClick={onRotate}
        title="Rotate 90°"
        aria-label="Rotate 90 degrees"
      >
        <RotateIcon />
      </button>
      {viewport.rotationDeg !== 0 && (
        <span className="canvas-control-readout mono">
          {viewport.rotationDeg}°
        </span>
      )}
    </div>
  );
}

function RotateIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path
        d="M11 7 A 4 4 0 1 1 7 3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        fill="none"
      />
      <path
        d="M7 1.5 L7 3.5 L9 3.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}
