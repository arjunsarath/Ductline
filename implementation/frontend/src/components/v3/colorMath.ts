/**
 * Client-side color math matching OpenCV's HSV conventions.
 *
 * OpenCV uses an 8-bit HSV space:
 *   • Hue       0..180  (degrees / 2 — half the conventional 0..360 range)
 *   • Saturation 0..255
 *   • Value     0..255
 *
 * The backend's ``cv2.inRange`` runs in this same space, so the picks we
 * post must be expressed in 0..180 / 0..255 not 0..360 / 0..100. All
 * conversions in this file use OpenCV semantics directly.
 */

export interface RGB {
  r: number;
  g: number;
  b: number;
}

export interface HSV {
  h: number; // 0..180 (OpenCV)
  s: number; // 0..255
  v: number; // 0..255
}

export function rgbToHsv({ r, g, b }: RGB): HSV {
  const r1 = r / 255;
  const g1 = g / 255;
  const b1 = b / 255;
  const max = Math.max(r1, g1, b1);
  const min = Math.min(r1, g1, b1);
  const d = max - min;
  let h = 0;
  if (d > 1e-6) {
    if (max === r1) h = ((g1 - b1) / d) % 6;
    else if (max === g1) h = (b1 - r1) / d + 2;
    else h = (r1 - g1) / d + 4;
    h *= 60; // 0..360
    if (h < 0) h += 360;
  }
  // OpenCV scales hue to 0..180.
  const hCv = Math.round(h / 2);
  const s = max === 0 ? 0 : Math.round((d / max) * 255);
  const v = Math.round(max * 255);
  return { h: hCv, s, v };
}

/**
 * Default tolerance band for a freshly-picked pixel. Saturation/value
 * floors keep anti-aliased edge pixels and near-black/near-white from
 * sneaking into the mask. Hue tolerance is tighter — duct colors are
 * usually well-separated on the hue wheel.
 */
export function defaultBand(hsv: HSV) {
  const h_lo = clamp(hsv.h - 12, 0, 180);
  const h_hi = clamp(hsv.h + 12, 0, 180);
  const s_lo = clamp(Math.max(60, hsv.s - 80), 0, 255);
  const s_hi = 255;
  const v_lo = clamp(Math.max(60, hsv.v - 80), 0, 255);
  const v_hi = 255;
  return { h_lo, h_hi, s_lo, s_hi, v_lo, v_hi };
}

function clamp(value: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, value));
}

/**
 * SMACNA-ish hue → likely-system mapping. Used to pre-fill the picker's
 * label and kind dropdowns when the user picks a pixel. The user can
 * always override.
 */
export function suggestKind(hsv: HSV): {
  label: string;
  kind: "supply" | "return" | "exhaust" | "outside" | "other";
} {
  // hue is 0..180; map to 0..360 for readability
  const h360 = hsv.h * 2;
  if (h360 < 15 || h360 >= 340) return { label: "Return / Exhaust", kind: "return" };
  if (h360 < 50) return { label: "Outside Air", kind: "outside" };
  if (h360 < 75) return { label: "Exhaust", kind: "exhaust" };
  if (h360 < 175) return { label: "Return Air", kind: "return" };
  if (h360 < 240) return { label: "Supply Air", kind: "supply" };
  if (h360 < 300) return { label: "Supply Air", kind: "supply" };
  return { label: "Other", kind: "other" };
}

/** A non-clashing display color for the overlay tint. Cycles through a
 *  palette of distinguishable hues when the user adds multiple systems. */
export function displayColor(index: number): [number, number, number] {
  // BGR (OpenCV order). Saturated, mid-bright so alpha-blend reads well
  // on a near-white drawing.
  const palette: [number, number, number][] = [
    [255, 96, 24],   // blue-orange
    [56, 178, 0],    // green
    [40, 60, 220],   // red
    [200, 0, 220],   // magenta
    [220, 200, 0],   // teal
    [0, 165, 255],   // amber
  ];
  return palette[index % palette.length];
}
