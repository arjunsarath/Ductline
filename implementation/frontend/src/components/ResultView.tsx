/**
 * Result view per Paper artboard 03 — top bar (brand + filename + zoom + toggles)
 * → optional quality banner → reviewer banner (V2 §5.6) → viewer + sidebar.
 *
 * State: selection, grayscale, sidebar collapse, and a UI-only zoom percentage
 * (the canvas auto-fits and zoom is decorative in v1 — UI-SPEC §"Open until
 * first drawing"). Keyboard: ←/→ cycle selection, Esc clear, g grayscale, s sidebar.
 *
 * The reviewer can be running while this view is on screen (V2 §5.6 — we
 * surface the assembled-but-not-yet-reviewed result as soon as detection
 * finishes). `segmentUpdates` carries per-segment review verdicts and
 * confidence bumps that arrive on the SSE stream after the preliminary
 * result; we merge them into each segment before rendering so the
 * popover / sidebar / viewer all see the latest reviewer output.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { SegmentReviewedPayload } from "../api/client";
import type { DrawingResult, Segment } from "../types/api";
import { Brand } from "./Brand";
import { QualityBanner } from "./QualityBanner";
import { Sidebar } from "./Sidebar";
import { Viewer } from "./Viewer";
import {
  INITIAL_VIEWPORT,
  SCALE_MAX,
  SCALE_MIN,
  type Viewport,
} from "./viewport";

export interface ReviewerStatus {
  current: number;
  total: number;
  running: boolean;
}

interface Props {
  filename: string;
  file: File;
  result: DrawingResult;
  /** Per-segment reviewer updates that arrived after the preliminary
   *  result was rendered. Empty once the final result lands. */
  segmentUpdates?: Record<string, SegmentReviewedPayload>;
  /** Reviewer phase progress; null after the reviewer completes. */
  reviewerStatus?: ReviewerStatus | null;
  onReset: () => void;
}

export function ResultView({
  filename,
  file,
  result,
  segmentUpdates,
  reviewerStatus,
  onReset,
}: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [grayscale, setGrayscale] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [viewport, setViewport] = useState<Viewport>(INITIAL_VIEWPORT);

  const zoomBy = useCallback((factor: number) => {
    setViewport((v) => {
      const next = clamp(v.scale * factor, SCALE_MIN, SCALE_MAX);
      // Anchor to current center: keep tx/ty so the visible center stays put.
      // Simpler than locking to a screen point and adequate for button clicks.
      return { ...v, scale: next };
    });
  }, []);

  const resetViewport = useCallback(() => setViewport(INITIAL_VIEWPORT), []);

  const rotate = useCallback(() => {
    setViewport((v) => ({ ...v, rotationDeg: (v.rotationDeg + 90) % 360 }));
    setSelectedId(null);
  }, []);

  // Merge per-segment reviewer updates onto the rendered result. The
  // result object itself is left unchanged so memo identity holds for
  // its other fields (display_image_data_url, aggregate, etc.); only
  // the segments list is rebuilt when an update arrives.
  const mergedResult = useMemo<DrawingResult>(() => {
    if (!segmentUpdates || Object.keys(segmentUpdates).length === 0) {
      return result;
    }
    const segments = result.segments.map((segment) =>
      applySegmentUpdate(segment, segmentUpdates[segment.id]),
    );
    return { ...result, segments };
  }, [result, segmentUpdates]);

  const segmentIds = useMemo(
    () => mergedResult.segments.map((s) => s.id),
    [mergedResult.segments],
  );

  useEffect(() => {
    function handler(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.tagName === "INPUT" || target?.tagName === "TEXTAREA") return;

      if (event.key === "Escape") return setSelectedId(null);
      if (event.key === "g" || event.key === "G") return setGrayscale((g) => !g);
      if (event.key === "s" || event.key === "S")
        return setSidebarCollapsed((c) => !c);
      if (event.key === "ArrowRight" || (event.key === "Tab" && !event.shiftKey)) {
        setSelectedId((current) => stepSelection(segmentIds, current, +1));
        event.preventDefault();
      }
      if (event.key === "ArrowLeft" || (event.key === "Tab" && event.shiftKey)) {
        setSelectedId((current) => stepSelection(segmentIds, current, -1));
        event.preventDefault();
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [segmentIds]);

  const handleSelect = useCallback(
    (id: string | null) => setSelectedId(id),
    [],
  );

  return (
    <main className="result-view">
      <header className="result-topbar">
        <div className="result-topbar-left">
          <Brand />
          <button type="button" className="button-ghost" onClick={onReset}>
            ← New drawing
          </button>
          <div className="result-topbar-meta">
            <span className="mono">{filename}</span>
            <span>·</span>
            <strong>{mergedResult.aggregate.total} segments</strong>
          </div>
        </div>
        <div className="result-topbar-spacer" />
        <div className="result-topbar-right">
          <button type="button" className="button-ghost" onClick={resetViewport}>
            Reset
          </button>
          <button
            type="button"
            className="button-ghost"
            aria-pressed={grayscale}
            onClick={() => setGrayscale((g) => !g)}
            title="Toggle grayscale"
          >
            Grayscale
            <kbd className="kbd-hint">G</kbd>
          </button>
          <button
            type="button"
            className="button-ghost"
            aria-pressed={!sidebarCollapsed}
            onClick={() => setSidebarCollapsed((c) => !c)}
            title="Toggle sidebar"
          >
            Sidebar
            <kbd className="kbd-hint">S</kbd>
          </button>
        </div>
      </header>

      {mergedResult.quality.overall !== "high" && (
        <QualityBanner quality={mergedResult.quality} />
      )}

      {reviewerStatus?.running && (
        <ReviewerBanner status={reviewerStatus} />
      )}

      <div className="result-body">
        <Viewer
          result={mergedResult}
          file={file}
          selectedId={selectedId}
          grayscale={grayscale}
          viewport={viewport}
          onViewportChange={setViewport}
          onSelect={handleSelect}
          onRotate={rotate}
          onZoomBy={zoomBy}
        />
        {!sidebarCollapsed && (
          <Sidebar
            result={mergedResult}
            selectedId={selectedId}
            onSelect={handleSelect}
          />
        )}
      </div>
    </main>
  );
}

/** Apply a reviewer's per-segment update onto the preliminary segment.
 *
 *  Merge rules — the reviewer is the authority on the fields it owns:
 *    • verdict / iterations replace the defaults wholesale.
 *    • pressure_class is replaced when the reviewer emitted one
 *      (post-bump confidence band, V2 §5.6 confidence-ladder math).
 *    • reasoning_trace is replaced with the reviewer's full trace —
 *      the backend already prepended the pre-existing detect / OCR /
 *      schedule steps before appending the reviewer_critique /
 *      reviewer_refine entries, so we never need to splice.
 *    • geometry / dimension / id are reviewer-untouched.
 */
function applySegmentUpdate(
  segment: Segment,
  update: SegmentReviewedPayload | undefined,
): Segment {
  if (!update) return segment;
  return {
    ...segment,
    review_verdict: update.verdict,
    review_iterations: update.iterations,
    pressure_class: update.pressure_class ?? segment.pressure_class,
    reasoning_trace: update.reasoning_trace.map((step) => ({
      stage: step.stage,
      evidence: step.evidence,
      iteration: step.iteration ?? undefined,
    })),
  };
}

function ReviewerBanner({ status }: { status: ReviewerStatus }) {
  const label =
    status.total > 0
      ? `Reviewer running… ${status.current} / ${status.total}`
      : "Reviewer running…";
  return (
    <div className="reviewer-banner" role="status" aria-live="polite">
      <span className="reviewer-banner-spinner" aria-hidden="true" />
      <span className="reviewer-banner-label">{label}</span>
    </div>
  );
}

function stepSelection(ids: string[], current: string | null, delta: number): string | null {
  if (ids.length === 0) return null;
  if (current === null) return delta > 0 ? ids[0] : ids[ids.length - 1];
  const index = ids.indexOf(current);
  const next = (index + delta + ids.length) % ids.length;
  return ids[next];
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
