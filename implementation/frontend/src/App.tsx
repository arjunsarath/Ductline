/**
 * View shell — Upload → Processing → Result (UI-SPEC.md "Three views, one route").
 *
 * No router. View state is a discriminated union; transitions happen on
 * upload (idle → processing), on the preliminary_result event
 * (processing → result, V2 §5.6), and on completion (final result
 * replaces the preliminary in the same view).
 *
 * Processing view is driven by streaming progress events (PR-D). The
 * SSE consumer in `api/client.ts` fires `onProgress` for each event; we
 * fold those into a `ProgressState` and pass it to ProcessingView.
 */

import { useCallback, useState } from "react";
import {
  detectDrawing,
  type ProgressEvent,
  type SegmentReviewedPayload,
} from "./api/client";
import {
  applyProgressEvent,
  initialProgressState,
  type ProgressState,
} from "./components/processingProgress";
import { ProcessingView } from "./components/ProcessingView";
import { ResultView, type ReviewerStatus } from "./components/ResultView";
import { UploadView } from "./components/UploadView";
import type { DrawingResult } from "./types/api";

/** Map of in-flight reviewer updates keyed by segment id. Merged into
 *  the rendered segment so verdicts / pressure_class / reasoning_trace
 *  appear in-place as the reviewer processes each draft. */
export type SegmentUpdateMap = Record<string, SegmentReviewedPayload>;

type View =
  | { kind: "upload" }
  | { kind: "processing"; filename: string; file: File; progress: ProgressState }
  | {
      kind: "result";
      filename: string;
      file: File;
      result: DrawingResult;
      /** Per-segment reviewer updates received over SSE. Cleared once
       *  the final result lands (the final's segments already carry the
       *  reviewer's mutations). */
      segmentUpdates: SegmentUpdateMap;
      /** Reviewer banner state: shows current/total while the reviewer
       *  is still running, null after pipeline_done. */
      reviewerStatus: ReviewerStatus | null;
    }
  | { kind: "error"; filename: string; message: string };

export default function App() {
  const [view, setView] = useState<View>({ kind: "upload" });

  const handleFile = useCallback(async (file: File) => {
    setView({
      kind: "processing",
      filename: file.name,
      file,
      progress: initialProgressState(),
    });
    try {
      const onProgress = (event: ProgressEvent) => {
        // Updates are dropped if the user has navigated away (view.kind
        // changed to error before the stream closed). That's safe —
        // React's setView guards against the stale path.
        setView((current) => {
          if (current.kind === "processing") {
            return {
              ...current,
              progress: applyProgressEvent(current.progress, event),
            };
          }
          if (current.kind === "result") {
            // After the preliminary_result event flips us to the result
            // view, reviewer events keep streaming. Merge them into the
            // segmentUpdates map and update the reviewer banner.
            return mergeReviewerEvent(current, event);
          }
          return current;
        });
      };
      const onPreliminary = (preliminary: DrawingResult) => {
        // Switch to the result view immediately. The reviewer phase
        // keeps running; review events update the rendered segments
        // in-place via `mergeReviewerEvent` above.
        setView((current) => {
          if (current.kind !== "processing") return current;
          return {
            kind: "result",
            filename: current.filename,
            file: current.file,
            result: preliminary,
            segmentUpdates: {},
            reviewerStatus: { current: 0, total: 0, running: true },
          };
        });
      };
      const result = await detectDrawing(file, onProgress, onPreliminary);
      // Carry the original File alongside the result so the PDF.js renderer
      // can read its bytes without a re-fetch (V2 §5.7).
      setView((current) => {
        // The final result already incorporates reviewer mutations (the
        // runner re-assembles after the reviewer phase). Drop the
        // segmentUpdates map and the reviewer banner; they're
        // redundant once the final is on screen.
        if (current.kind === "result") {
          return {
            ...current,
            result,
            segmentUpdates: {},
            reviewerStatus: null,
          };
        }
        // Defensive: if we never saw a preliminary (e.g. a server that
        // skipped the new event), fall through to a fresh result view.
        return {
          kind: "result",
          filename: file.name,
          file,
          result,
          segmentUpdates: {},
          reviewerStatus: null,
        };
      });
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
      return (
        <ProcessingView
          filename={view.filename}
          file={view.file}
          progress={view.progress}
        />
      );
    case "result":
      return (
        <ResultView
          filename={view.filename}
          file={view.file}
          result={view.result}
          segmentUpdates={view.segmentUpdates}
          reviewerStatus={view.reviewerStatus}
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

/** Apply a reviewer-related progress event to a result-view state.
 *
 *  • `segment_reviewed` updates the per-segment override map.
 *  • `review_start` / `review_done` advance the banner counter.
 *  • Other events (stage_start/done, pipeline_done, etc.) leave the
 *    view untouched; the banner clears when the awaited final
 *    `result` lands in `handleFile`.
 */
function mergeReviewerEvent(
  current: Extract<View, { kind: "result" }>,
  event: ProgressEvent,
): Extract<View, { kind: "result" }> {
  if (event.event === "segment_reviewed") {
    return {
      ...current,
      segmentUpdates: {
        ...current.segmentUpdates,
        [event.segment_id]: event,
      },
    };
  }
  if (event.event === "review_start" || event.event === "review_done") {
    return {
      ...current,
      reviewerStatus: {
        current: event.current,
        total: event.total,
        running: true,
      },
    };
  }
  return current;
}
