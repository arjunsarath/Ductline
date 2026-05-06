/**
 * Top bar for the V4 result view. Visual layout matches the V3 result
 * topbar so users moving between modes see consistent rhythm.
 */

import type { V4Result } from "../../types/v4";

interface Props {
  filename: string;
  result: V4Result | null;
  busy: boolean;
  debug: boolean;
  onReset: () => void;
  onOpenSettings: () => void;
  onToggleDebug: (next: boolean) => void;
}

export function V4Topbar({
  filename,
  result,
  busy,
  debug,
  onReset,
  onOpenSettings,
  onToggleDebug,
}: Props) {
  return (
    <header className="result-topbar">
      <div className="result-topbar-left">
        <div className="brand">Ductline · V4</div>
        <button type="button" className="button-ghost" onClick={onReset}>
          ← New drawing
        </button>
        <div className="result-topbar-meta">
          <span className="mono">{filename}</span>
          {result && (
            <>
              <span>·</span>
              <strong>{result.segments.length} segments</strong>
              <span>·</span>
              <strong>{result.terminals.length} terminals</strong>
              <span>·</span>
              <span>
                scale {result.scale.paper_inches_per_foot.toFixed(3)}″/ft (
                {result.scale.source})
              </span>
            </>
          )}
        </div>
      </div>
      <div className="result-topbar-spacer" />
      <div className="result-topbar-right">
        <label className="v4-debug-toggle">
          <input
            type="checkbox"
            checked={debug}
            disabled={busy}
            onChange={(e) => onToggleDebug(e.target.checked)}
          />
          <span>Show all detections</span>
        </label>
        <button
          type="button"
          className="button-ghost"
          onClick={onOpenSettings}
          disabled={busy}
        >
          Calculation settings
        </button>
      </div>
    </header>
  );
}
