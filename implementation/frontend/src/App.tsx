/**
 * App shell — V3 flow (SOLUTION-DESIGN-V3 §4):
 *   Upload → Picker → Result
 *
 * V4 sits behind a tab toggle (rendered on the V3 upload page). The two
 * modes are independent flows — V3 stays the default to avoid disturbing
 * existing users; switching to V4 swaps in a self-contained `<V4View />`.
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
import { V4View } from "./components/v4/V4View";
import type {
  PickPayload,
  V3DetectResponse,
  V3RenderResponse,
} from "./types/v3";

type Mode = "v3" | "v4";

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
  const [mode, setMode] = useState<Mode>("v4");
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

  if (mode === "v4") {
    return (
      <V4View
        renderUploadHeader={() => <ModeToggle mode={mode} onChange={setMode} />}
      />
    );
  }

  switch (view.kind) {
    case "upload":
      return (
        <>
          <ModeToggle mode={mode} onChange={setMode} />
          <V3Upload onFile={handleFile} errorMessage={view.errorMessage} />
        </>
      );
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

function ModeToggle({
  mode,
  onChange,
}: {
  mode: Mode;
  onChange: (next: Mode) => void;
}) {
  return (
    <div
      className="app-mode-toggle"
      role="tablist"
      aria-label="Pipeline version"
      style={{ position: "fixed", top: 14, right: 20, zIndex: 60 }}
    >
      <span className="app-mode-toggle__label">Pipeline:</span>
      <button
        type="button"
        role="tab"
        aria-selected={mode === "v4"}
        className={mode === "v4" ? "is-active" : ""}
        onClick={() => onChange("v4")}
      >
        V4 (active)
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={mode === "v3"}
        className={mode === "v3" ? "is-active" : ""}
        onClick={() => onChange("v3")}
      >
        V3 (fallback)
      </button>
    </div>
  );
}
