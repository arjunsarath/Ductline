/**
 * Floating filter console — exposes the rectangle-shape filter properties so
 * the operator can iterate live. Sliders commit on release; toggle commits
 * on click. Oversized is on permanently and not exposed (working as-is).
 * Other legacy filters are off and not surfaced for now.
 */

import { useEffect, useState } from "react";
import type { CropArea } from "../../api/v4Client";

interface Props {
  enabled: boolean;
  epsilonFrac: number;
  maxCornerCos: number;
  busy: boolean;
  totals: { total: number; kept: number; byReason: Record<string, number> };
  onCommit: (next: {
    enabled: boolean;
    epsilonFrac: number;
    maxCornerCos: number;
  }) => void;
  showDropped: boolean;
  onToggleShowDropped: (next: boolean) => void;
  cropArea: CropArea | null;
  onRedefineArea: () => void;
}

export function V4FilterConsole(props: Props) {
  const {
    enabled, epsilonFrac, maxCornerCos, busy, totals, onCommit,
    showDropped, onToggleShowDropped, cropArea, onRedefineArea,
  } = props;
  const [eps, setEps] = useState(epsilonFrac);
  const [cosMax, setCosMax] = useState(maxCornerCos);

  useEffect(() => setEps(epsilonFrac), [epsilonFrac]);
  useEffect(() => setCosMax(maxCornerCos), [maxCornerCos]);

  const commit = () => {
    if (eps !== epsilonFrac || cosMax !== maxCornerCos) {
      onCommit({ enabled, epsilonFrac: eps, maxCornerCos: cosMax });
    }
  };

  return (
    <aside className="v4-debug-panel" role="region" aria-label="Filter console">
      <header className="v4-debug-panel__head">
        <strong>Rectangle filter</strong>
        <span className="v4-debug-panel__stopped">
          {totals.kept}/{totals.total} kept
        </span>
      </header>

      <div className="v4-debug-panel__row">
        <span className="v4-debug-panel__label-line">Drawing area</span>
        <div className="v4-debug-panel__crop-row">
          <button
            type="button"
            className="v4-debug-panel__btn v4-debug-panel__btn--ghost"
            onClick={onRedefineArea}
            disabled={busy}
          >
            Redefine area
          </button>
        </div>
        <p className="v4-debug-panel__hint">
          {cropArea
            ? `Active: ${cropArea.w}×${cropArea.h} px @ (${cropArea.x}, ${cropArea.y})`
            : "Full page (no mask)."}
        </p>
      </div>

      <div className="v4-debug-panel__row">
        <label className="v4-debug-panel__check">
          <input
            type="checkbox"
            checked={enabled}
            disabled={busy}
            onChange={(e) => onCommit({
              enabled: e.target.checked,
              epsilonFrac: eps,
              maxCornerCos: cosMax,
            })}
          />
          <span style={{ flex: 1 }}>Rectangles only</span>
          <span className="v4-debug-panel__reason-count">
            {enabled ? totals.byReason["not_rectangle"] ?? 0 : "off"}
          </span>
        </label>
      </div>

      <div className="v4-debug-panel__row">
        <label htmlFor="v4-cos">
          Corner squareness (cos max)
          <span className="v4-debug-panel__value">{cosMax.toFixed(2)}</span>
        </label>
        <input
          id="v4-cos"
          type="range"
          min={0.0}
          max={0.5}
          step={0.01}
          value={cosMax}
          disabled={busy || !enabled}
          onChange={(e) => setCosMax(Number(e.target.value))}
          onMouseUp={commit}
          onTouchEnd={commit}
          onKeyUp={commit}
        />
        <p className="v4-debug-panel__hint">
          0.00 = perfect right angles. 0.25 ≈ ±15° tolerance. 0.50 ≈ ±30°.
        </p>
      </div>

      <div className="v4-debug-panel__row">
        <label htmlFor="v4-eps">
          Polygon simplification
          <span className="v4-debug-panel__value">
            {(eps * 100).toFixed(1)}%
          </span>
        </label>
        <input
          id="v4-eps"
          type="range"
          min={0.005}
          max={0.05}
          step={0.005}
          value={eps}
          disabled={busy || !enabled}
          onChange={(e) => setEps(Number(e.target.value))}
          onMouseUp={commit}
          onTouchEnd={commit}
          onKeyUp={commit}
        />
        <p className="v4-debug-panel__hint">
          approxPolyDP epsilon as % of perimeter. Higher = more aggressive
          smoothing (more shapes resolve to 4 vertices).
        </p>
      </div>

      <div className="v4-debug-panel__row">
        <label className="v4-debug-panel__check">
          <input
            type="checkbox"
            checked={showDropped}
            onChange={(e) => onToggleShowDropped(e.target.checked)}
          />
          <span style={{ flex: 1 }}>Show dropped rectangles</span>
        </label>
      </div>
    </aside>
  );
}
