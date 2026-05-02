/**
 * TypeScript mirrors of the backend Pydantic schemas (SOLUTION-DESIGN §5.2).
 * The contract is the source of truth on the backend; these types must match
 * — drift means a bug.
 */

export type Confidence = "high" | "medium" | "low";
export type PressureClassValue = "LOW" | "MEDIUM" | "HIGH";
export type DuctShape = "round" | "rectangular";
export type QualityVerdict = "high" | "medium" | "low";

export interface Geometry {
  type: "polyline" | "bbox";
  points: [number, number][];
}

export interface Dimension {
  value: string;
  shape: DuctShape;
  confidence: Confidence;
  source: string;
}

export interface PressureClass {
  value: PressureClassValue;
  confidence: Confidence;
  source: string;
  alternatives: string[];
}

export interface ReasoningStep {
  stage: string;
  evidence: string;
}

export interface Segment {
  id: string;
  geometry: Geometry;
  dimension: Dimension | null;
  pressure_class: PressureClass;
  reasoning_trace: ReasoningStep[];
}

export interface Quality {
  overall: QualityVerdict;
  blur_score: number;
  skew_degrees: number;
  ocr_confidence_avg: number;
  warnings: string[];
}

export interface AggregateStats {
  total: number;
  by_pressure_class: Record<PressureClassValue, number>;
  by_confidence: Record<Confidence, number>;
}

export interface SampleDrawing {
  name: string;
  size_bytes: number;
}

export interface DrawingResult {
  drawing_id: string;
  width_px: number;
  height_px: number;
  /**
   * Inline data URL for the downsampled display raster. Detection coords
   * remain in original-resolution (`width_px`/`height_px`) space.
   */
  display_image_data_url: string;
  quality: Quality;
  segments: Segment[];
  aggregate: AggregateStats;
  errors: string[];
}
