/**
 * App shell — V3 flow (SOLUTION-DESIGN-V3 §4):
 *   Upload → Picker → Result
 *
 * The legacy V1/V2 pipeline now lives behind /api/agent on the backend
 * and is not surfaced in the UI by default — it's parked until we have
 * a sufficiently-capable on-prem VLM.
 */

import { useCallback, useState } from "react";
import { detect, renderPage } from "./api/v3Client";
import { V3PickerView } from "./components/v3/V3PickerView";
import { V3ResultView } from "./components/v3/V3ResultView";
import { V3Upload } from "./components/v3/V3Upload";
import type {
  PickPayload,
  V3DetectResponse,
  V3RenderResponse,
} from "./types/v3";

type View =
  | { kind: "upload"; errorMessage?: string }
  | { kind: "rendering"; file: File }
  | {
      kind: "picking";
      file: File;
      render: V3RenderResponse;
      isRunning: boolean;
      errorMessage?: string;
    }
  | {
      kind: "result";
      file: File;
      response: V3DetectResponse;
    };

export default function App() {
  const [view, setView] = useState<View>({ kind: "upload" });

  const handleFile = useCallback(async (file: File) => {
    setView({ kind: "rendering", file });
    try {
      const render = await renderPage(file);
      setView({ kind: "picking", file, render, isRunning: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : "render failed";
      setView({ kind: "upload", errorMessage: `${file.name}: ${message}` });
    }
  }, []);

  const handleRun = useCallback(
    async (picks: PickPayload[]) => {
      setView((cur) =>
        cur.kind === "picking" ? { ...cur, isRunning: true, errorMessage: undefined } : cur,
      );
      const current = view; // closes over latest view at click time
      if (current.kind !== "picking") return;
      try {
        const response = await detect(current.file, picks);
        setView({ kind: "result", file: current.file, response });
      } catch (err) {
        const message = err instanceof Error ? err.message : "detect failed";
        setView((cur) =>
          cur.kind === "picking"
            ? { ...cur, isRunning: false, errorMessage: message }
            : cur,
        );
      }
    },
    [view],
  );

  const handleBackToUpload = useCallback(() => {
    setView({ kind: "upload" });
  }, []);

  switch (view.kind) {
    case "upload":
      return <V3Upload onFile={handleFile} errorMessage={view.errorMessage} />;
    case "rendering":
      return (
        <main className="processing-view">
          <div className="processing-card">
            <h2>Rendering page…</h2>
            <p>{view.file.name}</p>
            <p className="muted">
              Probing for smallest text height + applying adaptive DPI.
            </p>
          </div>
        </main>
      );
    case "picking":
      return (
        <V3PickerView
          filename={view.file.name}
          render={view.render}
          onRun={handleRun}
          onBack={handleBackToUpload}
          isRunning={view.isRunning}
          errorMessage={view.errorMessage}
        />
      );
    case "result":
      return (
        <V3ResultView
          filename={view.file.name}
          file={view.file}
          response={view.response}
          onReset={handleBackToUpload}
        />
      );
  }
}
