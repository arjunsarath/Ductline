"use client";

import { useCallback, useState } from "react";
import dynamic from "next/dynamic";
import { toast } from "sonner";
import UploadScreen from "@/components/upload-screen";
import type { CropRegion, ExtractResponse } from "@/lib/extract";

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

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/extract";

type State =
  | { step: "upload"; file: File | null }
  | { step: "crop"; file: File; pdfUrl: string }
  | {
      step: "viewer";
      file: File;
      pdfUrl: string;
      data: ExtractResponse;
      regions: CropRegion[];
    };

export default function Page() {
  const [state, setState] = useState<State>({ step: "upload", file: null });
  const [loading, setLoading] = useState(false);

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

  const runExtraction = useCallback(
    async (regions: CropRegion[]) => {
      if (state.step !== "crop") return;
      setLoading(true);
      try {
        const form = new FormData();
        form.append("file", state.file);
        form.append("crop", JSON.stringify(regions));
        const res = await fetch(API_URL, { method: "POST", body: form });
        if (!res.ok) {
          const body = await res.text().catch(() => "");
          throw new Error(
            `Extract failed (${res.status})${body ? `: ${body.slice(0, 200)}` : ""}`,
          );
        }
        const data = (await res.json()) as ExtractResponse;
        if (data.pages.length === 0) {
          throw new Error("No elements found in the selected regions.");
        }
        setState({
          step: "viewer",
          file: state.file,
          pdfUrl: state.pdfUrl,
          data,
          regions,
        });
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Unknown error during extraction.";
        toast.error("Extraction failed", { description: msg });
      } finally {
        setLoading(false);
      }
    },
    [state],
  );

  if (state.step === "upload") {
    return (
      <UploadScreen
        apiUrl={API_URL}
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
        onRun={runExtraction}
        loading={loading}
      />
    );
  }

  return (
    <Viewer
      data={state.data}
      file={state.file}
      regions={state.regions}
      onReset={onReset}
    />
  );
}
