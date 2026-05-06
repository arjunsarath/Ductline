/**
 * V4 terminal-detail panel. Per A5 the type letter is shown raw — MVP
 * declines to interpret it.
 */

import type { V4Terminal } from "../../types/v4";

interface Props {
  terminal: V4Terminal;
  onClose: () => void;
}

export function V4TerminalPanel({ terminal, onClose }: Props) {
  return (
    <aside className="v4-detail-panel" aria-label={`Terminal ${terminal.id}`}>
      <header className="v4-detail-head">
        <div>
          <div className="v4-detail-id mono">{terminal.id}</div>
          <div className="v4-detail-kind">Air terminal</div>
        </div>
        <button
          type="button"
          className="v4-detail-close"
          aria-label="Close"
          onClick={onClose}
        >
          ×
        </button>
      </header>

      <dl className="v4-detail-grid">
        <dt>CFM</dt>
        <dd className="v4-detail-strong">
          {terminal.cfm === null ? "—" : terminal.cfm.toFixed(0)}
        </dd>

        <dt>Type letter</dt>
        <dd>
          {terminal.type_letter === null ? (
            <span className="muted">—</span>
          ) : (
            <span className="mono">{terminal.type_letter}</span>
          )}
        </dd>
      </dl>
    </aside>
  );
}
