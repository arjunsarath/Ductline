export type ElementBase = {
  id: string;
  x0: number;
  top: number;
  x1: number;
  bottom: number;
};

export type LineElement = ElementBase & {
  type: "line";
  linewidth: number;
  stroke: string | null;
};

export type RectElement = ElementBase & {
  type: "rect";
  fill: string | null;
  stroke: string | null;
};

export type CurveElement = ElementBase & {
  type: "curve";
  points: [number, number][];
};

/** Axis-aligned or rotated rectangle emitted as a curve by CAD exporters
 *  rather than via the PDF `re` operator. `corners` are the 4 actual rectangle
 *  vertices (top-left origin) — used to draw the polygon for rotated rects
 *  instead of falling back to the axis-aligned bbox. Empty if detection
 *  succeeded via bbox-corner heuristics but no canonical corners were found. */
export type RectCurveElement = ElementBase & {
  type: "rect_curve";
  points: [number, number][];
  corners: [number, number][];
  stroke: string | null;
  fill: string | null;
};

/** A 3-segment axis-aligned U-shape — the visible end-cap of a rectangle
 *  whose middle is occluded by another element drawn on top. */
export type RectPartialElement = ElementBase & {
  type: "rect_partial";
  points: [number, number][];
  stroke: string | null;
  fill: string | null;
};

/** Synthetic rectangle inferred by pairing two opposing rect_partials. Has
 *  no underlying path data — bbox only. */
export type InferredRectElement = ElementBase & {
  type: "inferred_rect";
};

export type CharElement = ElementBase & {
  type: "char";
  text: string;
  fontname: string;
  size: number;
  fill: string | null;
};

export type WordElement = ElementBase & {
  type: "word";
  text: string;
};

export type Element =
  | LineElement
  | RectElement
  | RectCurveElement
  | RectPartialElement
  | InferredRectElement
  | CurveElement
  | CharElement
  | WordElement;

export type ElementType = Element["type"];

export type PageData = {
  page_number: number;
  width: number;
  height: number;
  elements: Element[];
};

export type ExtractResponse = {
  filename: string;
  page_count: number;
  pages: PageData[];
};

// User-facing types: only the rectangle family. Lines/chars/words/curves are
// still emitted by the backend and accessible in `Element`, but they don't
// appear in the filter pane or the element list. inferred_rect is currently
// disabled at the backend (too many false matches in real drawings); the type
// stays in the schema so we can flip it back on without code changes.
export const ELEMENT_TYPES: ElementType[] = [
  "rect",
  "rect_curve",
  "rect_partial",
];

export const TYPE_COLORS: Record<ElementType, { stroke: string; fill: string }> =
  {
    // Lower fill alpha (0.18 → 0.10) so overlapping rectangles compound
    // visibly — stacked rects render darker, making both ends discernible.
    line: { stroke: "#3b82f6", fill: "rgba(59, 130, 246, 0.10)" },
    rect: { stroke: "#22c55e", fill: "rgba(34, 197, 94, 0.10)" },
    rect_curve: { stroke: "#14b8a6", fill: "rgba(20, 184, 166, 0.10)" },
    rect_partial: { stroke: "#f97316", fill: "rgba(249, 115, 22, 0.10)" },
    inferred_rect: { stroke: "#ec4899", fill: "rgba(236, 72, 153, 0.10)" },
    curve: { stroke: "#a855f7", fill: "rgba(168, 85, 247, 0.10)" },
    char: { stroke: "#f59e0b", fill: "rgba(245, 158, 11, 0.10)" },
    word: { stroke: "#ef4444", fill: "rgba(239, 68, 68, 0.10)" },
  };

export const TYPE_LABELS: Record<ElementType, string> = {
  line: "Lines",
  rect: "Rects",
  rect_curve: "Rect curves",
  rect_partial: "Rect partials",
  inferred_rect: "Inferred rects",
  curve: "Curves",
  char: "Chars",
  word: "Words",
};

export function elementText(el: Element): string {
  if (el.type === "char" || el.type === "word") return el.text;
  return `(${el.x0.toFixed(1)}, ${el.top.toFixed(1)}) → (${el.x1.toFixed(1)}, ${el.bottom.toFixed(1)})`;
}

/** Region in PDF points (top-left origin) bound to a page. */
export type CropRegion = {
  page: number;
  x0: number;
  top: number;
  x1: number;
  bottom: number;
};

export type ScaleBBox = { x0: number; top: number; x1: number; bottom: number };

export type WallSegment = { x0: number; top: number; x1: number; bottom: number };

export type WallPair = {
  a: WallSegment;
  b: WallSegment;
  distance_pts: number;
};

export type ScaleCallout = {
  id: string;
  text: string;
  diameter_in: number;
  raw_text: string;
  confidence: number;
  bbox: ScaleBBox;
  enclosing_rect: ScaleBBox | null;
  duct_bbox: ScaleBBox | null;
  drawn_diameter_pts: number | null;
  scale_pts_per_inch: number | null;
  wall_pairs: WallPair[];
};

export type ScaleResponse = {
  page_number: number;
  dpi: number;
  callouts: ScaleCallout[];
  drawing_scale_pts_per_inch: number | null;
  callout_count: number;
};

/** Translate a bbox by (dx, dy). Used to map original-PDF coords into the
 *  crop-local space of the preprocessed debug PDF. */
export function shiftBBox<T extends ScaleBBox>(b: T, dx: number, dy: number): T {
  return { ...b, x0: b.x0 + dx, top: b.top + dy, x1: b.x1 + dx, bottom: b.bottom + dy };
}

export function shiftElement(el: Element, dx: number, dy: number): Element {
  const base = { ...el, x0: el.x0 + dx, top: el.top + dy, x1: el.x1 + dx, bottom: el.bottom + dy };
  if (el.type === "rect_curve") {
    return {
      ...base,
      points: el.points.map(([x, y]) => [x + dx, y + dy] as [number, number]),
      corners: el.corners.map(([x, y]) => [x + dx, y + dy] as [number, number]),
    } as Element;
  }
  if (el.type === "curve" || el.type === "rect_partial") {
    return {
      ...base,
      points: el.points.map(([x, y]) => [x + dx, y + dy] as [number, number]),
    } as Element;
  }
  return base as Element;
}

export function shiftScaleResponse(s: ScaleResponse, dx: number, dy: number): ScaleResponse {
  return {
    ...s,
    callouts: s.callouts.map((c) => ({
      ...c,
      bbox: shiftBBox(c.bbox, dx, dy),
      enclosing_rect: c.enclosing_rect ? shiftBBox(c.enclosing_rect, dx, dy) : null,
      duct_bbox: c.duct_bbox ? shiftBBox(c.duct_bbox, dx, dy) : null,
      wall_pairs: c.wall_pairs.map((p) => ({
        ...p,
        a: shiftBBox(p.a, dx, dy),
        b: shiftBBox(p.b, dx, dy),
      })),
    })),
  };
}

/** Minimum side ratio for a rectangle to plausibly be a duct. Squarer shapes
 *  (title-block cells, scale-bar boxes, equipment glyphs) get dropped. */
export const MIN_RECT_ASPECT = 1.4;

/** The two side lengths (in PDF points) of a rect-family element. For
 *  `rect_curve` with corners we use the rotated sides so a duct drawn at an
 *  angle reports its true dimensions rather than its bounding-box. Returns
 *  null for non-rectangle types. */
export function rectSideLengthsPts(el: Element): { w: number; h: number } | null {
  if (el.type === "rect" || el.type === "rect_partial" || el.type === "inferred_rect") {
    return { w: el.x1 - el.x0, h: el.bottom - el.top };
  }
  if (el.type === "rect_curve") {
    if (el.corners.length === 4) {
      const [a, b, c] = el.corners;
      return {
        w: Math.hypot(a[0] - b[0], a[1] - b[1]),
        h: Math.hypot(b[0] - c[0], b[1] - c[1]),
      };
    }
    return { w: el.x1 - el.x0, h: el.bottom - el.top };
  }
  return null;
}

/** True if a rect/rect_curve is elongated enough to keep. Non-rectangle types
 *  pass through unchanged. */
export function passesRectAspect(el: Element, minRatio = MIN_RECT_ASPECT): boolean {
  if (el.type !== "rect" && el.type !== "rect_curve") return true;
  const sides = rectSideLengthsPts(el);
  if (!sides) return false;
  if (sides.w <= 0 || sides.h <= 0) return false;
  return Math.max(sides.w / sides.h, sides.h / sides.w) >= minRatio;
}

/** Per-element colour (stroke for lines, stroke-or-fill for rects, fill for chars). */
export function elementColor(el: Element): string | null {
  if (el.type === "line") return el.stroke;
  if (el.type === "rect") return el.stroke ?? el.fill;
  if (el.type === "rect_curve") return el.stroke ?? el.fill;
  if (el.type === "rect_partial") return el.stroke ?? el.fill;
  if (el.type === "char") return el.fill;
  return null; // curves/words/inferred_rects don't carry a stored colour
}

/** Aggregate distinct colours across the elements of a page, with counts. */
export function colorSummary(elements: Element[]): {
  color: string;
  count: number;
  byType: Partial<Record<ElementType, number>>;
}[] {
  const map = new Map<string, { count: number; byType: Partial<Record<ElementType, number>> }>();
  for (const el of elements) {
    const c = elementColor(el);
    if (!c) continue;
    const norm = c.toLowerCase();
    let entry = map.get(norm);
    if (!entry) {
      entry = { count: 0, byType: {} };
      map.set(norm, entry);
    }
    entry.count += 1;
    entry.byType[el.type] = (entry.byType[el.type] ?? 0) + 1;
  }
  return [...map.entries()]
    .map(([color, v]) => ({ color, count: v.count, byType: v.byType }))
    .sort((a, b) => b.count - a.count);
}

/** Approximate luminance of a #rrggbb colour in [0,1]. Used to flag "ink-like" colours. */
export function hexLuma(hex: string): number {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return 1;
  const v = parseInt(m[1], 16);
  const r = ((v >> 16) & 0xff) / 255;
  const g = ((v >> 8) & 0xff) / 255;
  const b = (v & 0xff) / 255;
  // Max-channel mirrors backend `_is_black` so the panel agrees with the filter.
  return Math.max(r, g, b);
}

/** Closest common architectural scale to a measured pts/inch value. */
export function formatScale(ptsPerInch: number): { ratio: string; label: string } {
  // 72 PDF pts == 1 paper-inch.  ratio = real-inches per paper-inch.
  const ratio = 72 / ptsPerInch;
  // Common HVAC plan scales.
  const COMMON = [
    { ratio: 192, label: '1/16" = 1\'' },
    { ratio: 96, label: '1/8" = 1\'' },
    { ratio: 64, label: '3/16" = 1\'' },
    { ratio: 48, label: '1/4" = 1\'' },
    { ratio: 32, label: '3/8" = 1\'' },
    { ratio: 24, label: '1/2" = 1\'' },
    { ratio: 16, label: '3/4" = 1\'' },
    { ratio: 12, label: '1" = 1\'' },
  ];
  let best = COMMON[0];
  let bestErr = Math.abs(Math.log(ratio / best.ratio));
  for (const c of COMMON) {
    const err = Math.abs(Math.log(ratio / c.ratio));
    if (err < bestErr) {
      best = c;
      bestErr = err;
    }
  }
  // Tolerance for snapping: within 8% relative.
  if (bestErr < 0.08) return { ratio: `1:${best.ratio}`, label: best.label };
  return { ratio: `1:${ratio.toFixed(1)}`, label: `${ptsPerInch.toFixed(2)} pts/in` };
}

