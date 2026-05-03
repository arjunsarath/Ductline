/**
 * Processing view per Paper artboard 02. Filename in top bar, big timer,
 * detection-pipeline card showing each stage as it runs.
 *
 * Driven by streaming progress events from the SSE consumer (PR-D). The
 * v1 time-driven heuristic is gone — stages now transition pending → active
 * → done strictly in response to events. Long-running stages (duct
 * detection, reviewer) render sub-progress as "tile X / Y" or
 * "segment X / Y — verdict".
 *
 * The timer is still local — it's just elapsed wall-clock since the
 * pipeline_start event arrived (or since component mount as a fallback).
 */

import { useEffect, useState } from "react";
import {
  STAGE_ORDER,
  elapsedSeconds,
  stageLabel,
  type ProgressState,
  type StageInfo,
  type StageName,
} from "./processingProgress";
import { Brand } from "./Brand";
import { Stepper } from "./Stepper";

interface Props {
  filename: string;
  progress: ProgressState;
}

const STAGE_TAGS: Record<StageName, Array<"ALG" | "WF" | "AGT">> = {
  ingest: ["ALG"],
  probe_ocr: ["ALG"],
  page_categorize: ["ALG", "AGT"],
  legend_parse: ["ALG", "AGT"],
  quality: ["ALG"],
  region_detect: ["ALG"],
  duct_detect_tiled: ["AGT", "ALG"],
  text_extraction: ["ALG"],
  pressure_class: ["WF"],
  review: ["AGT", "WF"],
};

const STAGE_DETAIL: Record<StageName, string> = {
  ingest: "DrawingSource classifier · vector / raster split",
  probe_ocr: "low-DPI text inventory · smallest-text → target DPI",
  page_categorize: "Hough decomposition · keyword classification · VLM fallback",
  legend_parse: "OCR rows + glyph fallback · drawing-specific legend",
  quality: "Laplacian variance · projection skew · OCR sample",
  region_detect: "title block + duct schedule (v1 detector)",
  duct_detect_tiled: "tile @ 1100 px · trail context · stitch in source space",
  text_extraction: "RapidOCR over segments + schedule grammar",
  pressure_class: "4-tier ranked policy state machine",
  review: "per-segment verdict · refine if implausible · max 2 iters",
};

export function ProcessingView({ filename, progress }: Props) {
  // Ticker for the timer display. We don't store the elapsed value in
  // state because progress updates also re-render — a 100 ms tick is
  // enough for a smooth "MM:SS.s" display.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 100);
    return () => window.clearInterval(id);
  }, []);

  const elapsed = elapsedSeconds(progress, performance.now());
  const activeStage = findActiveStage(progress);
  const overallStageNumber = activeStage
    ? STAGE_ORDER.indexOf(activeStage) + 1
    : (STAGE_ORDER.findIndex((n) => progress.stages[n].status === "pending") + 1 || STAGE_ORDER.length);

  // "what's happening right now" — prefer the active stage's sub-progress
  // label, fall back to the stage label, fall back to "starting…".
  let statusLine = "starting…";
  if (activeStage) {
    const info = progress.stages[activeStage];
    statusLine = info.subProgress
      ? `${stageLabel(activeStage).toLowerCase()} — ${info.subProgress.label}`
      : `${stageLabel(activeStage).toLowerCase()}…`;
  } else if (progress.completed) {
    statusLine = "done";
  }

  return (
    <main className="processing-view">
      <header className="topbar">
        <Brand />
        <div className="processing-topbar-filename">
          <span className="filename">{filename}</span>
        </div>
        <button type="button" className="button-ghost" disabled title="Cancel not wired">
          Cancel
        </button>
      </header>

      <section className="processing-body">
        <div className="processing-header">
          <Stepper active="processing" />
          <div className="timer" data-tick={tick}>{formatTimer(elapsed)}</div>
          <div className="processing-stage-line">
            Stage {overallStageNumber} of {STAGE_ORDER.length} — {statusLine}
          </div>
        </div>

        <div className="pipeline">
          <div className="pipeline-header">
            <span className="pipeline-header-label">Detection pipeline</span>
            <div className="pipeline-legend">
              <span className="legend-pill tag-alg">● ALG · algorithmic</span>
              <span className="legend-pill tag-wf">● WF · workflow</span>
              <span className="legend-pill tag-agt">● AGT · agent</span>
            </div>
          </div>
          {STAGE_ORDER.map((name, index) => (
            <PipelineRow
              key={name}
              num={String(index + 1).padStart(2, "0")}
              info={progress.stages[name]}
              tags={STAGE_TAGS[name]}
              detail={STAGE_DETAIL[name]}
            />
          ))}
        </div>

        <div className="processing-foot">
          <InfoIcon />
          <span>
            Tiled detection runs many small VLM calls and a per-segment
            reviewer. Long drawings fan out to dozens of model calls — the
            sub-progress above tracks each one.
          </span>
        </div>
      </section>
    </main>
  );
}

function PipelineRow({
  num,
  info,
  tags,
  detail,
}: {
  num: string;
  info: StageInfo;
  tags: Array<"ALG" | "WF" | "AGT">;
  detail: string;
}) {
  const cls =
    info.status === "active"
      ? " is-current"
      : info.status === "pending"
        ? " is-pending"
        : info.status === "failed"
          ? " is-failed"
          : "";
  return (
    <div className={`pipeline-row${cls}`}>
      <div className="pipeline-status">
        {info.status === "done" && <CheckIcon />}
        {info.status === "active" && <span className="spinner-ring" />}
        {info.status === "pending" && <PendingIcon />}
        {info.status === "failed" && <FailIcon />}
      </div>
      <div className="pipeline-row-num">{num}</div>
      <div className="pipeline-row-body">
        <div className="pipeline-row-head">
          <span className="pipeline-row-name">{stageLabel(info.name)}</span>
          {tags.map((t) => (
            <span
              key={t}
              className={`tag ${
                info.status === "pending"
                  ? "tag-muted"
                  : t === "ALG"
                    ? "tag-alg"
                    : t === "WF"
                      ? "tag-wf"
                      : "tag-agt"
              }`}
            >
              {t}
            </span>
          ))}
          <span className="pipeline-row-detail">{detail}</span>
        </div>
        {info.subProgress && info.status === "active" && (
          <SubProgressBar
            current={info.subProgress.current}
            total={info.subProgress.total}
            label={info.subProgress.label}
          />
        )}
        {info.error && info.status === "failed" && (
          <div className="pipeline-row-error">{info.error}</div>
        )}
      </div>
      <div className="pipeline-row-time">
        {info.status === "done" && info.durationSec !== null && `${info.durationSec.toFixed(1)} s`}
        {info.status === "active" && (info.subProgress ? `${info.subProgress.current}/${info.subProgress.total}` : "running")}
        {info.status === "failed" && "failed"}
        {info.status === "pending" && "—"}
      </div>
    </div>
  );
}

function SubProgressBar({
  current,
  total,
  label,
}: {
  current: number;
  total: number;
  label: string;
}) {
  const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;
  return (
    <div className="pipeline-row-subprogress">
      <div
        className="pipeline-row-subprogress-bar"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="pipeline-row-subprogress-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="pipeline-row-subprogress-label">{label}</div>
    </div>
  );
}

function findActiveStage(progress: ProgressState): StageName | null {
  for (const name of STAGE_ORDER) {
    if (progress.stages[name].status === "active") return name;
  }
  return null;
}

function formatTimer(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${remaining.toFixed(1).padStart(4, "0")}`;
}

function CheckIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="7" fill="#059669" />
      <path d="M5 8 L7 10 L11 6" stroke="#FFFFFF" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function PendingIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="6.5" stroke="#D6D2C7" strokeWidth="1.2" fill="#FFFFFF" />
    </svg>
  );
}

function FailIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="7" fill="#dc2626" />
      <path d="M5 5 L11 11 M11 5 L5 11" stroke="#FFFFFF" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function InfoIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <circle cx="7" cy="7" r="6" stroke="#5C6166" strokeWidth="1.2" fill="none" />
      <path d="M7 4 L7 7.5 M7 9.5 L7 10" stroke="#5C6166" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}
