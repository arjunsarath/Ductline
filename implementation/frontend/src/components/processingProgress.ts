/**
 * ProgressState reducer for the streaming /api/detect endpoint (PR-D).
 *
 * The backend pipeline emits a small vocabulary of progress events as it
 * runs (stage_start / stage_done, plus tile_* and review_* sub-events for
 * the long-running stages). This module folds the event stream into a
 * single denormalised state object the ProcessingView can render.
 *
 * Lives in its own .ts module so React Fast Refresh stays happy — non-
 * component exports must not share a .tsx file with components.
 */

import type { ProgressEvent } from "../api/client";

/** v2 pipeline stage list, in execution order. Mirrors runner.py. */
export const STAGE_ORDER = [
  "ingest",
  "probe_ocr",
  "page_categorize",
  "legend_parse",
  "quality",
  "regions",
  "duct_detect_tiled",
  "text_extract",
  "pressure_class",
  "review",
] as const;

export type StageName = (typeof STAGE_ORDER)[number];

export type StageStatus = "pending" | "active" | "done" | "failed";

export interface StageInfo {
  name: StageName;
  status: StageStatus;
  /** Wall-clock seconds spent on this stage (set on stage_done). */
  durationSec: number | null;
  /** Stage-emitted error string (when status === "failed"). */
  error: string | null;
  /** Sub-progress for stages that emit tile_* or review_* events. */
  subProgress: { current: number; total: number; label: string } | null;
}

export interface ProgressState {
  /** Wall-clock seconds since the pipeline_start event arrived. Null until then. */
  startedAtMs: number | null;
  /** True once pipeline_done arrives; ProcessingView uses this for the final
   *  collapsing animation. */
  completed: boolean;
  /** Per-stage status. Keyed by STAGE_ORDER. */
  stages: Record<StageName, StageInfo>;
  /** Most recent progress event received — for "what's happening right now"
   *  status text in the view. */
  lastEvent: ProgressEvent | null;
}

const STAGE_LABEL: Record<StageName, string> = {
  ingest: "Ingest",
  probe_ocr: "Probe OCR",
  page_categorize: "Page categorizer",
  legend_parse: "Legend parser",
  quality: "Quality check",
  regions: "Region detect (v1)",
  duct_detect_tiled: "Tiled duct detection",
  text_extract: "Text extraction",
  pressure_class: "Pressure class",
  review: "MEP reviewer",
};

export function stageLabel(name: StageName): string {
  return STAGE_LABEL[name];
}

export function initialProgressState(): ProgressState {
  const stages = Object.fromEntries(
    STAGE_ORDER.map((name) => [
      name,
      {
        name,
        status: "pending" as StageStatus,
        durationSec: null,
        error: null,
        subProgress: null,
      } satisfies StageInfo,
    ]),
  ) as Record<StageName, StageInfo>;
  return { startedAtMs: null, completed: false, stages, lastEvent: null };
}

const STAGE_START_TIMES = new WeakMap<ProgressState, Map<StageName, number>>();

function getStartTimes(state: ProgressState): Map<StageName, number> {
  let map = STAGE_START_TIMES.get(state);
  if (!map) {
    map = new Map();
    STAGE_START_TIMES.set(state, map);
  }
  return map;
}

/**
 * Apply a single progress event to the state. Returns a new state object
 * (immutable update) so React change-detection works as expected.
 */
export function applyProgressEvent(
  state: ProgressState,
  event: ProgressEvent,
): ProgressState {
  const next: ProgressState = {
    ...state,
    stages: { ...state.stages },
    lastEvent: event,
  };
  // Inherit start-time map across copies so durations carry forward.
  const startTimes = getStartTimes(state);
  STAGE_START_TIMES.set(next, startTimes);

  switch (event.event) {
    case "pipeline_start":
      next.startedAtMs = performance.now();
      return next;

    case "stage_start": {
      const name = event.stage as StageName;
      if (!(name in next.stages)) return next;
      startTimes.set(name, performance.now());
      next.stages[name] = {
        ...next.stages[name],
        status: "active",
        error: null,
      };
      return next;
    }

    case "stage_done": {
      const name = event.stage as StageName;
      if (!(name in next.stages)) return next;
      const startedAt = startTimes.get(name);
      const durationSec =
        startedAt != null ? (performance.now() - startedAt) / 1000 : null;
      next.stages[name] = {
        ...next.stages[name],
        status: event.ok === false ? "failed" : "done",
        durationSec,
        error: event.error ?? null,
        subProgress: null,
      };
      return next;
    }

    case "tile_start":
    case "tile_done": {
      // Sub-progress: "tile current/total". We update the duct_detect_tiled
      // entry's subProgress; tile_done refreshes the same numbers (the model
      // may have emitted segments_found we don't surface here).
      const stage = next.stages.duct_detect_tiled;
      const totalTiles = (event as { total?: number }).total ?? 0;
      const current = (event as { current?: number }).current ?? 0;
      const totalSegmentsSoFar =
        event.event === "tile_done" && typeof event.segments_found === "number"
          ? event.segments_found
          : null;
      next.stages.duct_detect_tiled = {
        ...stage,
        subProgress: {
          current,
          total: totalTiles,
          label:
            totalSegmentsSoFar != null
              ? `tile ${current}/${totalTiles} (+${totalSegmentsSoFar})`
              : `tile ${current}/${totalTiles}`,
        },
      };
      return next;
    }

    case "review_start":
    case "review_done": {
      const stage = next.stages.review;
      const total = (event as { total?: number }).total ?? 0;
      const current = (event as { current?: number }).current ?? 0;
      const verdict =
        event.event === "review_done"
          ? (event as { verdict?: string }).verdict ?? null
          : null;
      next.stages.review = {
        ...stage,
        subProgress: {
          current,
          total,
          label: verdict
            ? `segment ${current}/${total} — ${verdict}`
            : `segment ${current}/${total}`,
        },
      };
      return next;
    }

    case "pipeline_done":
      next.completed = true;
      return next;

    default:
      return next;
  }
}

/** Total elapsed seconds since pipeline_start, given a "now" in ms. */
export function elapsedSeconds(state: ProgressState, nowMs: number): number {
  if (state.startedAtMs == null) return 0;
  return Math.max(0, (nowMs - state.startedAtMs) / 1000);
}
