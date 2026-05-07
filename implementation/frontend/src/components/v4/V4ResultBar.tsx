/**
 * Compact stats + view-control strip shown above the V4 result viewer.
 * Replaces the old wide right-hand sidebar — the post-pipeline summary
 * (counts, total length, total CFM, class breakdown) is small enough to
 * fit inline, freeing the canvas to be the focal point.
 */

import type { DebugOcrMatch, SmacnaClass } from "../../types/v4";

interface Props {
  matches: DebugOcrMatch[];
  backgroundOpacity: number;
  onBackgroundOpacityChange: (next: number) => void;
  shadeByPressure: boolean;
  onShadeByPressureChange: (next: boolean) => void;
}

interface DerivedStats {
  ducts: number;
  terminals: number;
  totalLengthFt: number;
  totalCfm: number;
  classCounts: Record<SmacnaClass, number>;
  estimatedDucts: number;
}

function deriveStats(matches: DebugOcrMatch[]): DerivedStats {
  let ducts = 0;
  let terminals = 0;
  let totalLengthFt = 0;
  let totalCfm = 0;
  const classCounts: Record<SmacnaClass, number> = {
    Low: 0, Medium: 0, High: 0,
  };
  let estimatedDucts = 0;
  for (const m of matches) {
    const isDuct = typeof m.length_ft === "number" && m.length_ft > 0;
    const isTerminal = !isDuct && (m.text?.match(/\b\d{3}\b/) !== null);
    if (isDuct) {
      ducts += 1;
      totalLengthFt += m.length_ft ?? 0;
      if (m.smacna_class) classCounts[m.smacna_class] += 1;
      if (m.pressure_estimated) estimatedDucts += 1;
      if (typeof m.cfm === "number") totalCfm += m.cfm;
    } else if (isTerminal) {
      terminals += 1;
      const cfmFromText = Number(m.text.match(/\b(\d{3})\b/)?.[1] ?? 0);
      if (cfmFromText > 0) totalCfm += cfmFromText;
    }
  }
  return {
    ducts, terminals,
    totalLengthFt: Math.round(totalLengthFt),
    totalCfm: Math.round(totalCfm),
    classCounts, estimatedDucts,
  };
}

export function V4ResultBar({
  matches,
  backgroundOpacity,
  onBackgroundOpacityChange,
  shadeByPressure,
  onShadeByPressureChange,
}: Props) {
  const stats = deriveStats(matches);
  return (
    <div className="v4-result-bar">
      <div className="v4-result-bar__stats">
        <Stat label="Ducts" value={String(stats.ducts)} />
        <Stat label="Terminals" value={String(stats.terminals)} />
        <Stat label="Total length" value={`${stats.totalLengthFt} ft`} />
        <Stat label="Total CFM" value={String(stats.totalCfm)} />
        <div className="v4-result-bar__classes">
          <span className="v4-result-bar__cls v4-cls-low">
            <span /> {stats.classCounts.Low} Low
          </span>
          <span className="v4-result-bar__cls v4-cls-med">
            <span /> {stats.classCounts.Medium} Med
          </span>
          <span className="v4-result-bar__cls v4-cls-high">
            <span /> {stats.classCounts.High} High
          </span>
        </div>
        {stats.estimatedDucts > 0 && (
          <span className="v4-result-bar__note">
            {stats.estimatedDucts} duct{stats.estimatedDucts === 1 ? "" : "s"} use fallback velocity
          </span>
        )}
      </div>
      <div className="v4-result-bar__controls">
        <label className="v4-result-bar__check">
          <input
            type="checkbox"
            checked={shadeByPressure}
            onChange={(e) => onShadeByPressureChange(e.target.checked)}
          />
          Shade by pressure class
        </label>
        <label className="v4-result-bar__slider">
          <span>Background</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={backgroundOpacity}
            onChange={(e) =>
              onBackgroundOpacityChange(Number(e.target.value))
            }
            aria-label="Background image opacity"
          />
          <span className="v4-result-bar__pct">
            {Math.round(backgroundOpacity * 100)}%
          </span>
        </label>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="v4-result-bar__stat">
      <span className="v4-result-bar__stat-label">{label}</span>
      <span className="v4-result-bar__stat-value">{value}</span>
    </div>
  );
}
