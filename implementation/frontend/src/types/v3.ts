/**
 * TypeScript mirrors of the V3 backend schemas (SOLUTION-DESIGN-V3 §7).
 * Matches app/pipeline/v3/runner.py V3Result + V3Segment + V3SystemSummary.
 */

export type Confidence = "high" | "medium" | "low";
export type PressureValue = "LOW" | "MEDIUM" | "HIGH";
export type PageUnit = "in" | "mm";
export type Pattern = "outline" | "centerline";
export type SystemKind =
  | "supply"
  | "return"
  | "exhaust"
  | "outside"
  | "other";

export interface HSVBand {
  h_lo: number; h_hi: number;
  s_lo: number; s_hi: number;
  v_lo: number; v_hi: number;
}

/** Wire shape posted to /v3/detect. Mirror of api/v3_routes.py PickPayload. */
export interface PickPayload {
  label: string;
  pattern: Pattern;
  kind: SystemKind;
  primary: HSVBand;
  second?: HSVBand;
  /** OpenCV BGR triplet for the overlay tint. */
  display_color_bgr: [number, number, number];
  system_id: string;
}

export interface V3PressureResult {
  value: PressureValue;
  confidence: Confidence;
  source: "extracted" | "estimated:size_only";
  flow_value: number | null;
  flow_unit: "CFM" | "L/s" | null;
  velocity_fpm: number | null;
  material: string;
}

export interface V3Segment {
  id: string;
  system_id: string;
  /** "rectangular" → ``visible_unit × hidden_unit`` (e.g., 15×13 in).
   *  "round" → ``visible_unit`` is the diameter; hidden_unit repeats it. */
  shape: "rectangular" | "round";
  visible_unit: number;
  hidden_unit: number;
  page_unit: PageUnit;
  pixel_width: number;
  chosen_ppu: number;
  delta_pct: number;
  dim_confidence: Confidence;
  dim_source: string;
  /** Which attribution rule fired for this token — see V3 §5.7. */
  rule: "in_mask" | "proximity";
  pressure: V3PressureResult;
  skel_xy: [number, number];
  token_text: string;
}

export interface V3SystemSummary {
  system_id: string;
  label: string;
  pattern: Pattern;
  kind: SystemKind;
  mask_pixels: number;
  filled_pixels: number;
  n_segments: number;
}

export interface V3Calibration {
  ppu: number | null;
  n_pairs: number;
  n_in_band: number;
  band_lo: number | null;
  band_hi: number | null;
}

export interface V3Result {
  drawing_id: string;
  width_px: number;
  height_px: number;
  rotation_applied: number;
  page_unit: PageUnit;
  ppu: number | null;
  target_dpi: number;
  rendered_size: [number, number];
  systems: V3SystemSummary[];
  segments: V3Segment[];
  n_tokens_total: number;
  n_dim_rect_tokens: number;
  n_flow_tokens: number;
  n_attributed_rect: number;
  n_attributed_flow: number;
  calibration: V3Calibration;
  errors: string[];
}

export interface V3Swatch {
  r: number; g: number; b: number;
  count: number;
  /** OpenCV-space HSV (h: 0..180, s/v: 0..255) for tight inRange band
   *  construction without redoing the rgb→hsv math client-side. */
  h: number; s: number; v: number;
  /** Representative on-page pixel coord (rendered-page space). Lets the
   *  picker drop a marker on the canvas at that point so the user can
   *  see *which* on-page color each swatch refers to. */
  sample_x: number;
  sample_y: number;
}

export interface V3RenderResponse {
  drawing_id: string;
  width_px: number;
  height_px: number;
  target_dpi: number;
  rotation_applied: number;
  rendered_png_base64: string;
  smallest_text_height_px_p5: number | null;
  swatches: V3Swatch[];
  errors: string[];
}

export interface V3DetectResponse {
  result: V3Result;
  /** The rendered page used by the pipeline. Frontends layer the
   *  overlay PNG over this — keeping them as separate images lets the
   *  grayscale toggle filter only the page underneath. */
  page_png_base64: string | null;
  /** Transparent RGBA overlay (mask + contours + segment markers). */
  overlay_png_base64: string | null;
  errors: string[];
}
