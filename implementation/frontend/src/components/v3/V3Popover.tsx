/**
 * Per-segment popover for the V3 result view.
 *
 * Mirrors the V1/V2 popover layout (header + Dimension + Pressure-class +
 * footer) but speaks the V3 result shape. The big posture difference: V3
 * is honest about ``estimated:size_only`` pressure class, so the section
 * surfaces the disclaimer + override hint when no flow was extracted.
 */

import { useEffect, useRef } from "react";
import type { Confidence, V3Segment } from "../../types/v3";

interface Props {
  segment: V3Segment;
  /** Anchor in stage-local coords (segment marker center). */
  anchor: { x: number; y: number };
  /** Height of the stage element so we can place above-or-below the marker
   *  based on available room without measuring the popover itself. */
  stageHeight: number;
  onClose: () => void;
}

type Placement = "above" | "below";

// Conservative — slightly larger than the typical rendered popover height so
// we err toward "place below" when in doubt. Avoids a flip cycle that would
// happen if we tried to read the actual popover rect during layout.
const POPOVER_HEIGHT_ESTIMATE = 520;

export function V3Popover({ segment, anchor, stageHeight, onClose }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    ref.current?.focus();
  }, []);

  // Derive placement from the anchor's vertical position vs the popover's
  // expected height. Pure computation — no setState during layout, so no
  // infinite-loop risk regardless of the layout settling cycle.
  const placement: Placement =
    anchor.y < POPOVER_HEIGHT_ESTIMATE + 16
      ? "below"
      : stageHeight && anchor.y > stageHeight - 60
        ? "above"
        : "above";

  return (
    <div
      ref={ref}
      className={`popover popover-${placement}`}
      role="dialog"
      tabIndex={-1}
      aria-label={`Segment ${segment.id} details`}
      style={{ left: anchor.x, top: anchor.y }}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <header className="popover-head">
        <div className="popover-head-title">
          <span className="popover-head-id">{segment.id}</span>
          <span>{segment.shape === "round" ? "Round duct" : "Rectangular duct"}</span>
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
      <PressureSection segment={segment} />
      <CalibrationSection segment={segment} />

      <footer className="popover-foot">
        <span>
          <kbd className="kbd-hint">←/→</kbd> cycle
        </span>
        <span>
          <kbd className="kbd-hint">Esc</kbd> close
        </span>
      </footer>
    </div>
  );
}

function DimensionSection({ segment }: { segment: V3Segment }) {
  const isRound = segment.shape === "round";
  const valueLabel = isRound
    ? `${segment.visible_unit}″ Ø`
    : `${segment.visible_unit}×${segment.hidden_unit} ${segment.page_unit}`;
  const detail = isRound
    ? `measured pixel diameter = ${segment.pixel_width.toFixed(1)} px (${
        segment.delta_pct >= 0 ? "+" : ""
      }${segment.delta_pct.toFixed(1)}% from drawing-wide ppu)`
    : `measured pixel width = ${segment.pixel_width.toFixed(
        1,
      )} px, plan-visible side picked = ${segment.visible_unit} (${
        segment.delta_pct >= 0 ? "+" : ""
      }${segment.delta_pct.toFixed(1)}% from drawing-wide ppu)`;
  return (
    <section className="popover-section">
      <div className="popover-section-label">Dimension</div>
      <div className="popover-row">
        <span className="popover-value">{valueLabel}</span>
        <ConfidencePill confidence={segment.dim_confidence} />
      </div>
      <div className="popover-trace">
        <span className="popover-trace-source">
          ↳ ocr text “{segment.token_text}”
        </span>
        <span className="popover-trace-detail">{detail}</span>
      </div>
    </section>
  );
}

function PressureSection({ segment }: { segment: V3Segment }) {
  const pc = segment.pressure;
  const cssTone = pc.value.toLowerCase();
  const isExtracted = pc.source === "extracted";

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
        <span className="popover-trace-source">
          ↳{" "}
          {isExtracted
            ? `extracted (${pc.flow_value} ${pc.flow_unit}${
                pc.velocity_fpm
                  ? `, ≈${pc.velocity_fpm.toFixed(0)} fpm`
                  : ""
              })`
            : "estimated from size — no CFM/L/s extracted on this segment"}
        </span>
        <span className="popover-trace-detail">
          {isExtracted
            ? `material: ${pc.material}, SMACNA velocity tier`
            : "User override is available; phase-2 work adds duct topology + downstream CFM aggregation."}
        </span>
      </div>
    </section>
  );
}

function CalibrationSection({ segment }: { segment: V3Segment }) {
  return (
    <section className="popover-section">
      <div className="popover-section-label">Provenance</div>
      <div className="popover-trace">
        <span className="popover-trace-source">
          ↳ system: {segment.system_id} · attribution: {segment.rule}
        </span>
        <span className="popover-trace-detail">
          chosen ppu {segment.chosen_ppu.toFixed(3)} px/{segment.page_unit}
        </span>
      </div>
    </section>
  );
}

function ConfidencePill({ confidence }: { confidence: Confidence }) {
  return (
    <span className={`confidence-pill conf-${confidence}`}>
      conf {confidence}
    </span>
  );
}
