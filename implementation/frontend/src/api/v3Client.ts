/**
 * V3 API client. Calls /api/v3/* (Vite dev proxy strips /api → backend /v3).
 *
 * Two endpoints:
 *   • renderPage(file)        → page rendered at adaptive DPI for the picker
 *   • detect(file, picks)     → run pipeline, return result + overlay PNG
 */

import type {
  PickPayload,
  V3DetectResponse,
  V3RenderResponse,
} from "../types/v3";

export async function renderPage(file: File): Promise<V3RenderResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("target_text_height_px", "24");
  form.append("min_dpi", "200");
  form.append("max_dpi", "600");
  const response = await fetch("/api/v3/render", { method: "POST", body: form });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(`render failed (${response.status}): ${message}`);
  }
  return (await response.json()) as V3RenderResponse;
}

export async function detect(
  file: File,
  picks: PickPayload[],
): Promise<V3DetectResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("picks_json", JSON.stringify(picks));
  const response = await fetch("/api/v3/detect", { method: "POST", body: form });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(`detect failed (${response.status}): ${message}`);
  }
  return (await response.json()) as V3DetectResponse;
}

export interface V3SampleEntry {
  name: string;
  size_bytes: number;
}

export async function listSamples(): Promise<V3SampleEntry[]> {
  const response = await fetch("/api/v3/samples");
  if (!response.ok) return [];
  return (await response.json()) as V3SampleEntry[];
}

export async function fetchSample(name: string): Promise<File> {
  const response = await fetch(`/api/v3/samples/${encodeURIComponent(name)}`);
  if (!response.ok) throw new Error(`sample fetch failed (${response.status})`);
  const blob = await response.blob();
  return new File([blob], name, { type: blob.type || "application/pdf" });
}
