/**
 * Popover — load-bearing per UI-SPEC.md and Paper artboard 03. Header card
 * (segment id + shape + close), then a Dimension section and a Pressure-class
 * section. Every value carries a `↳` line citing the stage that produced it.
 */

import { useEffect, useRef } from "react";
import type {
  Confidence,
  ReasoningStep,
  ReviewVerdict,
  Segment,
} from "../types/api";

interface Props {
  segment: Segment;
  anchor: { x: number; y: number };
  onClose: () => void;
}

export function Popover({ segment, anchor, onClose }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    ref.current?.focus();
  }, []);

  return (
    <div
      ref={ref}
      className="popover"
      role="dialog"
      tabIndex={-1}
      aria-label={`Segment ${segment.id} details`}
      style={{ left: anchor.x, top: anchor.y }}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <header className="popover-head">
        <div className="popover-head-title">
          <span className="popover-head-id">{segment.id}</span>
          <span>
            {segment.dimension?.shape === "round" ? "Round duct" : "Rectangular duct"}
          </span>
        </div>
        <button
          type="button"
          className="popover-close"
          aria-label="Close"
          onClick={onClose}
        >
          ×
        </button>
      </header>

      <DimensionSection segment={segment} />
      <PressureClassSection segment={segment} />
      <ReviewerSection segment={segment} />

      <footer className="popover-foot">
        <span>
          <kbd className="kbd-hint">Tab</kbd> cycle
        </span>
        <span>
          <kbd className="kbd-hint">Esc</kbd> close
        </span>
      </footer>
    </div>
  );
}

function DimensionSection({ segment }: { segment: Segment }) {
  const dim = segment.dimension;
  const evidence = primaryEvidence(segment, "ocr_callout");

  return (
    <section className="popover-section">
      <div className="popover-section-label">Dimension</div>
      <div className="popover-row">
        <span className="popover-value">{dim?.value ?? "—"}</span>
        <ConfidencePill confidence={dim?.confidence ?? "low"} />
      </div>
      <div className="popover-trace">
        <span className="popover-trace-source">
          ↳ {dim?.source ?? "ocr:no_match"}
        </span>
        <span className="popover-trace-detail">
          {evidence ?? "no callout text within search radius"}
        </span>
      </div>
    </section>
  );
}

function PressureClassSection({ segment }: { segment: Segment }) {
  const pc = segment.pressure_class;
  const evidence = primaryEvidence(segment, "schedule_lookup");
  const cssTone = pc.value.toLowerCase();

  return (
    <section className="popover-section">
      <div className="popover-section-label">Pressure class</div>
      <div className="popover-row">
        <div className="popover-pc-row">
          <span className={`popover-pc-dot pc-${cssTone}`} />
          <span className={`popover-pc-value pc-${cssTone}`}>{pc.value}</span>
        </div>
        <ConfidencePill confidence={pc.confidence} />
      </div>
      <div className="popover-trace">
        <span className="popover-trace-source">↳ {pc.source}</span>
        {evidence && <span className="popover-trace-detail">{evidence}</span>}
      </div>
      {pc.alternatives.length > 0 && (
        <div className="popover-alternatives">
          alternatives: {pc.alternatives.join(", ")}
        </div>
      )}
    </section>
  );
}

function ReviewerSection({ segment }: { segment: Segment }) {
  // Reviewer steps land in the trace at stages "reviewer_critique" /
  // "reviewer_refine" (V2 §6.2). When the backend hasn't emitted any (today's
  // main, pre-PR-6), this section renders nothing — the popover looks
  // identical to v1.
  const reviewerSteps = segment.reasoning_trace.filter(isReviewerStep);
  if (reviewerSteps.length === 0) return null;

  // Per V2 §5.7: per-step verdict isn't emitted today; use the segment-
  // terminal verdict as the colouring proxy. When unknown/missing, fall back
  // to "uncertain" tone so the row still renders something legible.
  const verdict: ReviewVerdict = segment.review_verdict ?? "uncertain";
  const tone = critiqueToneClass(verdict);

  return (
    <section className="popover-section popover-section--reviewer">
      <div className="popover-section-label">Reviewer</div>
      {reviewerSteps.map((step, index) => (
        <div key={`${step.stage}-${index}`} className={`critique-row ${tone}`}>
          <span className="critique-evidence">{step.evidence}</span>
          {step.iteration !== undefined && step.iteration > 1 && (
            <span className="critique-iter">· iter {step.iteration}</span>
          )}
        </div>
      ))}
    </section>
  );
}

function isReviewerStep(step: ReasoningStep): boolean {
  return step.stage === "reviewer_critique" || step.stage === "reviewer_refine";
}

function critiqueToneClass(verdict: ReviewVerdict): string {
  switch (verdict) {
    case "plausible":
      return "critique-plausible";
    case "implausible":
      return "critique-implausible";
    case "uncertain":
    case "not_reviewed":
    default:
      return "critique-uncertain";
  }
}

function ConfidencePill({ confidence }: { confidence: Confidence }) {
  return (
    <span className={`confidence-pill conf-${confidence}`}>
      conf {confidence}
    </span>
  );
}

function primaryEvidence(segment: Segment, stage: string): string | null {
  return (
    segment.reasoning_trace.find((step) => step.stage === stage)?.evidence ?? null
  );
}
