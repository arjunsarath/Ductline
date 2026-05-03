/**
 * API client. Vite's dev server proxies /api → backend (vite.config.ts).
 *
 * /api/detect now returns a Server-Sent Event stream (PR-D). The client
 * consumes the stream incrementally — `progress` events drive the
 * ProcessingView UI; the terminal `result` event resolves the Promise.
 * Old single-shot JSON behaviour is preserved on /api/detect/blocking
 * for tooling that wants curl-friendly output.
 */

import type { DrawingResult, SampleDrawing } from "../types/api";

/** Approval-gate payload shape — backend pauses, frontend POSTs to release. */
export interface CategorizeApprovalPayload {
  drawing_id: string;
  coord_space: "pdf_points" | "pixels";
  page_size_pt: [number, number] | null;
  raster_probe_size: [number, number] | null;
  raster_probe_data_url: string | null;
  layout: {
    plan_view: [number, number, number, number] | null;
    legend: [number, number, number, number] | null;
    schedule: [number, number, number, number] | null;
    title_block: [number, number, number, number] | null;
    notes: Array<[number, number, number, number]>;
  } | null;
  errors: string[];
}

export interface TilingApprovalPayload {
  drawing_id: string;
  plan_view: [number, number, number, number];
  dpi: number;
  tile_px: number;
  overlap_pct: number;
  tile_count: number;
  tiles: Array<{
    rect: [number, number, number, number];
    row: number;
    col: number;
    total_rows: number;
    total_cols: number;
  }>;
}

/** One pipeline progress event. Names mirror the backend SSE vocabulary. */
export type ProgressEvent =
  | { event: "pipeline_start"; drawing_id: string; filename: string }
  | { event: "stage_start"; stage: string; index: number; total: number }
  | { event: "stage_done"; stage: string; ok: boolean; error?: string }
  | ({ event: "awaiting_categorize_approval" } & CategorizeApprovalPayload)
  | ({ event: "awaiting_tiling_approval" } & TilingApprovalPayload)
  | {
      event: "tile_start";
      stage: "duct_detect_tiled";
      row: number;
      col: number;
      current: number;
      total: number;
    }
  | {
      event: "tile_done";
      stage: "duct_detect_tiled";
      row: number;
      col: number;
      current: number;
      total: number;
      segments_found: number;
    }
  | {
      event: "review_start";
      stage: "review";
      segment_id: string;
      current: number;
      total: number;
    }
  | {
      event: "review_done";
      stage: "review";
      segment_id: string;
      current: number;
      total: number;
      verdict?: string;
      iterations?: number;
      skipped?: string;
      error?: string;
    }
  | { event: "pipeline_done"; drawing_id: string; segments: number; errors: number };

/**
 * POST a drawing and consume the SSE progress stream.
 *
 * @param file        The drawing file (PDF / PNG / JPG).
 * @param onProgress  Called once per `progress` event received from the server.
 *                    Optional — pass `undefined` to ignore progress entirely.
 * @returns Promise that resolves with the final DrawingResult on the
 *          terminal `result` event, or rejects with an Error on the
 *          terminal `error` event / network failure.
 */
export async function detectDrawing(
  file: File,
  onProgress?: (event: ProgressEvent) => void,
): Promise<DrawingResult> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/api/detect", {
    method: "POST",
    body: formData,
    headers: { Accept: "text/event-stream" },
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(`detect failed (${response.status}): ${message}`);
  }
  if (!response.body) {
    throw new Error("detect failed: no response body to stream");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (value) {
      buffer += decoder.decode(value, { stream: true });
    }

    // SSE records are terminated by a blank line (`\n\n`). Process every
    // complete record left in the buffer; any trailing partial record
    // stays in the buffer for the next chunk.
    let separatorIndex = buffer.indexOf("\n\n");
    while (separatorIndex !== -1) {
      const record = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      const parsed = parseSseRecord(record);
      if (parsed) {
        if (parsed.event === "result") {
          // Drain the rest of the stream defensively (the server should
          // close after `result`, but be tolerant).
          await reader.cancel().catch(() => {
            /* ignore */
          });
          const resultPayload = parsed.data as { result: DrawingResult };
          return resultPayload.result;
        }
        if (parsed.event === "error") {
          const errorPayload = parsed.data as { message: string; status: number };
          throw new Error(
            `detect failed (${errorPayload.status}): ${errorPayload.message}`,
          );
        }
        if (parsed.event === "progress" && onProgress) {
          onProgress(parsed.data as ProgressEvent);
        }
      }
      separatorIndex = buffer.indexOf("\n\n");
    }

    if (done) {
      // Stream closed without a terminal `result`/`error` event.
      throw new Error("detect failed: stream ended before result");
    }
  }
}

/** Parse a single SSE record. Returns `null` if the record is malformed. */
function parseSseRecord(
  record: string,
): { event: string; data: unknown } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of record.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
    // Other SSE fields (id:, retry:, comments) ignored — we don't use them.
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

export async function listSamples(): Promise<SampleDrawing[]> {
  const response = await fetch("/api/samples");
  if (!response.ok) throw new Error(`samples failed (${response.status})`);
  return (await response.json()) as SampleDrawing[];
}

/** Release a HITL approval gate. Resolves with the server's ack on 200,
 *  rejects on any non-2xx (including 404 — session already finished). */
export async function approveGate(
  drawingId: string,
  gate: "categorize" | "tiling",
): Promise<void> {
  const response = await fetch(
    `/api/detect/${encodeURIComponent(drawingId)}/approve/${gate}`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
  );
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`approve ${gate} failed (${response.status}): ${body}`);
  }
}

/** Cancel an in-flight detect job. The SSE stream will terminate with an
 *  `error` event (status 499). */
export async function cancelDetection(drawingId: string): Promise<void> {
  const response = await fetch(
    `/api/detect/${encodeURIComponent(drawingId)}/cancel`,
    { method: "POST" },
  );
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`cancel failed (${response.status}): ${body}`);
  }
}

export async function fetchSample(name: string): Promise<File> {
  const response = await fetch(`/api/samples/${encodeURIComponent(name)}`);
  if (!response.ok) throw new Error(`sample fetch failed (${response.status})`);
  const blob = await response.blob();
  return new File([blob], name, { type: blob.type || "application/pdf" });
}
