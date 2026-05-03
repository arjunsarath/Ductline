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
  /** Populated only for reviewer steps (V2 §6.2). */
  iteration?: number;
}

export type ReviewVerdict =
  | "plausible"
  | "implausible"
  | "uncertain"
  | "not_reviewed";

export interface Segment {
  id: string;
  geometry: Geometry;
  dimension: Dimension | null;
  pressure_class: PressureClass;
  reasoning_trace: ReasoningStep[];
  /** Reviewer verdict (V2 §6.2). Optional — backend may not emit until PR-6. */
  review_verdict?: ReviewVerdict;
  /** Number of reviewer refinement iterations (V2 §6.2). */
  review_iterations?: number;
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
  /**
   * Renderer routing (V2 §6.2 / ADR-0007). Backend already emits this today.
   *   "pdf_points" — vector PDF; geometry in PDF points.
   *   "pixels"     — raster (PNG/JPG/raster_pdf); geometry in pixel coords.
   */
  coord_space: "pdf_points" | "pixels";
  /** Page size in PDF points; populated for vector inputs (V2 §6.2).
   *  Reflects the page after any auto-rotation (W/H swap for 90/270). */
  page_size_pt?: [number, number] | null;
  /** CW rotation baked into segment coords. Vector viewers must
   *  re-apply this in PDF.js so the canvas matches the overlay. */
  rotation_applied?: 0 | 90 | 180 | 270;
  /** Forward-compat: full PageLayout/Legend shapes land with PR-3/PR-4. */
  layout?: unknown;
  legend?: unknown;
}
