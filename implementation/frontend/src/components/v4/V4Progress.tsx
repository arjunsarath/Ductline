/**
 * Streaming progress card for the V4 pipeline. Shows the current stage,
 * elapsed wall-clock, and a checked-off list of completed stages so the user
 * has live feedback during a 30-90s run.
 */

import type { V4ProgressEvent } from "../../types/v4";

const STAGE_LABELS: Record<string, string> = {
  rasterize: "Rasterizing PDF",
  grey_removal: "Removing background fill",
  scale: "Resolving drawing scale",
  detect_ducts: "Detecting duct polygons",
  detect_boundaries: "Detecting segment boundaries",
  detect_connectors: "Detecting connectors",
  detect_terminals: "Detecting air terminals",
  detect_crossings: "Resolving crossings",
  ocr_labels: "Reading duct labels (OCR)",
  build_network: "Building duct network",
  flow_trace: "Tracing CFM",
  pressure: "Computing pressure",
};

const STAGE_ORDER = Object.keys(STAGE_LABELS);

interface Props {
  events: V4ProgressEvent[];
}

function formatElapsed(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function V4Progress({ events }: Props) {
  const last = events[events.length - 1];
  const completedStages = new Set(events.map((e) => e.stage));
  const currentStage = last?.stage ?? "rasterize";
  const currentLabel = STAGE_LABELS[currentStage] ?? currentStage;
  const elapsed = last ? formatElapsed(last.elapsed_ms) : "0:00";

  return (
    <div className="v4-progress" role="status" aria-live="polite">
      <div className="v4-progress-head">
        <span className="v4-progress-spinner" aria-hidden="true" />
        <strong>{currentLabel}</strong>
        <span className="v4-progress-elapsed">{elapsed}</span>
      </div>
      <ol className="v4-progress-list">
        {STAGE_ORDER.map((stage) => {
          const done = completedStages.has(stage) && stage !== currentStage;
          const active = stage === currentStage;
          return (
            <li
              key={stage}
              className={
                "v4-progress-step" +
                (done ? " is-done" : "") +
                (active ? " is-active" : "")
              }
            >
              <span className="v4-progress-mark" aria-hidden="true">
                {done ? "✓" : active ? "•" : ""}
              </span>
              {STAGE_LABELS[stage]}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
