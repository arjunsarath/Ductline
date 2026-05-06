/**
 * V4 API client. POST multipart to /api/v4/sessions (Vite dev proxy strips
 * /api → backend /v4). Settings recompute = re-upload with new op_vars.
 *
 * Streaming variant uses /v4/sessions/stream which emits NDJSON: one JSON
 * object per line. Stage events arrive as {stage, message, elapsed_ms}; the
 * terminal event is {stage:"done", result:V4Result} or {stage:"error", message}.
 */

import type { OperationalVars, V4ProgressEvent, V4Result } from "../types/v4";

export interface FilterToggles {
  oversized: boolean;
  aspectRatio: boolean;
  interior: boolean;
  content: boolean;
  rectangle: boolean;
}

export interface CropArea {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface RunV4Options {
  opVars?: OperationalVars;
  sourceNodeId?: string;
  debug?: boolean;
  minAspectRatio?: number;
  minWhitePct?: number;
  epsilonFrac?: number;
  maxCornerCos?: number;
  toggles?: FilterToggles;
  cropArea?: CropArea | null;
  stopAfter?: "grey_removal";
  dpi?: number;
  enableVlmOcr?: boolean;
  maxVlmCrops?: number;
  inkThreshold?: number;
  rectDpi?: number;
  ocrDpi?: number;
  enableMinInk?: boolean;
  minInkPct?: number;
  enableMaxInk?: boolean;
  maxInkPct?: number;
  enableSquarish?: boolean;
  minDuctAspect?: number;
}

function buildForm(file: File, options: RunV4Options): FormData {
  const form = new FormData();
  form.append("file", file);
  if (options.opVars) {
    form.append("op_vars", JSON.stringify(options.opVars));
  }
  if (options.sourceNodeId) {
    form.append("source_node_id", options.sourceNodeId);
  }
  if (options.debug) {
    form.append("debug", "true");
  }
  if (options.minAspectRatio !== undefined) {
    form.append("min_aspect_ratio", String(options.minAspectRatio));
  }
  if (options.minWhitePct !== undefined) {
    form.append("min_white_pct", String(options.minWhitePct));
  }
  if (options.toggles) {
    form.append("enable_oversized", String(options.toggles.oversized));
    form.append("enable_aspect_ratio", String(options.toggles.aspectRatio));
    form.append("enable_interior", String(options.toggles.interior));
    form.append("enable_content", String(options.toggles.content));
    form.append("enable_rectangle", String(options.toggles.rectangle));
  }
  if (options.epsilonFrac !== undefined) {
    form.append("epsilon_frac", String(options.epsilonFrac));
  }
  if (options.maxCornerCos !== undefined) {
    form.append("max_corner_cos", String(options.maxCornerCos));
  }
  if (options.cropArea) {
    form.append("crop_x", String(options.cropArea.x));
    form.append("crop_y", String(options.cropArea.y));
    form.append("crop_w", String(options.cropArea.w));
    form.append("crop_h", String(options.cropArea.h));
  }
  if (options.stopAfter) {
    form.append("stop_after", options.stopAfter);
  }
  if (options.dpi !== undefined) {
    form.append("dpi", String(options.dpi));
  }
  if (options.enableVlmOcr) {
    form.append("enable_vlm_ocr", "true");
  }
  if (options.maxVlmCrops !== undefined) {
    form.append("max_vlm_crops", String(options.maxVlmCrops));
  }
  if (options.inkThreshold !== undefined) {
    form.append("ink_threshold", String(options.inkThreshold));
  }
  if (options.rectDpi !== undefined) {
    form.append("rect_dpi", String(options.rectDpi));
  }
  if (options.ocrDpi !== undefined) {
    form.append("ocr_dpi", String(options.ocrDpi));
  }
  if (options.enableMinInk !== undefined) {
    form.append("enable_min_ink", String(options.enableMinInk));
  }
  if (options.minInkPct !== undefined) {
    form.append("min_ink_pct", String(options.minInkPct));
  }
  if (options.enableMaxInk !== undefined) {
    form.append("enable_max_ink", String(options.enableMaxInk));
  }
  if (options.maxInkPct !== undefined) {
    form.append("max_ink_pct", String(options.maxInkPct));
  }
  if (options.enableSquarish !== undefined) {
    form.append("enable_squarish", String(options.enableSquarish));
  }
  if (options.minDuctAspect !== undefined) {
    form.append("min_duct_aspect", String(options.minDuctAspect));
  }
  return form;
}

export async function runV4Session(
  file: File,
  options: RunV4Options = {},
): Promise<V4Result> {
  const response = await fetch("/api/v4/sessions", {
    method: "POST",
    body: buildForm(file, options),
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(`v4 session failed (${response.status}): ${message}`);
  }
  return (await response.json()) as V4Result;
}

interface DoneEvent {
  stage: "done";
  message: string;
  result: V4Result;
}
interface StreamErrorEvent {
  stage: "error";
  message: string;
}
type StreamEvent = V4ProgressEvent | DoneEvent | StreamErrorEvent;

function isDone(e: StreamEvent): e is DoneEvent {
  return e.stage === "done";
}
function isStreamError(e: StreamEvent): e is StreamErrorEvent {
  return e.stage === "error";
}

export async function runV4SessionStreaming(
  file: File,
  options: RunV4Options,
  onEvent: (event: V4ProgressEvent) => void,
): Promise<V4Result> {
  const response = await fetch("/api/v4/sessions/stream", {
    method: "POST",
    body: buildForm(file, options),
  });
  if (!response.ok || !response.body) {
    const message = !response.body ? "no response body" : await response.text();
    throw new Error(`v4 stream failed (${response.status}): ${message}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let newlineAt = buffer.indexOf("\n");
    while (newlineAt >= 0) {
      const line = buffer.slice(0, newlineAt).trim();
      buffer = buffer.slice(newlineAt + 1);
      newlineAt = buffer.indexOf("\n");
      if (!line) continue;
      const parsed = JSON.parse(line) as StreamEvent;
      if (isStreamError(parsed)) throw new Error(parsed.message);
      if (isDone(parsed)) return parsed.result;
      onEvent(parsed);
    }
  }

  const tail = buffer.trim();
  if (tail) {
    const parsed = JSON.parse(tail) as StreamEvent;
    if (isStreamError(parsed)) throw new Error(parsed.message);
    if (isDone(parsed)) return parsed.result;
  }
  throw new Error("v4 stream ended without a 'done' event");
}
