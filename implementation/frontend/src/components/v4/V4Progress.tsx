/**
 * Streaming progress card for the V4.5 pipeline. Top: a big mono timer + the
 * current "Stage X of N" headline. Below: the seven-row Detection Pipeline
 * with status indicators, ALG/AGT badges, live sub-status, and per-stage
 * durations derived from the SSE event timestamps.
 */

import type { V4ProgressEvent } from "../../types/v4";

type StageBadge = "ALG" | "AGT";

interface StageDef {
  id: string;
  label: string;
  badge: StageBadge;
  description: string;
  events: string[];
}

const STAGES: StageDef[] = [
  {
    id: "ingest",
    label: "Ingest",
    badge: "ALG",
    description: "Rasterize PDF · binarise · mask drawing area",
    events: ["rasterize", "grey_removal", "mask_outside"],
  },
  {
    id: "contours",
    label: "Find contours",
    badge: "ALG",
    description: "findContours + drop oversized page-frame artifacts",
    events: ["find_rectangles", "filter_oversized"],
  },
  {
    id: "filter_ducts",
    label: "Filter ducts",
    badge: "ALG",
    description: "rectangle · squarish · ink density · aspect chain",
    events: [
      "filter_rectangle", "filter_squarish",
      "filter_min_ink", "filter_max_ink",
      "filter_aspect_ratio", "filter_interior", "filter_content",
    ],
  },
  {
    id: "filter_terminals",
    label: "Filter terminals",
    badge: "ALG",
    description: "circularity ≥ 0.69 · horizontal divider check",
    events: ["filter_circle", "filter_divider"],
  },
  {
    id: "read_cfm",
    label: "Read terminal CFM",
    badge: "AGT",
    description: "Tesseract @600 → VLM @600/900/1200 ladder",
    events: ["filter_three_digit", "filter_three_digit_progress"],
  },
  {
    id: "read_dims",
    label: "Read duct labels",
    badge: "AGT",
    description: "rect-grammar VLM ladder · cached by image hash",
    events: ["ocr_per_crop", "ocr_per_crop_progress", "crops_only"],
  },
  {
    id: "compute",
    label: "Compute length & pressure",
    badge: "ALG",
    description: "median-scale length · Darcy ΔP · SMACNA class",
    events: ["done"],
  },
];

interface Props {
  events: V4ProgressEvent[];
}

function formatTimer(ms: number): string {
  const totalSeconds = ms / 1000;
  const m = Math.floor(totalSeconds / 60);
  const s = (totalSeconds % 60).toFixed(1).padStart(4, "0");
  return `${m.toString().padStart(2, "0")}:${s}`;
}

interface StageState {
  status: "pending" | "active" | "completed";
  startMs: number | null;
  durationMs: number;
  subStatus: string;
  progress: { done: number; total: number; kept?: number } | null;
}

function computeStageStates(
  events: V4ProgressEvent[],
): StageState[] {
  const stageOf = new Map<string, number>();
  STAGES.forEach((s, i) => s.events.forEach((e) => stageOf.set(e, i)));
  const stageEvents: V4ProgressEvent[][] = STAGES.map(() => []);
  for (const ev of events) {
    const idx = stageOf.get(ev.stage);
    if (idx !== undefined) stageEvents[idx].push(ev);
  }
  const lastEv = events[events.length - 1];
  const lastIdx = lastEv ? stageOf.get(lastEv.stage) ?? -1 : -1;
  return STAGES.map((stage, i) => {
    const evs = stageEvents[i];
    if (evs.length === 0) {
      return {
        status: "pending",
        startMs: null,
        durationMs: 0,
        subStatus: stage.description,
        progress: null,
      };
    }
    const startMs = evs[0].elapsed_ms;
    // End of this stage = start of the *next* stage that has any events.
    let endMs: number | null = null;
    for (let j = i + 1; j < STAGES.length; j++) {
      const next = stageEvents[j];
      if (next.length > 0) {
        endMs = next[0].elapsed_ms;
        break;
      }
    }
    const isLastTouched = i === lastIdx;
    const status: StageState["status"] =
      endMs !== null
        ? "completed"
        : isLastTouched
        ? "active"
        : "completed";
    const durationMs = endMs !== null ? endMs - startMs : (lastEv?.elapsed_ms ?? startMs) - startMs;
    const latest = evs[evs.length - 1];
    const progress =
      typeof latest.total === "number" && typeof latest.done === "number"
        ? { done: latest.done, total: latest.total, kept: latest.kept }
        : null;
    const subStatus = progress
      ? `${progress.done}/${progress.total} processed${typeof progress.kept === "number" ? ` · ${progress.kept} kept` : ""}`
      : latest.message || stage.description;
    return { status, startMs, durationMs, subStatus, progress };
  });
}

export function V4Progress({ events }: Props) {
  const totalElapsed = events[events.length - 1]?.elapsed_ms ?? 0;
  const stageStates = computeStageStates(events);
  const activeIdx = stageStates.findIndex((s) => s.status === "active");
  const headlineIdx = activeIdx >= 0 ? activeIdx : Math.max(0, stageStates.length - 1);
  const headlineStage = STAGES[headlineIdx];
  const headlineState = stageStates[headlineIdx];
  return (
    <div className="v4-progress" role="status" aria-live="polite">
      <div className="v4-progress__hero">
        <div className="v4-progress__step">STEP 02 / PROCESSING</div>
        <div className="v4-progress__timer">{formatTimer(totalElapsed)}</div>
        <div className="v4-progress__stage-line">
          Stage {headlineIdx + 1} of {STAGES.length} — {headlineStage.label.toLowerCase()}
          {headlineState?.progress
            ? ` (${headlineState.progress.done}/${headlineState.progress.total})`
            : ""}
        </div>
      </div>

      <div className="v4-progress__pipeline">
        <header className="v4-progress__pipeline-head">
          <span className="v4-progress__pipeline-title">DETECTION PIPELINE</span>
          <span className="v4-progress__legend">
            <span className="v4-badge alg">ALG</span> algorithmic
            <span className="v4-badge agt">AGT</span> agent
          </span>
        </header>
        <ol className="v4-progress__list">
          {STAGES.map((stage, i) => {
            const s = stageStates[i];
            const num = String(i + 1).padStart(2, "0");
            const cls =
              "v4-progress__row"
              + (s.status === "completed" ? " is-done" : "")
              + (s.status === "active" ? " is-active" : "")
              + (s.status === "pending" ? " is-pending" : "");
            return (
              <li key={stage.id} className={cls}>
                <span className="v4-progress__mark" aria-hidden="true">
                  {s.status === "completed"
                    ? "✓"
                    : s.status === "active"
                    ? <span className="v4-progress__spinner" />
                    : ""}
                </span>
                <span className="v4-progress__num">{num}</span>
                <span className="v4-progress__name">{stage.label}</span>
                <span className={`v4-badge ${stage.badge.toLowerCase()}`}>
                  {stage.badge}
                </span>
                <span className="v4-progress__desc">
                  {s.subStatus}
                  {s.progress && s.status === "active" && (
                    <span className="v4-progress__bar">
                      <span
                        className="v4-progress__bar-fill"
                        style={{
                          width: `${Math.min(
                            100,
                            (s.progress.done / Math.max(1, s.progress.total)) * 100,
                          )}%`,
                        }}
                      />
                    </span>
                  )}
                </span>
                <span className="v4-progress__time">
                  {s.status === "pending"
                    ? ""
                    : `${(s.durationMs / 1000).toFixed(1)} s`}
                </span>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
