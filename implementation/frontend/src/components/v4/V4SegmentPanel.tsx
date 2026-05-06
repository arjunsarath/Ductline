/**
 * V4 segment-detail panel (SOLUTION-DESIGN-V4 §7).
 *
 * Renders the contract from the brief: dimension, length, CFM range
 * (start → end if the segment is multi-terminal per A10, otherwise single),
 * velocity, pressure at both ends, SMACNA-class badge, and any segment-scoped
 * warnings.
 */

import type { SmacnaThresholds, V4Segment } from "../../types/v4";

interface Props {
  segment: V4Segment;
  thresholds: SmacnaThresholds;
  /** Pipeline warnings filtered to this segment (matched by id substring). */
  warnings: string[];
  onClose: () => void;
}

export function V4SegmentPanel({ segment, thresholds, warnings, onClose }: Props) {
  const cfm = segment.cfm_range;
  const cfmIsRange = Math.abs(cfm.start - cfm.end) > 0.5;
  const tone = segment.pressure.smacna_class.toLowerCase();
  const tooltip = smacnaTooltip(thresholds);

  return (
    <aside className="v4-detail-panel" aria-label={`Segment ${segment.id}`}>
      <header className="v4-detail-head">
        <div>
          <div className="v4-detail-id mono">{segment.id}</div>
          <div className="v4-detail-kind">Duct segment</div>
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
        <dt>Dimension</dt>
        <dd className="v4-detail-strong">{segment.dimension || "—"}</dd>

        <dt>Length</dt>
        <dd>{segment.length_ft.toFixed(1)} ft</dd>

        <dt>CFM</dt>
        <dd>
          {cfmIsRange
            ? `${formatCfm(cfm.start)} → ${formatCfm(cfm.end)}`
            : formatCfm(cfm.start)}
        </dd>

        <dt>Velocity</dt>
        <dd>{Math.round(segment.pressure.velocity_fpm)} FPM</dd>

        <dt>Pressure (start)</dt>
        <dd>{segment.pressure.start_in_wc.toFixed(2)} in. w.c.</dd>

        <dt>Pressure (end)</dt>
        <dd>{segment.pressure.end_in_wc.toFixed(2)} in. w.c.</dd>

        <dt>SMACNA class</dt>
        <dd>
          <span
            className={`v4-smacna-badge pc-${tone}`}
            title={tooltip}
            aria-label={`SMACNA class ${segment.pressure.smacna_class}. ${tooltip}`}
          >
            {segment.pressure.smacna_class}
          </span>
        </dd>
      </dl>

      {segment.terminals_on_segment.length > 0 && (
        <section className="v4-detail-block">
          <h4>Terminals on segment</h4>
          <ul className="v4-detail-terms">
            {segment.terminals_on_segment.map((t) => (
              <li key={t.terminal_id}>
                <span className="mono">{t.terminal_id}</span>
                <span>{formatCfm(t.cfm)} CFM</span>
                <span className="muted">
                  @ {t.distance_along_segment_ft.toFixed(1)} ft
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {warnings.length > 0 && (
        <section className="v4-detail-block">
          <h4>Warnings</h4>
          <ul className="v4-detail-warns">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}

function formatCfm(value: number): string {
  return value.toFixed(0);
}

function smacnaTooltip(t: SmacnaThresholds): string {
  return `SMACNA: ≤${t.low_max_in_wc.toFixed(1)}" w.c. / ${t.low_max_in_wc.toFixed(
    1,
  )}–${t.medium_max_in_wc.toFixed(1)}" w.c. / >${t.medium_max_in_wc.toFixed(
    1,
  )}" w.c. (overridable in Settings)`;
}
