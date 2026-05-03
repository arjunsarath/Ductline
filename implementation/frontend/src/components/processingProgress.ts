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

import type {
  CategorizeApprovalPayload,
  ProgressEvent,
  TilingApprovalPayload,
} from "../api/client";

/** v2 pipeline stage list, in execution order.
 *
 * Names mirror the backend stage `.name` attributes EXACTLY (see
 * implementation/backend/app/pipeline/*.py — `region_detect` not `regions`,
 * `text_extraction` not `text_extract`). A mismatch silently drops events:
 * the UI keeps showing pending while the backend completes the stage.
 */
export const STAGE_ORDER = [
  "ingest",
  "probe_ocr",
  "page_categorize",
  "legend_parse",
  "quality",
  "region_detect",
  "duct_detect_tiled",
  "text_extraction",
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

/** Currently-pending tile (the next or in-flight VLM call). Cleared on
 *  tile_done so the preview doesn't lag behind the actual run. */
export interface ActiveTile {
  row: number;
  col: number;
  current: number;
  total: number;
  /** Set by tile_done after the call completes. Null while in-flight. */
  segmentsFound: number | null;
}

export interface ProgressState {
  /** Drawing ID assigned by the backend; needed for approve/cancel POSTs. */
  drawingId: string | null;
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
  /** Open approval gate, if any. Set on awaiting_*_approval; cleared when the
   *  next stage_start fires (the gate has been released). */
  awaitingGate:
    | { gate: "categorize"; payload: CategorizeApprovalPayload }
    | { gate: "tiling"; payload: TilingApprovalPayload }
    | null;
  /** Latest tile being processed (for the 100% preview panel). */
  activeTile: ActiveTile | null;
}

const STAGE_LABEL: Record<StageName, string> = {
  ingest: "Ingest",
  probe_ocr: "Probe OCR",
  page_categorize: "Page categorizer",
  legend_parse: "Legend parser",
  quality: "Quality check",
  region_detect: "Region detect (v1)",
  duct_detect_tiled: "Tiled duct detection",
  text_extraction: "Text extraction",
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
  return {
    drawingId: null,
    startedAtMs: null,
    completed: false,
    stages,
    lastEvent: null,
    awaitingGate: null,
    activeTile: null,
  };
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
      next.drawingId = event.drawing_id;
      next.startedAtMs = performance.now();
      return next;

    case "stage_start": {
      const name = event.stage as StageName;
      // Releasing a gate is observable as the next stage starting — clear
      // any open gate so the UI dismisses the approval panel.
      next.awaitingGate = null;
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
      // tile_start is the unambiguous "we're past the tiling gate" signal.
      // duct_detect_tiled is one stage that wraps the entire ~4-min tile
      // loop, so no stage_start fires between approve and the first VLM
      // call — without this the approval panel hangs visible until the
      // run completes. (V2 §5.8 follow-up.)
      if (event.event === "tile_start") {
        next.awaitingGate = null;
      }
      const stage = next.stages.duct_detect_tiled;
      const totalTiles = (event as { total?: number }).total ?? 0;
      const current = (event as { current?: number }).current ?? 0;
      const segmentsFound =
        event.event === "tile_done" && typeof event.segments_found === "number"
          ? event.segments_found
          : null;
      next.stages.duct_detect_tiled = {
        ...stage,
        subProgress: {
          current,
          total: totalTiles,
          label:
            segmentsFound != null
              ? `tile ${current}/${totalTiles} (+${segmentsFound})`
              : `tile ${current}/${totalTiles}`,
        },
      };
      // Active tile: keep the latest row/col and segmentsFound. tile_start
      // resets segmentsFound to null (in-flight); tile_done sets it to the
      // count returned by the model. Used by the 100% tile preview panel.
      next.activeTile = {
        row: (event as { row: number }).row,
        col: (event as { col: number }).col,
        current,
        total: totalTiles,
        segmentsFound,
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
      next.awaitingGate = null;
      next.activeTile = null;
      return next;

    case "awaiting_categorize_approval":
      next.awaitingGate = { gate: "categorize", payload: event };
      return next;

    case "awaiting_tiling_approval":
      next.awaitingGate = { gate: "tiling", payload: event };
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
