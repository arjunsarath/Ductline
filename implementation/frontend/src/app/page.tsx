"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import UploadScreen from "@/components/upload-screen";
import PipelineProgress, {
  type PipelineState,
} from "@/components/pipeline-progress";
import type {
  CropRegion,
  ExtractResponse,
  ScaleResponse,
} from "@/lib/extract";

// react-pdf must not run during SSR — pdfjs touches DOM/worker globals at module load.
const Viewer = dynamic(() => import("@/components/viewer"), {
  ssr: false,
  loading: () => (
    <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
      Loading viewer…
    </div>
  ),
});

const Cropper = dynamic(() => import("@/components/cropper"), {
  ssr: false,
  loading: () => (
    <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
      Loading cropper…
    </div>
  ),
});

const EXTRACT_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/extract";
const SCALE_URL =
  process.env.NEXT_PUBLIC_SCALE_API_URL ??
  "http://localhost:8000/api/detect-scale";
// Fixed black-ink threshold: the system needs the stripped view internally
// but the user no longer sees it, so there's nothing to tune.
const BLACK_THRESHOLD = 0.02;

type PipelineStepState = PipelineState;

type State =
  | { step: "upload"; file: File | null }
  | { step: "crop"; file: File; pdfUrl: string }
  | {
      step: "pipeline";
      file: File;
      pdfUrl: string;
      regions: CropRegion[];
      progress: PipelineStepState;
      error: string | null;
    }
  | {
      step: "viewer";
      file: File;
      pdfUrl: string;
      data: ExtractResponse;
      regions: CropRegion[];
      scaleByPage: Record<number, ScaleResponse>;
    };

const INITIAL_PIPELINE: PipelineStepState = {
  extract: "idle",
  scale: "idle",
  cleanup: "idle",
};

export default function Page() {
  const [state, setState] = useState<State>({ step: "upload", file: null });
  // Tracks the latest pipeline run; an in-flight run from an earlier crop is
  // abandoned (its state writes ignored) if the user re-crops mid-flight.
  const runIdRef = useRef(0);

  const onContinue = useCallback((file: File) => {
    setState((prev) => {
      if (prev.step !== "upload" && prev.pdfUrl) URL.revokeObjectURL(prev.pdfUrl);
      return { step: "crop", file, pdfUrl: URL.createObjectURL(file) };
    });
  }, []);

  const onReset = useCallback(() => {
    setState((prev) => {
      if (prev.step !== "upload" && prev.pdfUrl) URL.revokeObjectURL(prev.pdfUrl);
      return { step: "upload", file: null };
    });
  }, []);

  const onBackToUpload = useCallback(() => {
    setState((prev) => {
      if (prev.step === "upload") return prev;
      return { step: "upload", file: prev.file };
    });
  }, []);

  const onBackToCrop = useCallback(() => {
    setState((prev) => {
      if (prev.step !== "pipeline") return prev;
      return { step: "crop", file: prev.file, pdfUrl: prev.pdfUrl };
    });
  }, []);

  const runPipeline = useCallback(async (regions: CropRegion[]) => {
    if (state.step !== "crop") return;
    const runId = ++runIdRef.current;
    const { file, pdfUrl } = state;

    setState({
      step: "pipeline",
      file,
      pdfUrl,
      regions,
      progress: { ...INITIAL_PIPELINE, extract: "running" },
      error: null,
    });

    const fail = (msg: string, atStep: keyof PipelineStepState) => {
      if (runIdRef.current !== runId) return;
      setState((prev) => {
        if (prev.step !== "pipeline") return prev;
        return {
          ...prev,
          progress: { ...prev.progress, [atStep]: prev.progress[atStep] },
          error: msg,
        };
      });
    };

    // 1. Extract
    let data: ExtractResponse;
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("crop", JSON.stringify(regions));
      const res = await fetch(EXTRACT_URL, { method: "POST", body: form });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new Error(
          `Extract failed (${res.status})${body ? `: ${body.slice(0, 200)}` : ""}`,
        );
      }
      data = (await res.json()) as ExtractResponse;
      if (data.pages.length === 0) {
        throw new Error("No elements found in the selected regions.");
      }
    } catch (e) {
      fail(e instanceof Error ? e.message : "Extraction failed.", "extract");
      return;
    }
    if (runIdRef.current !== runId) return;
    setState((prev) =>
      prev.step !== "pipeline"
        ? prev
        : { ...prev, progress: { ...prev.progress, extract: "done", scale: "running" } },
    );

    // 2. Scale detection — one call per crop region, run in parallel.
    const scaleByPage: Record<number, ScaleResponse> = {};
    try {
      const results = await Promise.all(
        regions.map(async (region) => {
          const form = new FormData();
          form.append("file", file);
          form.append("page_number", String(region.page));
          form.append(
            "crop",
            JSON.stringify({
              x0: region.x0,
              top: region.top,
              x1: region.x1,
              bottom: region.bottom,
            }),
          );
          form.append("black_threshold", String(BLACK_THRESHOLD));
          const res = await fetch(SCALE_URL, { method: "POST", body: form });
          if (!res.ok) {
            const body = await res.text().catch(() => "");
            throw new Error(
              `Scale detection failed on page ${region.page} (${res.status})${
                body ? `: ${body.slice(0, 200)}` : ""
              }`,
            );
          }
          return (await res.json()) as ScaleResponse;
        }),
      );
      for (const r of results) {
        if (r.drawing_scale_pts_per_inch == null) {
          throw new Error(
            `Couldn't infer a drawing scale on page ${r.page_number} — ${r.callout_count} callout(s) found but none had a matching duct rectangle. Try a tighter crop around a duct with a visible diameter callout.`,
          );
        }
        scaleByPage[r.page_number] = r;
      }
    } catch (e) {
      fail(e instanceof Error ? e.message : "Scale detection failed.", "scale");
      return;
    }
    if (runIdRef.current !== runId) return;
    setState((prev) =>
      prev.step !== "pipeline"
        ? prev
        : { ...prev, progress: { ...prev.progress, scale: "done", cleanup: "running" } },
    );

    // 3. Cleanup is purely client-side filtering inside the viewer — no async
    //    work. We flash the "running" state briefly so the user sees all three
    //    stages tick over rather than scale→done jumping straight to viewer.
    await new Promise((r) => setTimeout(r, 350));
    if (runIdRef.current !== runId) return;
    setState({
      step: "viewer",
      file,
      pdfUrl,
      data,
      regions,
      scaleByPage,
    });
  }, [state]);

  // Cancel an in-flight pipeline run if the user navigates away from the
  // pipeline step (re-crop or reset).
  useEffect(() => {
    return () => {
      runIdRef.current += 1;
    };
  }, []);

  if (state.step === "upload") {
    return (
      <UploadScreen
        apiUrl={EXTRACT_URL}
        initialFile={state.file}
        onContinue={onContinue}
      />
    );
  }

  if (state.step === "crop") {
    return (
      <Cropper
        file={state.file}
        pdfUrl={state.pdfUrl}
        onBack={onBackToUpload}
        onRun={runPipeline}
        loading={false}
      />
    );
  }

  if (state.step === "pipeline") {
    return (
      <PipelineProgress
        filename={state.file.name}
        state={state.progress}
        error={state.error}
        onRecrop={onBackToCrop}
        onReset={onReset}
      />
    );
  }

  return (
    <Viewer
      data={state.data}
      file={state.file}
      pdfUrl={state.pdfUrl}
      regions={state.regions}
      scaleByPage={state.scaleByPage}
      onReset={onReset}
    />
  );
}
