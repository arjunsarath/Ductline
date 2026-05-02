/**
 * View shell — Upload → Processing → Result (UI-SPEC.md "Three views, one route").
 *
 * No router. View state is a discriminated union; transitions happen on
 * upload (idle → processing) and on result (processing → result).
 */

import { useCallback, useState } from "react";
import { detectDrawing } from "./api/client";
import { ProcessingView } from "./components/ProcessingView";
import { ResultView } from "./components/ResultView";
import { UploadView } from "./components/UploadView";
import type { DrawingResult } from "./types/api";

type View =
  | { kind: "upload" }
  | { kind: "processing"; filename: string }
  | { kind: "result"; filename: string; file: File; result: DrawingResult }
  | { kind: "error"; filename: string; message: string };

export default function App() {
  const [view, setView] = useState<View>({ kind: "upload" });

  const handleFile = useCallback(async (file: File) => {
    setView({ kind: "processing", filename: file.name });
    try {
      const result = await detectDrawing(file);
      // Carry the original File alongside the result so the PDF.js renderer
      // can read its bytes without a re-fetch (V2 §5.7).
      setView({ kind: "result", filename: file.name, file, result });
    } catch (err) {
      const message = err instanceof Error ? err.message : "unknown error";
      setView({ kind: "error", filename: file.name, message });
    }
  }, []);

  const handleReset = useCallback(() => setView({ kind: "upload" }), []);

  switch (view.kind) {
    case "upload":
      return <UploadView onFile={handleFile} />;
    case "processing":
      return <ProcessingView filename={view.filename} />;
    case "result":
      return (
        <ResultView
          filename={view.filename}
          file={view.file}
          result={view.result}
          onReset={handleReset}
        />
      );
    case "error":
      return (
        <UploadView
          onFile={handleFile}
          errorMessage={`${view.filename}: ${view.message}`}
        />
      );
  }
}
