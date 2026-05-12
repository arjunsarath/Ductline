"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Bug,
  ChevronLeft,
  ChevronRight,
  Maximize2,
  Minus,
  Plus,
  Ruler,
  Tags,
  Target,
} from "lucide-react";
import { toast } from "sonner";
import AppHeader from "@/components/app-header";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import type { PdfRenderInfo } from "@/components/pdf-page";
import ElementOverlay from "@/components/element-overlay";
import ColorDebugPanel from "@/components/color-debug-panel";
import {
  ELEMENT_TYPES,
  TYPE_COLORS,
  TYPE_LABELS,
  elementColor,
  elementText,
  formatScale,
  hexLuma,
  passesRectAspect,
  shiftElement,
  shiftScaleResponse,
  type CropRegion,
  type Element,
  type ElementType,
  type ExtractResponse,
  type ScaleResponse,
} from "@/lib/extract";
import { cn } from "@/lib/utils";

type Props = {
  data: ExtractResponse;
  file: File;
  regions: CropRegion[];
  onReset: () => void;
};

const SCALE_API_URL =
  process.env.NEXT_PUBLIC_SCALE_API_URL ?? "http://localhost:8000/api/detect-scale";
const PREPROCESS_API_URL =
  process.env.NEXT_PUBLIC_PREPROCESS_API_URL ?? "http://localhost:8000/api/preprocess";

type Transform = { scale: number; tx: number; ty: number };

const MIN_SCALE = 0.1;
const MAX_SCALE = 20;
const ZOOM_STEP = 1.2;
// Cap on rasterised canvas width in CSS px. Beyond this we fall back to CSS
// scaling (pixelated) instead of re-rendering — pdfjs OOMs Chrome on dense
// vector PDFs above ~3000px wide.
const MAX_RASTER_WIDTH = 3000;
// Debounce window before re-rasterising at a new zoom level — keeps the wheel
// smooth and avoids stacking pdfjs render jobs.
const RASTER_DEBOUNCE_MS = 220;

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

export default function Viewer({ data, file, regions, onReset }: Props) {
  const [scaleByPage, setScaleByPage] = useState<Record<number, ScaleResponse>>({});
  const [detectingScale, setDetectingScale] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const [blackThreshold, setBlackThreshold] = useState(0.05);
  const [highlightedColor, setHighlightedColor] = useState<string | null>(null);
  const [preprocessedSvg, setPreprocessedSvg] = useState<string | null>(null);
  const [pageIdx, setPageIdx] = useState(0);
  // Non-rectangle types (line/char/word/curve) are not user-visible — the
  // filter pane only surfaces the rectangle family. inferred_rect stays
  // disabled by default while the pairing heuristic is being re-evaluated.
  const [enabled, setEnabled] = useState<Record<ElementType, boolean>>({
    line: false,
    rect: true,
    rect_curve: true,
    rect_partial: true,
    inferred_rect: false,
    curve: false,
    char: false,
    word: false,
  });
  const [showLabels, setShowLabels] = useState(false);
  const [search, setSearch] = useState("");
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [transform, setTransform] = useState<Transform>({ scale: 1, tx: 0, ty: 0 });
  const [pageWidthCss, setPageWidthCss] = useState(900);
  // The width we ask pdfjs to rasterise at. Tracks zoom (capped) but updates
  // only after user motion settles, so wheel/pan stays jitter-free.
  const [rasterWidth, setRasterWidth] = useState(900);
  const [animating, setAnimating] = useState(false);

  const stageRef = useRef<HTMLDivElement>(null);

  const currentPage = data.pages[pageIdx];
  const currentScale = currentPage
    ? scaleByPage[currentPage.page_number] ?? null
    : null;

  // The preprocessed PDF lives in crop-local coordinates. We translate every
  // overlay element/callout by -cropOrigin so they line up with the rendered
  // debug PDF instead of the original page.
  const cropRegion = useMemo(
    () => (currentPage ? regions.find((r) => r.page === currentPage.page_number) ?? null : null),
    [regions, currentPage],
  );
  const cropX0 = cropRegion?.x0 ?? 0;
  const cropTop = cropRegion?.top ?? 0;
  const displayPageW = cropRegion ? cropRegion.x1 - cropRegion.x0 : currentPage?.width ?? 0;
  const displayPageH = cropRegion ? cropRegion.bottom - cropRegion.top : currentPage?.height ?? 0;

  const counts = useMemo(() => {
    const c: Record<ElementType, number> = {
      line: 0,
      rect: 0,
      rect_curve: 0,
      rect_partial: 0,
      inferred_rect: 0,
      curve: 0,
      char: 0,
      word: 0,
    };
    if (!currentPage) return c;
    // Always apply the black threshold so the counts reflect what the backend
    // actually processes. Uncoloured elements (no stroke/fill metadata) pass
    // through — pdfplumber returns null for the PDF default colour (black).
    for (const el of currentPage.elements) {
      const col = elementColor(el);
      if (col && hexLuma(col) > blackThreshold) continue;
      if (!passesRectAspect(el)) continue;
      c[el.type] += 1;
    }
    return c;
  }, [currentPage, blackThreshold]);

  const visibleElements = useMemo<Element[]>(() => {
    if (!currentPage) return [];
    const q = search.trim().toLowerCase();
    const target = highlightedColor?.toLowerCase() ?? null;
    return currentPage.elements.filter((el) => {
      if (!enabled[el.type]) return false;
      // Threshold filter is always on — it mirrors what the backend rebuilds
      // into the preprocessed PDF the user is looking at.
      const c = elementColor(el);
      if (c && hexLuma(c) > blackThreshold) return false;
      if (!passesRectAspect(el)) return false;
      if (target && (!c || c.toLowerCase() !== target)) return false;
      if (!q) return true;
      if (el.id.toLowerCase().includes(q)) return true;
      if (
        (el.type === "char" || el.type === "word") &&
        el.text.toLowerCase().includes(q)
      ) {
        return true;
      }
      return false;
    });
  }, [currentPage, enabled, search, highlightedColor, blackThreshold]);

  // The SVG view has no async-render step (no canvas), so we derive the
  // render info synchronously instead of waiting for an onRender callback.
  const render = useMemo<PdfRenderInfo | null>(() => {
    if (!preprocessedSvg || !displayPageW || !displayPageH) return null;
    const h = (rasterWidth * displayPageH) / displayPageW;
    return {
      width: rasterWidth,
      height: h,
      pointWidth: displayPageW,
      pointHeight: displayPageH,
    };
  }, [preprocessedSvg, rasterWidth, displayPageW, displayPageH]);

  const baseScale = render && displayPageW ? render.width / displayPageW : 1;

  // Translate elements + scale callouts into the preprocessed-PDF's crop-local
  // coordinate system so the overlays line up.
  const displayElements = useMemo(
    () => visibleElements.map((el) => shiftElement(el, -cropX0, -cropTop)),
    [visibleElements, cropX0, cropTop],
  );
  const displayScale = useMemo(
    () => (currentScale ? shiftScaleResponse(currentScale, -cropX0, -cropTop) : null),
    [currentScale, cropX0, cropTop],
  );
  const displayElementsById = useMemo(() => {
    const m = new Map<string, Element>();
    for (const el of displayElements) m.set(el.id, el);
    return m;
  }, [displayElements]);

  // Fetch the debug preprocessed SVG (cropped + non-black elements stripped).
  // Debounced so the threshold slider doesn't fire a request on every tick.
  useEffect(() => {
    if (!currentPage || !cropRegion) return;
    const t = window.setTimeout(async () => {
      try {
        const form = new FormData();
        form.append("file", file);
        form.append("page_number", String(currentPage.page_number));
        form.append(
          "crop",
          JSON.stringify({
            x0: cropRegion.x0,
            top: cropRegion.top,
            x1: cropRegion.x1,
            bottom: cropRegion.bottom,
          }),
        );
        form.append("black_threshold", String(blackThreshold));
        const res = await fetch(PREPROCESS_API_URL, { method: "POST", body: form });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const svg = await res.text();
        setPreprocessedSvg(svg);
      } catch (e) {
        toast.error(
          "Preprocess failed",
          { description: e instanceof Error ? e.message : "" },
        );
      }
    }, 250);
    return () => window.clearTimeout(t);
  }, [currentPage, cropRegion, file, blackThreshold]);


  // ----- Zoom / pan helpers -----

  const fitToView = useCallback(() => {
    setTransform({ scale: 1, tx: 0, ty: 0 });
  }, []);

  const actualSize = useCallback(() => {
    // "100%" — i.e. the rendered PDF size matches the natural CSS size of the
    // <Page>. Because the page is rendered at pageWidthCss, the "actual" target
    // is just scale=1 (CSS px = render px). Same as fit.
    fitToView();
  }, [fitToView]);

  const zoomAtPointer = useCallback(
    (clientX: number, clientY: number, factor: number) => {
      const stage = stageRef.current;
      if (!stage) return;
      const rect = stage.getBoundingClientRect();
      const px = clientX - rect.left;
      const py = clientY - rect.top;
      setTransform((prev) => {
        const newScale = clamp(prev.scale * factor, MIN_SCALE, MAX_SCALE);
        if (newScale === prev.scale) return prev;
        // Anchor at pointer: the world point under the cursor stays under it.
        const ratio = newScale / prev.scale;
        return {
          scale: newScale,
          tx: px - (px - prev.tx) * ratio,
          ty: py - (py - prev.ty) * ratio,
        };
      });
    },
    [],
  );

  const zoomCentered = useCallback((factor: number) => {
    const stage = stageRef.current;
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    setTransform((prev) => {
      const newScale = clamp(prev.scale * factor, MIN_SCALE, MAX_SCALE);
      if (newScale === prev.scale) return prev;
      const px = rect.width / 2;
      const py = rect.height / 2;
      const ratio = newScale / prev.scale;
      return {
        scale: newScale,
        tx: px - (px - prev.tx) * ratio,
        ty: py - (py - prev.ty) * ratio,
      };
    });
  }, []);

  // Wheel zoom — non-passive so we can preventDefault and stop the page from scrolling.
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const factor = Math.exp(-e.deltaY * 0.0015);
      zoomAtPointer(e.clientX, e.clientY, factor);
    };
    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
  }, [zoomAtPointer]);

  // Pan: drag empty space, or spacebar + drag.
  const panRef = useRef<{ startX: number; startY: number; tx: number; ty: number } | null>(null);
  const spaceHeldRef = useRef(false);
  const [spaceHeld, setSpaceHeld] = useState(false);
  const [panning, setPanning] = useState(false);

  useEffect(() => {
    const onDown = (e: KeyboardEvent) => {
      if (e.code === "Space" && !spaceHeldRef.current) {
        // Don't hijack typing in inputs.
        const t = e.target as HTMLElement | null;
        if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable))
          return;
        spaceHeldRef.current = true;
        setSpaceHeld(true);
        e.preventDefault();
      }
    };
    const onUp = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        spaceHeldRef.current = false;
        setSpaceHeld(false);
      }
    };
    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    return () => {
      window.removeEventListener("keydown", onDown);
      window.removeEventListener("keyup", onUp);
    };
  }, []);

  const onStagePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const target = e.target as HTMLElement;
      // Allow click-through on the SVG overlay (it's pointer-events:none) — only
      // empty stage background or space-held drag should pan. The PDF canvas
      // itself doesn't need to capture; we just pan on any non-button pointer.
      if (target.closest("button, input, [data-role='no-pan']")) return;
      panRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        tx: transform.tx,
        ty: transform.ty,
      };
      e.currentTarget.setPointerCapture(e.pointerId);
      setPanning(true);
    },
    [transform.tx, transform.ty],
  );

  const onStagePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const start = panRef.current;
      if (!start) return;
      setTransform((prev) => ({
        ...prev,
        tx: start.tx + (e.clientX - start.startX),
        ty: start.ty + (e.clientY - start.startY),
      }));
    },
    [],
  );

  const endPan = useCallback(() => {
    panRef.current = null;
    setPanning(false);
  }, []);

  // Keyboard shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable))
        return;
      if (e.key === "+" || e.key === "=") {
        e.preventDefault();
        zoomCentered(ZOOM_STEP);
      } else if (e.key === "-" || e.key === "_") {
        e.preventDefault();
        zoomCentered(1 / ZOOM_STEP);
      } else if (e.key === "0") {
        e.preventDefault();
        fitToView();
      } else if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        fitToView();
      } else if (
        e.key === "ArrowUp" ||
        e.key === "ArrowDown" ||
        e.key === "ArrowLeft" ||
        e.key === "ArrowRight"
      ) {
        e.preventDefault();
        const step = 40;
        setTransform((prev) => ({
          ...prev,
          tx:
            prev.tx +
            (e.key === "ArrowLeft" ? step : e.key === "ArrowRight" ? -step : 0),
          ty:
            prev.ty +
            (e.key === "ArrowUp" ? step : e.key === "ArrowDown" ? -step : 0),
        }));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomCentered, fitToView]);

  // Reset transform when changing pages. React's documented pattern for
  // "adjusting state on prop change" — store the previous trigger value in
  // state, compare during render, and update both atomically.
  const [transformPageIdx, setTransformPageIdx] = useState(pageIdx);
  if (transformPageIdx !== pageIdx) {
    setTransformPageIdx(pageIdx);
    setTransform({ scale: 1, tx: 0, ty: 0 });
  }

  // Fit the page width to available space. Capped at 900 — see cropper for
  // the same OOM rationale; zoom handles detailed inspection.
  useLayoutEffect(() => {
    const compute = () => {
      const stage = stageRef.current;
      if (!stage) return;
      const w = Math.min(900, stage.clientWidth - 80);
      setPageWidthCss(Math.max(480, w));
    };
    compute();
    const obs = new ResizeObserver(compute);
    if (stageRef.current) obs.observe(stageRef.current);
    return () => obs.disconnect();
  }, []);

  // Re-rasterise the PDF at a higher resolution when the user zooms in, so
  // pixels stay crisp instead of upscaled. Debounced so the wheel feels smooth.
  useEffect(() => {
    const target = clamp(pageWidthCss * transform.scale, pageWidthCss, MAX_RASTER_WIDTH);
    if (Math.abs(target - rasterWidth) < 1) return;
    const t = window.setTimeout(() => setRasterWidth(target), RASTER_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [transform.scale, pageWidthCss, rasterWidth]);

  // CSS scale to apply on top of the raster. Equal to transform.scale when the
  // raster matches the requested zoom; bigger when zoom exceeds the raster cap
  // (graceful fallback to pixelated zoom beyond MAX_RASTER_WIDTH).
  const visualScale = (pageWidthCss * transform.scale) / rasterWidth;

  // Focus an element: pan + zoom so it sits at the center of the stage at a
  // comfortable scale. Uses a brief CSS transition.
  const focusElement = useCallback(
    (id: string) => {
      const el = displayElementsById.get(id);
      const stage = stageRef.current;
      if (!el || !stage || !displayPageW) return;
      const sx = pageWidthCss / displayPageW;
      const cx = ((el.x0 + el.x1) / 2) * sx;
      const cy = ((el.top + el.bottom) / 2) * sx;
      const w = Math.max(20, (el.x1 - el.x0) * sx);
      const h = Math.max(20, (el.bottom - el.top) * sx);
      const stageW = stage.clientWidth;
      const stageH = stage.clientHeight;
      // Target scale: keep element under ~30% of viewport, clamped.
      const targetScale = clamp(
        Math.min((stageW * 0.4) / w, (stageH * 0.4) / h, 6),
        1.5,
        12,
      );
      const tx = stageW / 2 - cx * targetScale;
      const ty = stageH / 2 - cy * targetScale;
      setAnimating(true);
      setTransform({ scale: targetScale, tx, ty });
      window.setTimeout(() => setAnimating(false), 220);
    },
    [displayElementsById, displayPageW, pageWidthCss],
  );

  const onSelectFromList = useCallback(
    (id: string) => {
      setHighlightedId((prev) => (prev === id ? null : id));
      focusElement(id);
    },
    [focusElement],
  );

  const detectScale = useCallback(async () => {
    if (!currentPage) return;
    const region = regions.find((r) => r.page === currentPage.page_number);
    if (!region) {
      toast.error("No crop region defined for this page.");
      return;
    }
    setDetectingScale(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("page_number", String(currentPage.page_number));
      form.append(
        "crop",
        JSON.stringify({ x0: region.x0, top: region.top, x1: region.x1, bottom: region.bottom }),
      );
      form.append("black_threshold", String(blackThreshold));
      const res = await fetch(SCALE_API_URL, { method: "POST", body: form });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 200)}` : ""}`);
      }
      const result = (await res.json()) as ScaleResponse;
      setScaleByPage((prev) => ({ ...prev, [currentPage.page_number]: result }));
      if (result.callout_count === 0) {
        toast.warning("No diameter callouts detected in this region.");
      } else if (result.drawing_scale_pts_per_inch == null) {
        toast.warning(
          `Found ${result.callout_count} callout${result.callout_count === 1 ? "" : "s"} but couldn't infer duct geometry.`,
        );
      } else {
        toast.success(
          `Detected ${result.callout_count} callout${result.callout_count === 1 ? "" : "s"} · scale ≈ ${formatScale(result.drawing_scale_pts_per_inch).ratio}`,
        );
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Detection failed.";
      toast.error("Scale detection failed", { description: msg });
    } finally {
      setDetectingScale(false);
    }
  }, [currentPage, regions, file, blackThreshold]);

  const cursor = panning ? "grabbing" : spaceHeld ? "grab" : "default";
  const zoomPct = Math.round(transform.scale * 100);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <AppHeader
        filename={data.filename}
        onReset={onReset}
        meta={
          <>
            {data.page_count > 1 && (
              <div className="flex items-center gap-1 rounded-md border border-border/60 bg-card/60 p-0.5">
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => setPageIdx((i) => Math.max(0, i - 1))}
                  disabled={pageIdx === 0}
                  aria-label="Previous page"
                >
                  <ChevronLeft />
                </Button>
                <span className="px-1 text-[12px] tabular-nums">
                  {pageIdx + 1} / {data.page_count}
                </span>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={() =>
                    setPageIdx((i) => Math.min(data.pages.length - 1, i + 1))
                  }
                  disabled={pageIdx >= data.pages.length - 1}
                  aria-label="Next page"
                >
                  <ChevronRight />
                </Button>
              </div>
            )}
            <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
              {zoomPct}%
            </span>
            <ScalePill
              scale={currentScale}
              loading={detectingScale}
              onRun={detectScale}
            />
            <Button
              variant={debugOpen ? "default" : "outline"}
              size="sm"
              onClick={() => setDebugOpen((v) => !v)}
              aria-label="Colour debug panel"
              title="Colour debug (temporary)"
            >
              <Bug />
            </Button>
          </>
        }
      />

      <main className="flex min-h-0 flex-1">
        <FiltersPane
          counts={counts}
          enabled={enabled}
          setEnabled={setEnabled}
          showLabels={showLabels}
          setShowLabels={setShowLabels}
          search={search}
          setSearch={setSearch}
        />

        <section
          ref={stageRef}
          data-role="no-pan-bg"
          className={cn(
            "relative min-w-0 flex-1 overflow-hidden bg-[radial-gradient(circle_at_center,oklch(0.97_0_0)_0%,oklch(0.93_0.003_260)_100%)]",
          )}
          style={{ cursor }}
          onPointerDown={onStagePointerDown}
          onPointerMove={onStagePointerMove}
          onPointerUp={endPan}
          onPointerCancel={endPan}
        >
          {/* Transform layer — the PDF + SVG overlay scale together. The PDF is
              raster-rendered at `rasterWidth` (CSS px); `visualScale` brings it
              back to the user-visible size after the user-zoom is applied. */}
          <div
            className="absolute left-0 top-0 origin-top-left"
            style={{
              transform: `translate(${transform.tx}px, ${transform.ty}px) scale(${visualScale})`,
              transition: animating ? "transform 220ms ease-out" : "none",
              willChange: "transform",
            }}
          >
            {currentPage && preprocessedSvg && render ? (
              <div
                className="relative bg-white shadow-2xl ring-1 ring-border/30"
                style={{ width: render.width, height: render.height }}
              >
                <div
                  // The arbitrary-variant selectors force the SVG inside to
                  // fill the container — otherwise `width="100%"` on the SVG
                  // sometimes resolves to the intrinsic viewBox size.
                  className="absolute inset-0 [&>svg]:block [&>svg]:h-full [&>svg]:w-full"
                  // Trusted: the SVG comes from our own backend, generated by
                  // PyMuPDF + a colour-filter post-pass — no user-supplied HTML.
                  dangerouslySetInnerHTML={{ __html: preprocessedSvg }}
                />
                <ElementOverlay
                  elements={displayElements}
                  scale={baseScale}
                  pageWidth={render.width}
                  pageHeight={render.height}
                  showLabels={showLabels}
                  highlightedId={highlightedId}
                  hoveredId={hoveredId}
                />
                {displayScale && (
                  <CalloutOverlay
                    scale={baseScale}
                    pageWidth={render.width}
                    pageHeight={render.height}
                    result={displayScale}
                  />
                )}
              </div>
            ) : (
              <div
                className="flex items-center justify-center rounded-md border border-border bg-card text-sm text-muted-foreground"
                style={{ width: rasterWidth, height: 200 }}
              >
                {preprocessedSvg ? "Preparing…" : "Generating preprocessed view…"}
              </div>
            )}
          </div>

          <ZoomToolbar
            scale={transform.scale}
            onZoomIn={() => zoomCentered(ZOOM_STEP)}
            onZoomOut={() => zoomCentered(1 / ZOOM_STEP)}
            onFit={fitToView}
            onActual={actualSize}
          />

          {spaceHeld && (
            <div className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-full bg-card/90 px-3 py-1 text-[11px] uppercase tracking-[0.14em] text-muted-foreground backdrop-blur">
              Pan mode
            </div>
          )}
        </section>

        <ElementList
          elements={visibleElements}
          highlightedId={highlightedId}
          onHover={setHoveredId}
          onSelect={onSelectFromList}
        />

        {debugOpen && currentPage && (
          <ColorDebugPanel
            elements={currentPage.elements}
            threshold={blackThreshold}
            setThreshold={setBlackThreshold}
            highlightedColor={highlightedColor}
            setHighlightedColor={setHighlightedColor}
            onProceed={() => {
              setHighlightedColor(null);
              setDebugOpen(false);
            }}
          />
        )}
      </main>
    </div>
  );
}

function FiltersPane({
  counts,
  enabled,
  setEnabled,
  showLabels,
  setShowLabels,
  search,
  setSearch,
}: {
  counts: Record<ElementType, number>;
  enabled: Record<ElementType, boolean>;
  setEnabled: React.Dispatch<React.SetStateAction<Record<ElementType, boolean>>>;
  showLabels: boolean;
  setShowLabels: (v: boolean) => void;
  search: string;
  setSearch: (s: string) => void;
}) {
  const total = ELEMENT_TYPES.reduce((acc, t) => acc + counts[t], 0);
  return (
    <aside
      data-role="no-pan"
      className="flex w-[260px] shrink-0 flex-col gap-5 border-r border-border/60 bg-card/30 p-4"
    >
      <section className="space-y-2">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Search
        </p>
        <Input
          placeholder="ID or text…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </section>

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Element types
          </p>
          <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
            {total}
          </span>
        </div>
        <ul className="space-y-0.5 rounded-lg border border-border/60 bg-background/30 p-1.5">
          {ELEMENT_TYPES.map((t) => {
            const on = enabled[t];
            return (
              <li key={t}>
                <label
                  htmlFor={`t-${t}`}
                  className={cn(
                    "flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-[12.5px] transition-colors hover:bg-muted/60",
                    !on && "opacity-55",
                  )}
                >
                  <Checkbox
                    id={`t-${t}`}
                    checked={on}
                    onCheckedChange={(v) =>
                      setEnabled((prev) => ({ ...prev, [t]: v === true }))
                    }
                  />
                  <span
                    className="inline-block size-2.5 rounded-[3px] ring-1 ring-inset"
                    style={{
                      background: TYPE_COLORS[t].fill,
                      boxShadow: `inset 0 0 0 1px ${TYPE_COLORS[t].stroke}`,
                    }}
                  />
                  <span className="flex-1">{TYPE_LABELS[t]}</span>
                  <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
                    {counts[t]}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="space-y-2">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Display
        </p>
        <label
          htmlFor="labels"
          className="flex cursor-pointer items-center gap-2.5 rounded-md border border-border/60 bg-background/30 px-2.5 py-2 text-[12.5px]"
        >
          <Checkbox
            id="labels"
            checked={showLabels}
            onCheckedChange={(v) => setShowLabels(v === true)}
          />
          <Tags className="size-3.5 text-muted-foreground" />
          <span className="flex-1">Show element IDs</span>
        </label>
      </section>
    </aside>
  );
}

function ZoomToolbar({
  scale,
  onZoomIn,
  onZoomOut,
  onFit,
  onActual,
}: {
  scale: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  onActual: () => void;
}) {
  return (
    <div
      data-role="no-pan"
      className="pointer-events-auto absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-1 rounded-full border border-border/60 bg-card/85 p-1 shadow-lg backdrop-blur"
      onPointerDown={(e) => e.stopPropagation()}
    >
      <Button variant="ghost" size="icon-sm" onClick={onZoomOut} aria-label="Zoom out">
        <Minus />
      </Button>
      <span className="min-w-[3.25rem] text-center font-mono text-[11px] tabular-nums text-foreground/85">
        {Math.round(scale * 100)}%
      </span>
      <Button variant="ghost" size="icon-sm" onClick={onZoomIn} aria-label="Zoom in">
        <Plus />
      </Button>
      <div className="mx-0.5 h-4 w-px bg-border/70" />
      <Button
        variant="ghost"
        size="sm"
        onClick={onFit}
        aria-label="Fit to view"
        title="Fit (F or 0)"
      >
        <Maximize2 />
        Fit
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={onActual}
        aria-label="Actual size"
        title="100%"
      >
        100%
      </Button>
    </div>
  );
}

function ScalePill({
  scale,
  loading,
  onRun,
}: {
  scale: ScaleResponse | null;
  loading: boolean;
  onRun: () => void;
}) {
  if (loading) {
    return (
      <span className="flex items-center gap-1.5 rounded-md border border-border/60 bg-card/60 px-2 py-1 text-[11px] text-muted-foreground">
        <Ruler className="size-3" />
        Detecting…
      </span>
    );
  }
  if (!scale) {
    return (
      <Button variant="outline" size="sm" onClick={onRun}>
        <Ruler />
        Detect scale
      </Button>
    );
  }
  const pts = scale.drawing_scale_pts_per_inch;
  const formatted = pts != null ? formatScale(pts) : null;
  return (
    <button
      type="button"
      onClick={onRun}
      title="Re-run scale detection"
      className="flex items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-2 py-1 text-[11px] hover:bg-primary/15"
    >
      <Ruler className="size-3 text-primary" />
      <span className="font-mono tabular-nums text-foreground/90">
        {formatted ? formatted.ratio : "scale unknown"}
      </span>
      {formatted && formatted.label !== `${pts!.toFixed(2)} pts/in` && (
        <span className="text-muted-foreground">· {formatted.label}</span>
      )}
      <span className="text-muted-foreground">
        · {scale.callout_count} callout{scale.callout_count === 1 ? "" : "s"}
      </span>
    </button>
  );
}

function CalloutOverlay({
  scale,
  pageWidth,
  pageHeight,
  result,
}: {
  scale: number;
  pageWidth: number;
  pageHeight: number;
  result: ScaleResponse;
}) {
  const stroke = "#f97316"; // orange — distinct from the element-type palette
  const wallStroke = "#0ea5e9"; // cyan — the wall pairs used for the distance median
  return (
    <svg
      className="pointer-events-none absolute left-0 top-0"
      width={pageWidth}
      height={pageHeight}
      viewBox={`0 0 ${pageWidth} ${pageHeight}`}
    >
      {result.callouts.map((c) => {
        const x = c.bbox.x0 * scale;
        const y = c.bbox.top * scale;
        const w = Math.max(2, (c.bbox.x1 - c.bbox.x0) * scale);
        const h = Math.max(2, (c.bbox.bottom - c.bbox.top) * scale);
        const hasWalls = c.wall_pairs.length > 0;
        const groupOpacity = hasWalls ? 1 : 0.45;
        const rect = c.enclosing_rect;
        return (
          <g key={c.id} opacity={groupOpacity}>
            {rect && (
              <rect
                x={rect.x0 * scale}
                y={rect.top * scale}
                width={Math.max(2, (rect.x1 - rect.x0) * scale)}
                height={Math.max(2, (rect.bottom - rect.top) * scale)}
                fill="none"
                stroke={stroke}
                strokeWidth={1.5}
                strokeDasharray="2 2"
                strokeOpacity={0.6}
              />
            )}
            {c.wall_pairs.map((p, idx) => {
              // Each pair: highlight both lines, draw a measurement segment
              // between their midpoints with the gap in inches/pts.
              const ax0 = p.a.x0 * scale;
              const ay0 = p.a.top * scale;
              const ax1 = p.a.x1 * scale;
              const ay1 = p.a.bottom * scale;
              const bx0 = p.b.x0 * scale;
              const by0 = p.b.top * scale;
              const bx1 = p.b.x1 * scale;
              const by1 = p.b.bottom * scale;
              const amx = (ax0 + ax1) / 2;
              const amy = (ay0 + ay1) / 2;
              const bmx = (bx0 + bx1) / 2;
              const bmy = (by0 + by1) / 2;
              const labelX = (amx + bmx) / 2;
              const labelY = (amy + bmy) / 2;
              return (
                <g key={`${c.id}-pair-${idx}`}>
                  <line
                    x1={ax0}
                    y1={ay0}
                    x2={ax1}
                    y2={ay1}
                    stroke={wallStroke}
                    strokeWidth={2}
                    strokeOpacity={0.9}
                  />
                  <line
                    x1={bx0}
                    y1={by0}
                    x2={bx1}
                    y2={by1}
                    stroke={wallStroke}
                    strokeWidth={2}
                    strokeOpacity={0.9}
                  />
                  <line
                    x1={amx}
                    y1={amy}
                    x2={bmx}
                    y2={bmy}
                    stroke={wallStroke}
                    strokeWidth={1}
                    strokeDasharray="3 2"
                    strokeOpacity={0.7}
                  />
                  <text
                    x={labelX}
                    y={labelY}
                    fontSize={9}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    fill={wallStroke}
                    fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                    style={{ paintOrder: "stroke", stroke: "white", strokeWidth: 2.5 }}
                  >
                    {p.distance_pts.toFixed(1)}
                  </text>
                </g>
              );
            })}
            <rect
              x={x - 2}
              y={y - 2}
              width={w + 4}
              height={h + 4}
              fill="none"
              stroke={stroke}
              strokeWidth={2}
            />
            <text
              x={x + w / 2}
              y={y - 6}
              fontSize={11}
              textAnchor="middle"
              fill={stroke}
              fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              style={{ paintOrder: "stroke", stroke: "white", strokeWidth: 3 }}
            >
              {c.text}
              {c.drawn_diameter_pts != null && (
                <tspan dx={4} fontSize={9} fill="#0ea5e9">
                  {c.drawn_diameter_pts.toFixed(1)}pt
                </tspan>
              )}
              <tspan dx={4} fontSize={9} fill={stroke} fillOpacity={0.7}>
                {c.confidence}%
              </tspan>
            </text>
          </g>
        );
      })}
    </svg>
  );
}

type ListProps = {
  elements: Element[];
  highlightedId: string | null;
  onHover: (id: string | null) => void;
  onSelect: (id: string) => void;
};

function ElementList({ elements, highlightedId, onHover, onSelect }: ListProps) {
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: elements.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 60,
    overscan: 12,
  });

  return (
    <aside
      data-role="no-pan"
      className="flex w-[320px] shrink-0 flex-col border-l border-border/60 bg-card/30"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border/60 px-3 py-2.5">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Elements
          </p>
          <p className="text-[13px] tabular-nums">
            {elements.length} <span className="text-muted-foreground">visible</span>
          </p>
        </div>
        <Target className="size-3.5 text-muted-foreground/60" />
      </div>
      <div ref={parentRef} className="flex-1 overflow-auto">
        {elements.length === 0 ? (
          <p className="px-3 py-6 text-center text-[12.5px] text-muted-foreground">
            No elements match the current filters.
          </p>
        ) : (
          <div
            style={{ height: virtualizer.getTotalSize(), position: "relative" }}
          >
            {virtualizer.getVirtualItems().map((row) => {
              const el = elements[row.index];
              const isActive = el.id === highlightedId;
              return (
                <button
                  key={el.id}
                  type="button"
                  onMouseEnter={() => onHover(el.id)}
                  onMouseLeave={() => onHover(null)}
                  onClick={() => onSelect(el.id)}
                  className={cn(
                    "absolute left-0 top-0 w-full border-l-2 px-3 py-2 text-left transition-colors",
                    isActive
                      ? "border-l-primary bg-accent/60"
                      : "border-l-transparent hover:bg-muted/50",
                  )}
                  style={{
                    height: row.size,
                    transform: `translateY(${row.start}px)`,
                  }}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[11px] tabular-nums text-foreground/85">
                      {el.id}
                    </span>
                    <Badge
                      variant="outline"
                      className="ml-auto h-4 px-1 py-0 text-[10px] uppercase tracking-wider"
                      style={{
                        borderColor: TYPE_COLORS[el.type].stroke,
                        color: TYPE_COLORS[el.type].stroke,
                      }}
                    >
                      {el.type}
                    </Badge>
                  </div>
                  <p className="mt-1 truncate text-[12px] text-muted-foreground">
                    {elementText(el)}
                  </p>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </aside>
  );
}
