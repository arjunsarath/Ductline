/**
 * Processing view per Paper artboard 02. Filename in top bar, big timer,
 * detection-pipeline card showing all 7 stages.
 *
 * The backend returns the result in a single response, so the per-stage
 * status here is a time-driven heuristic — stages 1–3 are quick and assumed
 * complete after a few seconds, stage 4 (the VLM call) is the long pole.
 * Once the response arrives we collapse all stages to "done" / warning.
 */

import { useEffect, useRef, useState } from "react";
import { Brand } from "./Brand";
import { Stepper } from "./Stepper";

interface Props {
  filename: string;
}

interface StageDef {
  num: string;
  name: string;
  tags: Array<"ALG" | "WF" | "AGT">;
  detail: string;
}

const STAGES: StageDef[] = [
  { num: "01", name: "Ingest", tags: ["ALG"], detail: "pdf2image @ 200 DPI · normalized to RGB" },
  { num: "02", name: "Quality check", tags: ["ALG"], detail: "Laplacian variance · projection skew · sample OCR" },
  { num: "03", name: "Region detect", tags: ["ALG"], detail: "title block + duct schedule · classical pass" },
  { num: "04", name: "Duct detection", tags: ["AGT", "ALG"], detail: "VLM call → HoughLinesP refinement" },
  { num: "05", name: "Text extraction", tags: ["ALG"], detail: "RapidOCR over segments + schedule grammar" },
  { num: "06", name: "Pressure class", tags: ["WF"], detail: "4-tier ranked policy state machine" },
  { num: "07", name: "Assemble", tags: ["ALG"], detail: "merge into DrawingResult + reasoning trace" },
];

// Elapsed-second thresholds at which each stage transitions from pending → active.
// Tuned against the smoke-run timings: stages 1–3 finish quickly, stage 4
// dominates wall-clock with the VLM call.
const STAGE_ACTIVE_AT_S = [0, 1, 2, 3, 999, 999, 999];

export function ProcessingView({ filename }: Props) {
  const [elapsed, setElapsed] = useState(0);
  // Frozen durations for completed stages — index N is set when stage N
  // transitions from active to done. Stages still active or pending are null.
  const [stageDurations, setStageDurations] = useState<Array<number | null>>(
    () => STAGES.map(() => null),
  );
  const lastActiveRef = useRef(0);

  useEffect(() => {
    const start = performance.now();
    const id = window.setInterval(() => {
      const now = (performance.now() - start) / 1000;
      setElapsed(now);

      const active = currentStageIndex(now);
      if (active > lastActiveRef.current) {
        // Stages between the old and new active index just completed; freeze
        // each one's duration as the gap between its activation threshold
        // and the next stage's activation threshold (or the current time
        // for the most recently completed one).
        setStageDurations((prev) => {
          const next = [...prev];
          for (let i = lastActiveRef.current; i < active; i++) {
            if (next[i] === null) {
              const startedAt = STAGE_ACTIVE_AT_S[i] ?? 0;
              const endedAt = STAGE_ACTIVE_AT_S[i + 1] ?? now;
              next[i] = Math.max(endedAt - startedAt, 0.1);
            }
          }
          return next;
        });
        lastActiveRef.current = active;
      }
    }, 100);
    return () => window.clearInterval(id);
  }, []);

  const activeIndex = currentStageIndex(elapsed);

  return (
    <main className="processing-view">
      <header className="topbar">
        <Brand />
        <div className="processing-topbar-filename">
          <span className="filename">{filename}</span>
        </div>
        <button type="button" className="button-ghost" disabled title="Cancel not wired in v1">
          Cancel
        </button>
      </header>

      <section className="processing-body">
        <div className="processing-header">
          <Stepper active="processing" />
          <div className="timer">{formatTimer(elapsed)}</div>
          <div className="processing-stage-line">
            Stage {activeIndex + 1} of 7 — {STAGES[activeIndex].name.toLowerCase()}.
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
          {STAGES.map((stage, index) => {
            const status: StageStatus =
              index < activeIndex ? "done" : index === activeIndex ? "active" : "pending";
            return (
              <PipelineRow
                key={stage.num}
                stage={stage}
                status={status}
                duration={stageDurations[index]}
              />
            );
          })}
        </div>

        <div className="processing-foot">
          <InfoIcon />
          <span>
            One vision-model call per drawing. Everything else runs
            deterministically — same input, same output, every time.
          </span>
        </div>
      </section>
    </main>
  );
}

type StageStatus = "done" | "active" | "pending";

function PipelineRow({
  stage,
  status,
  duration,
}: {
  stage: StageDef;
  status: StageStatus;
  duration: number | null;
}) {
  return (
    <div
      className={`pipeline-row${
        status === "active" ? " is-current" : status === "pending" ? " is-pending" : ""
      }`}
    >
      <div className="pipeline-status">
        {status === "done" && <CheckIcon />}
        {status === "active" && <span className="spinner-ring" />}
        {status === "pending" && <PendingIcon />}
      </div>
      <div className="pipeline-row-num">{stage.num}</div>
      <div className="pipeline-row-body">
        <div className="pipeline-row-head">
          <span className="pipeline-row-name">{stage.name}</span>
          {stage.tags.map((t) => (
            <span
              key={t}
              className={`tag ${
                status === "pending"
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
          <span className="pipeline-row-detail">{stage.detail}</span>
        </div>
      </div>
      <div className="pipeline-row-time">
        {status === "done" && duration !== null && `${duration.toFixed(1)} s`}
        {status === "active" && "running"}
        {status === "pending" && "—"}
      </div>
    </div>
  );
}

function currentStageIndex(elapsed: number): number {
  for (let i = STAGE_ACTIVE_AT_S.length - 1; i >= 0; i--) {
    if (elapsed >= STAGE_ACTIVE_AT_S[i]) return i;
  }
  return 0;
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

function InfoIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <circle cx="7" cy="7" r="6" stroke="#5C6166" strokeWidth="1.2" fill="none" />
      <path d="M7 4 L7 7.5 M7 9.5 L7 10" stroke="#5C6166" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}
