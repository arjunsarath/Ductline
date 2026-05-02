/**
 * Quality banner — amber/red strip below the result top bar when overall
 * quality is not "high". The "View metrics" button toggles an inline panel
 * exposing the three numeric quality scores from stage 2.
 */

import { useState } from "react";
import type { Quality } from "../types/api";

interface Props {
  quality: Quality;
}

export function QualityBanner({ quality }: Props) {
  const [showMetrics, setShowMetrics] = useState(false);
  const tone = quality.overall === "medium" ? "warn" : "danger";
  return (
    <div>
      <div className={`quality-banner quality-banner-${tone}`} role="status">
        <span>
          <strong>Drawing quality: {quality.overall}</strong>
          {quality.warnings.length > 0 && (
            <>
              {" · "}
              {quality.warnings.length}{" "}
              {quality.warnings.length === 1 ? "warning" : "warnings"}
              {" — "}
              {quality.warnings.join("; ")}
            </>
          )}
        </span>
        <button
          type="button"
          className="quality-banner-link"
          onClick={() => setShowMetrics((s) => !s)}
          aria-expanded={showMetrics}
        >
          {showMetrics ? "Hide metrics" : "View metrics"}
        </button>
      </div>
      {showMetrics && <QualityMetrics quality={quality} />}
    </div>
  );
}

function QualityMetrics({ quality }: { quality: Quality }) {
  return (
    <div className="quality-metrics" role="group" aria-label="quality metrics">
      <Metric
        label="Blur (Laplacian variance)"
        value={quality.blur_score.toFixed(0)}
        hint="higher = sharper · ≥ 100 high"
      />
      <Metric
        label="Skew"
        value={`${quality.skew_degrees.toFixed(1)}°`}
        hint="≤ 1° high · ≤ 3° medium"
      />
      <Metric
        label="Sample-region OCR conf."
        value={quality.ocr_confidence_avg.toFixed(2)}
        hint="≥ 0.85 high · ≥ 0.65 medium"
      />
    </div>
  );
}

function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="quality-metric">
      <div className="quality-metric-label">{label}</div>
      <div className="quality-metric-value mono">{value}</div>
      <div className="quality-metric-hint">{hint}</div>
    </div>
  );
}
