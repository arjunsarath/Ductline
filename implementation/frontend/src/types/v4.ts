/**
 * TypeScript mirrors of V4 backend schemas (SOLUTION-DESIGN-V4 §4).
 * Match implementation/backend/app/schemas.py V4Result + dependencies — names
 * must agree exactly so the JSON deserialises without a translation layer.
 */

export type SmacnaClass = "Low" | "Medium" | "High";
export type ScaleSource = "title_block" | "manual";

export interface SmacnaThresholds {
  low_max_in_wc: number;
  medium_max_in_wc: number;
}

export interface VelocityThresholds {
  low_max_fpm: number;
  medium_max_fpm: number;
}

export interface OperationalVars {
  air_density_lb_ft3: number;
  friction_factor: number;
  fitting_k_table: Record<string, number>;
  source_pressure_in_wc: number;
  flex_equiv_length_ft: number;
  smacna_thresholds_in_wc: SmacnaThresholds;
  velocity_thresholds_fpm: VelocityThresholds;
}

export interface ScaleInfo {
  paper_inches_per_foot: number;
  source: ScaleSource;
  confidence: number;
}

export interface CfmRange {
  start: number;
  end: number;
}

export interface PressureResult {
  start_in_wc: number;
  end_in_wc: number;
  smacna_class: SmacnaClass;
  velocity_fpm: number;
}

export interface TerminalRef {
  terminal_id: string;
  distance_along_segment_ft: number;
  cfm: number;
}

export interface V4Terminal {
  id: string;
  center: [number, number];
  radius: number;
  type_letter: string | null;
  cfm: number | null;
}

export interface V4Segment {
  id: string;
  dimension: string;
  length_ft: number;
  cfm_range: CfmRange;
  pressure: PressureResult;
  /** Centerline polyline in page-pixel coordinates. */
  polygon: [number, number][];
  terminals_on_segment: TerminalRef[];
}

export interface PageDims {
  width_px: number;
  height_px: number;
  dpi: number;
  rotation: 0 | 90 | 180 | 270;
}

export type DropReason =
  | "shape_unknown"
  | "diameter_out_of_range"
  | "no_label";

export interface DebugPolygon {
  id: string;
  bbox: [number, number, number, number];
  polygon: [number, number][];
  shape_hint: "round" | "rectangular" | "unknown";
  est_width_px: number;
  est_diameter_in: number | null;
  kept: boolean;
  drop_reason: DropReason | null;
}

export interface V4Debug {
  polygons: DebugPolygon[];
}

export interface V4Result {
  segments: V4Segment[];
  terminals: V4Terminal[];
  scale: ScaleInfo;
  op_vars: OperationalVars;
  page_dims: PageDims;
  warnings: string[];
  debug?: V4Debug | null;
  // Step-debug: when the runner is short-circuited after a stage, the
  // intermediate raster is returned as a base64 PNG data URL.
  stage_image_data_url?: string | null;
  stage_stopped_after?: string | null;
  // Every rectangle contour found on the cleaned raster, tagged with the
  // filter outcome (kept or which filter dropped it).
  debug_rectangles?: DebugRectangle[];
  debug_dimensions?: DebugDimension[];
  debug_ocr?: DebugOcrMatch[];
}

export interface DebugDimension {
  text: string;
  kind: "round" | "rectangular";
  bbox: [number, number, number, number];
}

export interface DebugOcrMatch {
  text: string;
  bbox: [number, number, number, number];
  confidence: number;
  crop_data_url?: string | null;
  source?: "tesseract" | "vlm" | "empty" | null;
  oriented_corners?: [number, number][] | null;
  /** Run length in feet for ducts; null for terminals or un-parsed labels. */
  length_ft?: number | null;
  /** MVP airflow attribution from the single directly-adjacent terminal. */
  cfm?: number | null;
  velocity_fpm?: number | null;
  pressure_drop_in_wc?: number | null;
  smacna_class?: SmacnaClass | null;
  /** Bbox of the directly-adjacent terminal whose CFM was attributed. */
  adjacent_terminal_bbox?: [number, number, number, number] | null;
  /** True when the velocity used for pressure estimation was the fallback. */
  pressure_estimated?: boolean;
}

export type RectDropReason =
  | "oversized"
  | "non_duct_text"
  | "low_aspect_ratio"
  | "interior_not_empty"
  | "not_rectangle"
  | "interior_no_ink"
  | "too_square"
  | "interior_too_full"
  | "not_circle"
  | "no_horizontal_divider"
  | "no_three_digit";

export interface DebugRectangle {
  corners: [number, number][];
  kept: boolean;
  drop_reason: RectDropReason | null;
}

export interface V4ProgressEvent {
  stage: string;
  message: string;
  elapsed_ms: number;
  count?: number;
  dpi?: number;
  segments?: number;
  terminals?: number;
  /** Per-bbox OCR ladder progress: bboxes processed so far. */
  done?: number;
  /** Total bbox count for the OCR ladder. */
  total?: number;
  /** Bboxes kept so far by the 3-digit predicate. */
  kept?: number;
}

/** Default OperationalVars matching backend schema defaults (schemas.py). */
export const DEFAULT_OP_VARS: OperationalVars = {
  air_density_lb_ft3: 0.075,
  friction_factor: 0.02,
  fitting_k_table: {
    elbow: 0.3,
    tee: 0.5,
    y_branch: 0.4,
    transition: 0.15,
    equipment: 0.0,
    terminal: 0.2,
  },
  source_pressure_in_wc: 0.0,
  flex_equiv_length_ft: 5.0,
  smacna_thresholds_in_wc: { low_max_in_wc: 2.0, medium_max_in_wc: 3.0 },
  velocity_thresholds_fpm: { low_max_fpm: 2000.0, medium_max_fpm: 2500.0 },
};
