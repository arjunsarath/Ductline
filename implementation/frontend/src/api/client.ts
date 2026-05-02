/**
 * API client. Vite's dev server proxies /api → backend (vite.config.ts).
 */

import type { DrawingResult, SampleDrawing } from "../types/api";

export async function detectDrawing(file: File): Promise<DrawingResult> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/api/detect", { method: "POST", body: formData });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(`detect failed (${response.status}): ${message}`);
  }
  return (await response.json()) as DrawingResult;
}

export async function listSamples(): Promise<SampleDrawing[]> {
  const response = await fetch("/api/samples");
  if (!response.ok) throw new Error(`samples failed (${response.status})`);
  return (await response.json()) as SampleDrawing[];
}

export async function fetchSample(name: string): Promise<File> {
  const response = await fetch(`/api/samples/${encodeURIComponent(name)}`);
  if (!response.ok) throw new Error(`sample fetch failed (${response.status})`);
  const blob = await response.blob();
  return new File([blob], name, { type: blob.type || "application/pdf" });
}
