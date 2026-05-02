/**
 * Sidebar per Paper artboard 03 — header (count) → 3 stat columns
 * (BY PC / BY CONF / LINEAR FT) → sort control → list of detections, each
 * with the top reasoning step truncated.
 */

import { useMemo, useState } from "react";
import type { Confidence, DrawingResult, Segment } from "../types/api";

type SortKey = "id" | "dimension" | "pc" | "confidence";

interface Props {
  result: DrawingResult;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function Sidebar({ result, selectedId, onSelect }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("id");

  const sorted = useMemo(
    () => [...result.segments].sort(compareBy(sortKey)),
    [result.segments, sortKey],
  );

  return (
    <aside className="sidebar" aria-label="Detection list and stats">
      <header className="sidebar-header">
        <div className="sidebar-header-row">
          <h3 className="sidebar-title">Detections {result.aggregate.total}</h3>
          <span className="sidebar-meta">
            {result.errors.length === 0
              ? "all stages OK"
              : `${result.errors.length} stage warnings`}
          </span>
        </div>
      </header>

      <div className="sidebar-stats">
        <div>
          <div className="sidebar-stat-label">By PC</div>
          <div className="sidebar-stat-value">
            <span className="pc-dot pc-low" />
            {result.aggregate.by_pressure_class.LOW}
            <span className="pc-dot pc-medium" />
            {result.aggregate.by_pressure_class.MEDIUM}
            <span className="pc-dot pc-high" />
            {result.aggregate.by_pressure_class.HIGH}
          </div>
        </div>
        <div>
          <div className="sidebar-stat-label">By conf.</div>
          <div className="sidebar-stat-value">
            {result.aggregate.by_confidence.high} hi ·{" "}
            {result.aggregate.by_confidence.medium} med ·{" "}
            {result.aggregate.by_confidence.low} lo
          </div>
        </div>
        <div>
          <div className="sidebar-stat-label">Quality</div>
          <div className="sidebar-stat-value">{result.quality.overall}</div>
        </div>
      </div>

      <div className="sidebar-controls">
        <label className="sidebar-stat-label" htmlFor="sort">
          Sort
        </label>
        <select
          id="sort"
          value={sortKey}
          onChange={(e) => setSortKey(e.target.value as SortKey)}
        >
          <option value="id">ID</option>
          <option value="dimension">Dimension</option>
          <option value="pc">Pressure class</option>
          <option value="confidence">Confidence</option>
        </select>
      </div>

      <ul className="sidebar-list">
        {sorted.map((segment) => (
          <SidebarRow
            key={segment.id}
            segment={segment}
            isSelected={segment.id === selectedId}
            onClick={() => onSelect(segment.id)}
          />
        ))}
      </ul>
    </aside>
  );
}

function SidebarRow({
  segment,
  isSelected,
  onClick,
}: {
  segment: Segment;
  isSelected: boolean;
  onClick: () => void;
}) {
  const trace = segment.reasoning_trace[segment.reasoning_trace.length - 1];
  const cssTone = segment.pressure_class.value.toLowerCase();

  return (
    <li>
      <button
        type="button"
        className={`sidebar-row${isSelected ? " is-selected" : ""}`}
        onClick={onClick}
      >
        <div className="sidebar-row-head">
          <span className={`pc-dot pc-${cssTone}`} />
          <span className="mono">{segment.id}</span>
          <span className="mono">{segment.dimension?.value ?? "—"}</span>
          <span className="pc-tag">{segment.pressure_class.value}</span>
          <ConfidenceTag confidence={segment.pressure_class.confidence} />
        </div>
        {trace && (
          <div className="sidebar-row-trace">
            ↳ {trace.stage}: {trace.evidence}
          </div>
        )}
      </button>
    </li>
  );
}

function ConfidenceTag({ confidence }: { confidence: Confidence }) {
  return (
    <span className={`confidence-pill conf-${confidence}`}>{confidence}</span>
  );
}

const PC_RANK: Record<string, number> = { LOW: 0, MEDIUM: 1, HIGH: 2 };
const CONF_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };

function compareBy(key: SortKey): (a: Segment, b: Segment) => number {
  switch (key) {
    case "id":
      return (a, b) => a.id.localeCompare(b.id);
    case "dimension":
      return (a, b) =>
        (a.dimension?.value ?? "").localeCompare(b.dimension?.value ?? "");
    case "pc":
      return (a, b) =>
        PC_RANK[a.pressure_class.value] - PC_RANK[b.pressure_class.value];
    case "confidence":
      return (a, b) =>
        CONF_RANK[a.pressure_class.confidence] -
        CONF_RANK[b.pressure_class.confidence];
  }
}
