/**
 * Workflow step: operator marks the drawing-area rectangle on the cleaned
 * page so downstream detection ignores title block, plan notes, and other
 * non-duct regions. Drag to draw; release commits to local state. The user
 * confirms with one of the two actions: use the marked area or use the full
 * page. Coordinates are in raster pixel space (page_dims).
 */

import { useCallback, useRef, useState } from "react";
import type { CropArea } from "../../api/v4Client";
import type { V4Result } from "../../types/v4";

interface Props {
  cleaned: V4Result;
  onConfirm: (area: CropArea | null) => void;
  onCancel: () => void;
}

export function V4MarkAreaScreen({ cleaned, onConfirm, onCancel }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [draft, setDraft] = useState<CropArea | null>(null);
  const pageW = cleaned.page_dims.width_px;
  const pageH = cleaned.page_dims.height_px;

  const eventToPageCoord = useCallback(
    (event: MouseEvent | React.MouseEvent) => {
      const el = containerRef.current;
      if (!el) return null;
      const rect = el.getBoundingClientRect();
      const fx = (event.clientX - rect.left) / Math.max(rect.width, 1);
      const fy = (event.clientY - rect.top) / Math.max(rect.height, 1);
      return {
        x: Math.round(Math.max(0, Math.min(1, fx)) * pageW),
        y: Math.round(Math.max(0, Math.min(1, fy)) * pageH),
      };
    },
    [pageW, pageH],
  );

  const onMouseDown = useCallback(
    (event: React.MouseEvent) => {
      if (event.button !== 0) return;
      const start = eventToPageCoord(event);
      if (!start) return;
      // Listeners are attached synchronously on mousedown so the first
      // pointer movement is captured. The previous useEffect-based pattern
      // had a render-cycle race that dropped the initial drag and left
      // ``draft`` at zero size on first use.
      const handleMove = (ev: MouseEvent) => {
        const p = eventToPageCoord(ev);
        if (!p) return;
        setDraft({
          x: Math.min(start.x, p.x),
          y: Math.min(start.y, p.y),
          w: Math.abs(p.x - start.x),
          h: Math.abs(p.y - start.y),
        });
      };
      const handleUp = () => {
        window.removeEventListener("mousemove", handleMove);
        window.removeEventListener("mouseup", handleUp);
      };
      window.addEventListener("mousemove", handleMove);
      window.addEventListener("mouseup", handleUp);
      setDraft({ x: start.x, y: start.y, w: 0, h: 0 });
    },
    [eventToPageCoord],
  );

  const hasArea = !!draft && draft.w > 4 && draft.h > 4;

  return (
    <main className="v4-mark-area">
      <header className="v4-mark-area__head">
        <div>
          <h2>Mark drawing area</h2>
          <p>
            Drag a rectangle around the duct drawing only. Title block, plan
            notes, schedules, and other regions outside the rectangle are
            ignored during detection.
          </p>
        </div>
        <div className="v4-mark-area__actions">
          <button
            type="button"
            className="v4-mark-area__btn v4-mark-area__btn--ghost"
            onClick={() => onConfirm(null)}
          >
            Use full page
          </button>
          <button
            type="button"
            className="v4-mark-area__btn"
            disabled={!hasArea}
            onClick={() => draft && onConfirm(draft)}
          >
            Continue with selection
          </button>
          <button
            type="button"
            className="v4-mark-area__btn v4-mark-area__btn--ghost"
            onClick={onCancel}
          >
            Cancel
          </button>
        </div>
      </header>

      <div className="v4-mark-area__stage">
        <div
          ref={containerRef}
          className="v4-mark-area__canvas"
          onMouseDown={onMouseDown}
        >
          {cleaned.stage_image_data_url && (
            <img
              src={cleaned.stage_image_data_url}
              alt="Cleaned drawing"
              draggable={false}
            />
          )}
          {draft && (
            <svg
              className="v4-mark-area__svg"
              viewBox={`0 0 ${pageW} ${pageH}`}
              preserveAspectRatio="xMinYMin meet"
            >
              <rect
                className="v4-mark-area__rect"
                x={draft.x}
                y={draft.y}
                width={draft.w}
                height={draft.h}
              />
            </svg>
          )}
        </div>
      </div>

      {hasArea && (
        <footer className="v4-mark-area__foot">
          Selection: {draft!.w}×{draft!.h} px @ ({draft!.x}, {draft!.y})
        </footer>
      )}
    </main>
  );
}
