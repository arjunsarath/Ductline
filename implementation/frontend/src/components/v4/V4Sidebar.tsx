/**
 * V4 result sidebar shown when nothing is selected. Counts segments by
 * SMACNA class and surfaces warnings grouped by prefix (label / segment /
 * scale). testset2 emits ~180 advisories and a flat list is unusable.
 */

import { useMemo } from "react";
import type { V4Result } from "../../types/v4";

const PREVIEW_PER_GROUP = 25;

interface WarningGroup {
  prefix: string;
  items: string[];
}

function groupWarnings(warnings: string[]): WarningGroup[] {
  const buckets = new Map<string, string[]>();
  for (const w of warnings) {
    const colon = w.indexOf(":");
    const prefix = colon > 0 ? w.slice(0, colon).trim() : "other";
    const list = buckets.get(prefix);
    if (list) list.push(w);
    else buckets.set(prefix, [w]);
  }
  return Array.from(buckets.entries())
    .map(([prefix, items]) => ({ prefix, items }))
    .sort((a, b) => b.items.length - a.items.length);
}

interface Props {
  result: V4Result;
}

export function V4Sidebar({ result }: Props) {
  const counts = useMemo(() => {
    const byClass = { Low: 0, Medium: 0, High: 0 };
    for (const s of result.segments) byClass[s.pressure.smacna_class] += 1;
    return byClass;
  }, [result.segments]);

  const groups = useMemo(() => groupWarnings(result.warnings), [result.warnings]);

  return (
    <aside className="v4-sidebar">
      <div className="v4-sidebar-summary">
        <strong>{result.segments.length}</strong> segments ·{" "}
        <strong>{result.terminals.length}</strong> terminals
        <br />
        <span className="pc-low">Low {counts.Low}</span> ·{" "}
        <span className="pc-medium">Medium {counts.Medium}</span> ·{" "}
        <span className="pc-high">High {counts.High}</span>
      </div>
      <p className="muted">
        Click a segment or terminal on the drawing for details.
      </p>
      {result.warnings.length > 0 && (
        <div className="v4-sidebar-warnings">
          <p className="v4-warn-summary">
            {result.warnings.length} advisories — mostly synthesized round
            labels (A9) and unbounded segments. Click a group to expand.
          </p>
          {groups.map((group) => (
            <details key={group.prefix} className="v4-warn-group">
              <summary>
                <span className="v4-warn-prefix">{group.prefix}</span>
                <span className="v4-warn-count">· {group.items.length}</span>
              </summary>
              <ul>
                {group.items.slice(0, PREVIEW_PER_GROUP).map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
                {group.items.length > PREVIEW_PER_GROUP && (
                  <li className="muted">
                    show all ({group.items.length - PREVIEW_PER_GROUP} more)
                  </li>
                )}
              </ul>
            </details>
          ))}
        </div>
      )}
    </aside>
  );
}
