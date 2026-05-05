/**
 * V3 color-picker view (SOLUTION-DESIGN-V3 §5.4).
 *
 * Workflow:
 *   1. Show the rendered page (returned by /v3/render).
 *   2. A magnifier follows the cursor over the page, showing a zoomed-in
 *      view of the pixels under the crosshair — duct outlines on a CAD
 *      raster are 2–3 px wide, so naked-eye targeting at fit-zoom is
 *      brittle. The magnifier makes precise pixel-pick practical.
 *   3. Click a colored line on the page → adds a system whose HSV band
 *      is centred on the picked pixel's hue. The backend's text-mask
 *      filter (OCR + 90° rotated OCR) then prevents text labels sharing
 *      that hue from showing up as false-positive segments.
 *   4. Each pick can be edited (label, kind, pattern, HSV band) in the
 *      right panel before posting.
 *   5. "Run detection" posts picks to /v3/detect; parent flips to result view.
 *
 * The picks coordinate space is the same pixel space the backend masks
 * over (the rendered_png_base64 is exactly what the pipeline rasterises
 * for HSV inRange), so click-pixel → backend-pixel is 1:1 — no scaling
 * math, no rotation math, even when the source PDF was auto-rotated.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { clamp, cursorInStage, scaleAroundPoint } from "../canvasShared";
import { SCALE_MAX, SCALE_MIN, type Viewport } from "../viewport";
import type {
  PickPayload,
  V3RenderResponse,
} from "../../types/v3";
import {
  defaultBand,
  displayColor,
  rgbToHsv,
  suggestKind,
  type HSV,
} from "./colorMath";

interface Props {
  filename: string;
  render: V3RenderResponse;
  onRun: (picks: PickPayload[]) => void;
  onBack: () => void;
  isRunning: boolean;
  errorMessage?: string;
}

interface DraftPick extends PickPayload {
  picked_xy: [number, number];
  picked_rgb: [number, number, number];
}

const INITIAL_PICKER_VIEWPORT: Viewport = {
  scale: 1,
  tx: 0,
  ty: 0,
  rotationDeg: 0,
};

export function V3PickerView({
  filename,
  render,
  onRun,
  onBack,
  isRunning,
  errorMessage,
}: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const offscreenRef = useRef<HTMLCanvasElement | null>(null);
  const [picks, setPicks] = useState<DraftPick[]>([]);
  const [hoverHsv, setHoverHsv] = useState<HSV | null>(null);
  const [viewport, setViewport] = useState<Viewport>(INITIAL_PICKER_VIEWPORT);
  const [canvasReady, setCanvasReady] = useState(false);
  const [imgLoaded, setImgLoaded] = useState(false);
  /** Cursor over the page → drives the magnifier overlay. ``stage*`` is
   *  the cursor in stage-relative coords (for placing the magnifier);
   *  ``src*`` is the cursor mapped to image-pixel coords (for picking
   *  the right region of the source PNG to enlarge). */
  const [magnifier, setMagnifier] = useState<{
    stageX: number; stageY: number;
    srcX: number; srcY: number;
  } | null>(null);
  /** Transient feedback when a click can't be turned into a pick — most
   *  often because the cursor was on white/black/grey rather than a
   *  colored duct outline. Without this the picker feels broken when the
   *  threshold fires on a different drawing's palette. */
  const [pickError, setPickError] = useState<string | null>(null);

  // Auto-clear pick errors after a few seconds so they don't linger.
  useEffect(() => {
    if (!pickError) return;
    const t = setTimeout(() => setPickError(null), 3500);
    return () => clearTimeout(t);
  }, [pickError]);

  const imageSrc = useMemo(
    () => `data:image/png;base64,${render.rendered_png_base64}`,
    [render.rendered_png_base64],
  );

  // Hydrate the offscreen canvas as soon as the source image decodes.
  // Keyed on imageSrc so a fresh /render swap re-hydrates. Using a
  // dedicated <img> we own (rather than relying on the on-screen img's
  // onLoad) avoids the race where rapid clicks land before the on-screen
  // image's load event fires and end up sampling a zero canvas.
  useEffect(() => {
    setCanvasReady(false);
    const img = new Image();
    img.crossOrigin = "anonymous";
    let cancelled = false;
    img.onload = () => {
      if (cancelled) return;
      const canvas = (offscreenRef.current ??= document.createElement("canvas"));
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) return;
      ctx.drawImage(img, 0, 0);
      setCanvasReady(true);
    };
    img.onerror = () => {
      // eslint-disable-next-line no-console
      console.error("V3PickerView: failed to decode rendered page");
    };
    img.src = imageSrc;
    return () => {
      cancelled = true;
    };
  }, [imageSrc]);

  const samplePixel = useCallback(
    (
      clientX: number,
      clientY: number,
    ): {
      x: number;
      y: number;
      rgb: [number, number, number];
    } | null => {
      const img = imgRef.current;
      const canvas = offscreenRef.current;
      if (!img || !canvas || !canvasReady) return null;
      const rect = img.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return null;
      const x = ((clientX - rect.left) / rect.width) * img.naturalWidth;
      const y = ((clientY - rect.top) / rect.height) * img.naturalHeight;
      if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) return null;
      const ctx = canvas.getContext("2d");
      if (!ctx) return null;
      const data = ctx.getImageData(Math.floor(x), Math.floor(y), 1, 1).data;
      return {
        x: Math.floor(x),
        y: Math.floor(y),
        rgb: [data[0], data[1], data[2]],
      };
    },
    [canvasReady],
  );

  // ── Pan / zoom ─────────────────────────────────────────────────────────
  const dragRef = useRef<{
    startX: number;
    startY: number;
    startTx: number;
    startTy: number;
    moved: boolean;
  } | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    function handleWheel(event: WheelEvent) {
      event.preventDefault();
      const factor = Math.exp(-event.deltaY * 0.0015);
      const newScale = clamp(viewport.scale * factor, SCALE_MIN, SCALE_MAX);
      if (newScale === viewport.scale) return;
      // Cursor coords expressed relative to the stage *center* — the
      // wrap is positioned so (tx=0, ty=0, scale) places its center at
      // the stage center, and the standard scaleAroundPoint formula
      // expects the cursor in the same coord origin as the translation.
      const cursorAbs = cursorInStage(event, stage!);
      const cursor = {
        x: cursorAbs.x - stage!.clientWidth / 2,
        y: cursorAbs.y - stage!.clientHeight / 2,
      };
      setViewport(scaleAroundPoint(viewport, newScale, cursor));
    }
    stage.addEventListener("wheel", handleWheel, { passive: false });
    return () => stage.removeEventListener("wheel", handleWheel);
  }, [viewport]);

  const onStageMouseDown = useCallback(
    (event: React.MouseEvent) => {
      if (event.button !== 0) return;
      dragRef.current = {
        startX: event.clientX,
        startY: event.clientY,
        startTx: viewport.tx,
        startTy: viewport.ty,
        moved: false,
      };
      setIsDragging(true);
    },
    [viewport.tx, viewport.ty],
  );

  useEffect(() => {
    if (!isDragging) return;
    function handleMove(event: MouseEvent) {
      const drag = dragRef.current;
      if (!drag) return;
      const dx = event.clientX - drag.startX;
      const dy = event.clientY - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) > 4) drag.moved = true;
      if (drag.moved) {
        setViewport((v) => ({ ...v, tx: drag.startTx + dx, ty: drag.startTy + dy }));
      }
    }
    function handleUp() {
      dragRef.current = null;
      setIsDragging(false);
    }
    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
  }, [isDragging]);

  // Initial fit-to-stage. Triggered when the on-screen <img> reports
  // load (i.e. naturalWidth is now non-zero). The earlier version keyed
  // off canvasReady which fires on the offscreen-decoder's load — the
  // on-screen img wasn't necessarily mounted yet, leaving the viewport
  // at scale=1 and the user staring at the middle of a 7000px image.
  useEffect(() => {
    if (!imgLoaded) return;
    const stage = stageRef.current;
    const img = imgRef.current;
    if (!stage || !img) return;
    const sw = stage.clientWidth;
    const sh = stage.clientHeight;
    const iw = img.naturalWidth;
    const ih = img.naturalHeight;
    if (!sw || !sh || !iw || !ih) return;
    const margin = 32;
    const fit = Math.min((sw - margin) / iw, (sh - margin) / ih, 1);
    setViewport({ scale: fit, tx: 0, ty: 0, rotationDeg: 0 });
  }, [imgLoaded]);

  // ── Picks ────────────────────────────────────────────────────────────
  const addPick = useCallback(
    (
      hsv: HSV,
      rgb: [number, number, number],
      pickedXY: [number, number],
    ) => {
      const idx = picks.length;
      // Dark-line drawings (e.g. drawing 02 — duct in black, building
      // faded grey) carry no useful hue: the discriminant is brightness.
      // Detect that case at pick time and build a permissive ``V<=ceiling,
      // S any, H any`` band rather than a hue-centred one. The backend's
      // text-mask + area filters then prune callout boxes / labels.
      const isDarkPick = hsv.v < 60;
      const newPick: DraftPick = isDarkPick
        ? {
            label: "Marked duct (dark)",
            pattern: "outline",
            kind: "other",
            primary: darkBand(hsv),
            display_color_bgr: displayColor(idx),
            system_id: `sys_${idx.toString().padStart(2, "0")}`,
            picked_xy: pickedXY,
            picked_rgb: rgb,
          }
        : (() => {
            const suggestion = suggestKind(hsv);
            return {
              label: suggestion.label,
              pattern: "outline",
              kind: suggestion.kind,
              primary: defaultBand(hsv),
              display_color_bgr: displayColor(idx),
              system_id: `sys_${idx.toString().padStart(2, "0")}`,
              picked_xy: pickedXY,
              picked_rgb: rgb,
            };
          })();
      setPicks((cur) => [...cur, newPick]);
    },
    [picks.length],
  );

  const onMouseMove = useCallback(
    (e: React.MouseEvent<HTMLImageElement>) => {
      const stage = stageRef.current;
      const sample = samplePixel(e.clientX, e.clientY);
      if (!sample) {
        setMagnifier(null);
        return;
      }
      const hsv = rgbToHsv({ r: sample.rgb[0], g: sample.rgb[1], b: sample.rgb[2] });
      setHoverHsv(hsv);
      if (stage) {
        const r = stage.getBoundingClientRect();
        setMagnifier({
          stageX: e.clientX - r.left,
          stageY: e.clientY - r.top,
          srcX: sample.x,
          srcY: sample.y,
        });
      }
    },
    [samplePixel],
  );

  const onMouseLeaveImage = useCallback(() => {
    setMagnifier(null);
  }, []);

  const onClickPick = useCallback(
    (e: React.MouseEvent<HTMLImageElement>) => {
      // Suppress the click that fires after a drag-pan.
      if (dragRef.current?.moved) return;
      const sample = samplePixel(e.clientX, e.clientY);
      if (!sample) {
        setPickError("Couldn't sample that pixel — try again over the page.");
        return;
      }
      const hsv = rgbToHsv({ r: sample.rgb[0], g: sample.rgb[1], b: sample.rgb[2] });
      // Reject only clear non-colored pixels. Loose floors so pastel duct
      // outlines (low S) and bold dark blues (low V) still pass; strict
      // upper-V cutoff keeps the white page background out. The rejection
      // surfaces a visible reason so the user isn't left wondering why
      // the click did nothing.
      // Rejection rules — discriminate "page background / faded gridline"
      // from "actual colored or dark duct line":
      //   1. White-ish background:  V > 240 *and* S < 30. (Vivid pure
      //      colors like cyan RGB(0,255,255) are V=255 S=255 — must NOT
      //      be mistaken for white.)
      //   2. Mid-tone faded grey:   V in [60, 240] *and* S < 25. Catches
      //      faded building outlines / gridlines on drawings like 02.
      //   3. Black is *allowed*: drawings using the "fade-everything-else"
      //      convention (drawing 02) draw ducts in black on grey. The
      //      ``isDarkPick`` branch in addPick builds a permissive band.
      const tone = `RGB(${sample.rgb[0]}, ${sample.rgb[1]}, ${sample.rgb[2]}) · S=${hsv.s} V=${hsv.v}`;
      if (hsv.v > 240 && hsv.s < 30) {
        setPickError(`That pixel reads as page background (${tone}). Aim the crosshair at the colored line itself.`);
        return;
      }
      if (hsv.s < 25 && hsv.v >= 60 && hsv.v <= 240) {
        setPickError(`That pixel reads as a faded gridline / grey background (${tone}). Aim the crosshair at the duct line itself.`);
        return;
      }
      setPickError(null);
      addPick(hsv, sample.rgb, [sample.x, sample.y]);
    },
    [samplePixel, addPick],
  );

  const removePick = useCallback((index: number) => {
    setPicks((cur) => cur.filter((_, i) => i !== index));
  }, []);

  const updatePick = useCallback(
    (index: number, patch: Partial<DraftPick>) => {
      setPicks((cur) => cur.map((p, i) => (i === index ? { ...p, ...patch } : p)));
    },
    [],
  );

  const submit = useCallback(() => {
    const payload: PickPayload[] = picks.map(
      ({ picked_xy: _xy, picked_rgb: _rgb, ...rest }) => rest,
    );
    onRun(payload);
  }, [picks, onRun]);

  // The wrap is absolute-positioned at the stage's top-left with
  // transform-origin: 0 0. To centre an unzoomed image and keep
  // ``viewport.tx === 0 / ty === 0`` meaning "centred", we bake the
  // centring offset into the translation: half the stage size minus
  // half the scaled image. ``imgRef.current`` is null on the first
  // render — fall back to 0 so the picker doesn't crash before the
  // <img> mounts; the post-load effect will reset the viewport once
  // dimensions are known.
  const stageSize = {
    w: stageRef.current?.clientWidth ?? 0,
    h: stageRef.current?.clientHeight ?? 0,
  };
  const imgSize = {
    w: imgRef.current?.naturalWidth ?? 0,
    h: imgRef.current?.naturalHeight ?? 0,
  };
  const centerTx = stageSize.w / 2 - (imgSize.w * viewport.scale) / 2;
  const centerTy = stageSize.h / 2 - (imgSize.h * viewport.scale) / 2;
  const transform =
    `translate(${centerTx + viewport.tx}px, ${centerTy + viewport.ty}px) scale(${viewport.scale})`;

  return (
    <main className="picker-view">
      <header className="picker-topbar">
        <div className="brand">Ductline · V3 · Picker</div>
        <div className="picker-meta">
          <span className="topbar-pill">{filename}</span>
          <span className="topbar-pill">
            {render.width_px}×{render.height_px} @ {render.target_dpi} DPI
          </span>
          {render.rotation_applied !== 0 && (
            <span className="topbar-pill">↻ {render.rotation_applied}°</span>
          )}
        </div>
        <div className="picker-actions">
          <button type="button" className="button button-secondary" onClick={onBack}>
            ← Back
          </button>
          <button
            type="button"
            className="button button-primary"
            disabled={picks.length === 0 || isRunning}
            onClick={submit}
          >
            {isRunning
              ? "Running detection…"
              : `Run detection${picks.length ? ` (${picks.length} system${picks.length > 1 ? "s" : ""})` : ""}`}
          </button>
        </div>
      </header>

      <div className="picker-body">
        <section
          ref={stageRef}
          className={`picker-canvas${isDragging ? " is-dragging" : ""}`}
          onMouseDown={onStageMouseDown}
        >
          <div
            className={`picker-instructions${pickError ? " is-error" : ""}`}
          >
            {pickError ? (
              <>
                <strong>Pick rejected:</strong> {pickError}
              </>
            ) : (
              <>
                Click a colored duct line on the page — the magnifier under
                your cursor helps target thin lines precisely.
                {hoverHsv && canvasReady && (
                  <span className="picker-hover">
                    {" "}· Hover HSV: H={hoverHsv.h * 2}° S={hoverHsv.s} V={hoverHsv.v}
                  </span>
                )}
                {!canvasReady && (
                  <span className="picker-hover"> · Loading page…</span>
                )}
              </>
            )}
          </div>
          <div className="picker-image-wrap" style={{ transform }}>
            <img
              ref={imgRef}
              src={imageSrc}
              alt="rendered page"
              className="picker-image"
              onClick={onClickPick}
              onMouseMove={onMouseMove}
              onMouseLeave={onMouseLeaveImage}
              onLoad={() => setImgLoaded(true)}
              draggable={false}
            />
            {picks.map((p, i) => {
              const counter = 1 / Math.max(viewport.scale, 0.05);
              return (
                <div
                  key={`pick-${p.system_id}-${i}`}
                  className="picker-marker"
                  style={{
                    left: `${(p.picked_xy[0] / render.width_px) * 100}%`,
                    top: `${(p.picked_xy[1] / render.height_px) * 100}%`,
                    background: `rgb(${p.picked_rgb[0]}, ${p.picked_rgb[1]}, ${p.picked_rgb[2]})`,
                    transform: `translate(-50%, -50%) scale(${counter})`,
                  }}
                  title={p.label}
                >
                  {i + 1}
                </div>
              );
            })}
          </div>

          {/* Magnifier — sits OUTSIDE the transformed wrap so it stays
           *  constant size + sharp (renders the source PNG with its own
           *  scale factor against natural pixels, independent of the
           *  page's display zoom). pointer-events:none so the underlying
           *  image still receives hover + click events. */}
          {magnifier && imgSize.w > 0 && (
            <div
              className="picker-magnifier"
              style={{
                left: clampMagnifierX(magnifier.stageX, stageSize.w),
                top: clampMagnifierY(magnifier.stageY, stageSize.h),
                backgroundImage: `url(${imageSrc})`,
                backgroundSize: `${imgSize.w * MAG_ZOOM}px ${imgSize.h * MAG_ZOOM}px`,
                backgroundPosition:
                  `${MAG_SIZE / 2 - magnifier.srcX * MAG_ZOOM}px ` +
                  `${MAG_SIZE / 2 - magnifier.srcY * MAG_ZOOM}px`,
              }}
            >
              <span className="picker-magnifier-crosshair-h" />
              <span className="picker-magnifier-crosshair-v" />
            </div>
          )}
          <div className="picker-zoom-controls">
            <button
              type="button"
              className="button button-secondary"
              onClick={() => {
                const stage = stageRef.current;
                if (!stage) return;
                const center = { x: stage.clientWidth / 2, y: stage.clientHeight / 2 };
                setViewport((v) =>
                  scaleAroundPoint(v, clamp(v.scale * 0.85, SCALE_MIN, SCALE_MAX), center),
                );
              }}
            >
              −
            </button>
            <span className="picker-zoom-readout">
              {Math.round(viewport.scale * 100)}%
            </span>
            <button
              type="button"
              className="button button-secondary"
              onClick={() => {
                const stage = stageRef.current;
                if (!stage) return;
                const center = { x: stage.clientWidth / 2, y: stage.clientHeight / 2 };
                setViewport((v) =>
                  scaleAroundPoint(v, clamp(v.scale * 1.18, SCALE_MIN, SCALE_MAX), center),
                );
              }}
            >
              +
            </button>
            <button
              type="button"
              className="button button-secondary"
              onClick={() => {
                const stage = stageRef.current;
                const img = imgRef.current;
                if (!stage || !img) return;
                const fit = Math.min(
                  stage.clientWidth / img.naturalWidth,
                  stage.clientHeight / img.naturalHeight,
                  1,
                );
                setViewport({ scale: fit, tx: 0, ty: 0, rotationDeg: 0 });
              }}
            >
              Fit
            </button>
          </div>
        </section>

        <aside className="picker-panel">
          <h2 className="picker-panel-title">Systems</h2>
          {picks.length === 0 && (
            <div className="picker-empty">
              No systems yet. Hover the page to bring up the magnifier,
              then click a colored duct line.
            </div>
          )}
          {picks.map((p, i) => (
            <PickCard
              key={`${p.system_id}-${i}`}
              index={i}
              pick={p}
              onChange={(patch) => updatePick(i, patch)}
              onRemove={() => removePick(i)}
            />
          ))}
          {errorMessage && (
            <div className="picker-error" role="alert">
              {errorMessage}
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}

// Magnifier — circular zoomed view of the source PNG that follows the
// cursor over the page. The numbers are tuned so a duct outline (2–3 px
// wide on the source raster) is comfortably 12–18 px wide inside the
// magnifier — large enough to target with a click.
const MAG_SIZE = 140;
const MAG_ZOOM = 6;
const MAG_OFFSET = 24; // gap between cursor and magnifier edge

/** Place the magnifier offset down-right of the cursor; flip to the
 *  opposite side when it would overflow the stage. */
function clampMagnifierX(stageX: number, stageW: number): number {
  const right = stageX + MAG_OFFSET;
  if (right + MAG_SIZE <= stageW) return right;
  return stageX - MAG_OFFSET - MAG_SIZE;
}

/**
 * Permissive HSV inRange band for dark-line drawings: any hue, any
 * saturation, V capped at a ceiling above the picked V. Hue is
 * meaningless when the duct is rendered black, so banding around the
 * picked H would unnecessarily exclude near-black pixels with
 * different anti-aliased hues. The text-mask filter in the runner is
 * what keeps callout boxes / labels from polluting detection.
 */
function darkBand(hsv: HSV) {
  const ceiling = Math.max(60, hsv.v + 30);
  return {
    h_lo: 0, h_hi: 180,
    s_lo: 0, s_hi: 255,
    v_lo: 0, v_hi: ceiling,
  };
}

function clampMagnifierY(stageY: number, stageH: number): number {
  const below = stageY + MAG_OFFSET;
  if (below + MAG_SIZE <= stageH) return below;
  return stageY - MAG_OFFSET - MAG_SIZE;
}

function PickCard({
  index,
  pick,
  onChange,
  onRemove,
}: {
  index: number;
  pick: DraftPick;
  onChange: (patch: Partial<DraftPick>) => void;
  onRemove: () => void;
}) {
  return (
    <div className="pick-card">
      <header className="pick-card-head">
        <span
          className="pick-swatch"
          style={{
            background: `rgb(${pick.picked_rgb[0]}, ${pick.picked_rgb[1]}, ${pick.picked_rgb[2]})`,
          }}
        />
        <span className="pick-index">#{index + 1}</span>
        <input
          type="text"
          value={pick.label}
          onChange={(e) => onChange({ label: e.target.value })}
          className="pick-label-input"
        />
        <button
          type="button"
          className="pick-remove"
          onClick={onRemove}
          aria-label="Remove system"
        >
          ✕
        </button>
      </header>
      <div className="pick-row">
        <label>
          Pattern{" "}
          <select
            value={pick.pattern}
            onChange={(e) =>
              onChange({ pattern: e.target.value as "outline" | "centerline" })
            }
          >
            <option value="outline">Outline (closed colored loop)</option>
            <option value="centerline">Centerline (line through duct)</option>
          </select>
        </label>
      </div>
      <div className="pick-row">
        <label>
          Kind{" "}
          <select
            value={pick.kind}
            onChange={(e) =>
              onChange({ kind: e.target.value as PickPayload["kind"] })
            }
          >
            <option value="supply">Supply</option>
            <option value="return">Return</option>
            <option value="exhaust">Exhaust</option>
            <option value="outside">Outside Air</option>
            <option value="other">Other</option>
          </select>
        </label>
      </div>
      <details className="pick-band">
        <summary>HSV tolerance band</summary>
        <BandSliders
          band={pick.primary}
          onChange={(p) => onChange({ primary: { ...pick.primary, ...p } })}
        />
      </details>
    </div>
  );
}

function BandSliders({
  band,
  onChange,
}: {
  band: PickPayload["primary"];
  onChange: (patch: Partial<PickPayload["primary"]>) => void;
}) {
  return (
    <div className="band-sliders">
      <BandRow
        label="H"
        loValue={band.h_lo}
        hiValue={band.h_hi}
        max={180}
        onLo={(v) => onChange({ h_lo: v })}
        onHi={(v) => onChange({ h_hi: v })}
      />
      <BandRow
        label="S"
        loValue={band.s_lo}
        hiValue={band.s_hi}
        max={255}
        onLo={(v) => onChange({ s_lo: v })}
        onHi={(v) => onChange({ s_hi: v })}
      />
      <BandRow
        label="V"
        loValue={band.v_lo}
        hiValue={band.v_hi}
        max={255}
        onLo={(v) => onChange({ v_lo: v })}
        onHi={(v) => onChange({ v_hi: v })}
      />
    </div>
  );
}

function BandRow({
  label,
  loValue,
  hiValue,
  max,
  onLo,
  onHi,
}: {
  label: string;
  loValue: number;
  hiValue: number;
  max: number;
  onLo: (v: number) => void;
  onHi: (v: number) => void;
}) {
  return (
    <div className="band-row">
      <span className="band-label">{label}</span>
      <input
        type="number"
        min={0}
        max={max}
        value={loValue}
        onChange={(e) => onLo(Number(e.target.value))}
      />
      <span className="band-dash">–</span>
      <input
        type="number"
        min={0}
        max={max}
        value={hiValue}
        onChange={(e) => onHi(Number(e.target.value))}
      />
    </div>
  );
}
