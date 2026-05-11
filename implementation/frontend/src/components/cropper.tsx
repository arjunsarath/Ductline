"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, ChevronLeft, ChevronRight, Crop, Eraser } from "lucide-react";
import { toast } from "sonner";
import PdfPage, { type PdfRenderInfo } from "@/components/pdf-page";
import AppHeader from "@/components/app-header";
import { Button } from "@/components/ui/button";
import type { CropRegion } from "@/lib/extract";
import { cn } from "@/lib/utils";

type Props = {
  file: File;
  pdfUrl: string;
  onBack: () => void;
  onRun: (regions: CropRegion[]) => void;
  loading: boolean;
};

type Bbox = { x0: number; top: number; x1: number; bottom: number };

// Per-page selection: rectangle in screen px + the scale used when it was
// drawn (px per PDF point). Stored so each page converts independently even
// when pages have different native sizes.
type Region = { rect: Bbox; scale: number };

type DragMode =
  | { kind: "draw"; originX: number; originY: number }
  | { kind: "move"; offsetX: number; offsetY: number }
  | {
      kind: "resize";
      // Anchor point is the opposite corner/edge that stays fixed.
      anchorX: number;
      anchorY: number;
      lockX: boolean;
      lockY: boolean;
    };

type HandleId = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";

const HANDLES: HandleId[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function bboxFromPx(
  a: { x: number; y: number },
  b: { x: number; y: number },
  bounds: { width: number; height: number },
): Bbox {
  const x0 = clamp(Math.min(a.x, b.x), 0, bounds.width);
  const x1 = clamp(Math.max(a.x, b.x), 0, bounds.width);
  const top = clamp(Math.min(a.y, b.y), 0, bounds.height);
  const bottom = clamp(Math.max(a.y, b.y), 0, bounds.height);
  return { x0, top, x1, bottom };
}

export default function Cropper({ file, pdfUrl, onBack, onRun, loading }: Props) {
  const [pageNumber, setPageNumber] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  // Render info is paired with the page it describes so a stale page's info
  // is naturally ignored after a page change without needing a reset effect.
  const [renderState, setRenderState] = useState<{
    page: number;
    info: PdfRenderInfo;
  } | null>(null);
  const render = renderState && renderState.page === pageNumber ? renderState.info : null;
  const [regions, setRegions] = useState<Map<number, Region>>(new Map());
  const [pageWidth, setPageWidth] = useState<number>(720);
  const overlayRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragMode | null>(null);
  const [dragging, setDragging] = useState(false);

  // Fit the PDF to the available horizontal space. Capped at 900px to keep the
  // rasterized canvas small on dense engineering vectors — pdfjs OOMs Chrome
  // tabs above this on real-world A1/A0 drawings.
  useEffect(() => {
    const compute = () => {
      const cap = Math.min(Math.max(window.innerWidth - 360, 480), 900);
      setPageWidth(cap);
    };
    compute();
    window.addEventListener("resize", compute);
    return () => window.removeEventListener("resize", compute);
  }, []);

  const currentRegion = regions.get(pageNumber) ?? null;
  const currentRect = currentRegion?.rect ?? null;

  const writeRect = useCallback(
    (page: number, rect: Bbox | null, scale: number) => {
      setRegions((prev) => {
        const next = new Map(prev);
        if (rect === null) next.delete(page);
        else next.set(page, { rect, scale });
        return next;
      });
    },
    [],
  );

  const localPoint = useCallback((e: React.PointerEvent) => {
    const el = overlayRef.current;
    if (!el) return { x: 0, y: 0 };
    const rect = el.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }, []);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!render) return;
      const scale = render.width / render.pointWidth;
      const target = e.target as HTMLElement;
      const handle = target.dataset.handle as HandleId | undefined;
      const isMoveTarget = target.dataset.role === "move";
      const p = localPoint(e);
      e.currentTarget.setPointerCapture(e.pointerId);
      setDragging(true);

      if (handle && currentRect) {
        const anchorX = handle.includes("w") ? currentRect.x1 : currentRect.x0;
        const anchorY = handle.includes("n") ? currentRect.bottom : currentRect.top;
        const lockX = handle === "n" || handle === "s";
        const lockY = handle === "e" || handle === "w";
        dragRef.current = { kind: "resize", anchorX, anchorY, lockX, lockY };
        return;
      }

      if (isMoveTarget && currentRect) {
        dragRef.current = {
          kind: "move",
          offsetX: p.x - currentRect.x0,
          offsetY: p.y - currentRect.top,
        };
        return;
      }

      dragRef.current = { kind: "draw", originX: p.x, originY: p.y };
      writeRect(
        pageNumber,
        { x0: p.x, top: p.y, x1: p.x, bottom: p.y },
        scale,
      );
    },
    [render, localPoint, currentRect, pageNumber, writeRect],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (!drag || !render) return;
      const scale = render.width / render.pointWidth;
      const p = localPoint(e);
      const bounds = { width: render.width, height: render.height };

      if (drag.kind === "draw") {
        writeRect(
          pageNumber,
          bboxFromPx(
            { x: drag.originX, y: drag.originY },
            { x: p.x, y: p.y },
            bounds,
          ),
          scale,
        );
        return;
      }

      if (drag.kind === "move" && currentRect) {
        const w = currentRect.x1 - currentRect.x0;
        const h = currentRect.bottom - currentRect.top;
        const x0 = clamp(p.x - drag.offsetX, 0, bounds.width - w);
        const top = clamp(p.y - drag.offsetY, 0, bounds.height - h);
        writeRect(
          pageNumber,
          { x0, top, x1: x0 + w, bottom: top + h },
          scale,
        );
        return;
      }

      if (drag.kind === "resize" && currentRect) {
        const px = drag.lockX ? currentRect.x0 : p.x;
        const py = drag.lockY ? currentRect.top : p.y;
        const next = bboxFromPx(
          { x: drag.anchorX, y: drag.anchorY },
          { x: drag.lockX ? currentRect.x1 : px, y: drag.lockY ? currentRect.bottom : py },
          bounds,
        );
        writeRect(pageNumber, next, scale);
      }
    },
    [render, localPoint, currentRect, pageNumber, writeRect],
  );

  const endDrag = useCallback(() => {
    const drag = dragRef.current;
    dragRef.current = null;
    setDragging(false);
    if (drag?.kind === "draw") {
      // Reject zero-size selections from accidental clicks.
      setRegions((prev) => {
        const r = prev.get(pageNumber);
        if (!r) return prev;
        if (r.rect.x1 - r.rect.x0 < 4 || r.rect.bottom - r.rect.top < 4) {
          const next = new Map(prev);
          next.delete(pageNumber);
          return next;
        }
        return prev;
      });
    }
  }, [pageNumber]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") writeRect(pageNumber, null, 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pageNumber, writeRect]);

  const submit = useCallback(() => {
    const out: CropRegion[] = [];
    regions.forEach((r, page) => {
      // px → points using each page's stored scale.
      out.push({
        page,
        x0: r.rect.x0 / r.scale,
        top: r.rect.top / r.scale,
        x1: r.rect.x1 / r.scale,
        bottom: r.rect.bottom / r.scale,
      });
    });
    out.sort((a, b) => a.page - b.page);
    onRun(out);
  }, [regions, onRun]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <CropperHeader
        filename={file.name}
        pageNumber={pageNumber}
        totalPages={totalPages}
        regionCount={regions.size}
        onPrev={() => setPageNumber((p) => Math.max(1, p - 1))}
        onNext={() => setPageNumber((p) => Math.min(totalPages, p + 1))}
        onBack={onBack}
      />

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-[260px] shrink-0 flex-col gap-4 border-r border-border/60 bg-card/30 p-4">
          <section className="space-y-1.5">
            <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Select region
            </p>
            <p className="text-[12.5px] leading-relaxed text-muted-foreground">
              Drag to draw a crop rectangle over the drawing area. Drag corners
              or edges to resize, drag inside to move. Pages without a region
              are skipped on extraction.
            </p>
          </section>

          <section className="space-y-2">
            <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              This page
            </p>
            <div className="rounded-lg border border-border/70 bg-background/40 p-3 text-[12.5px]">
              {currentRegion ? (
                <RegionStats region={currentRegion} />
              ) : (
                <p className="text-muted-foreground">No region selected</p>
              )}
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="w-full"
              onClick={() => writeRect(pageNumber, null, 1)}
              disabled={!currentRegion}
            >
              <Eraser />
              Clear region
            </Button>
          </section>

          {totalPages > 1 && (
            <section className="space-y-2">
              <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                All pages
              </p>
              <ul className="space-y-1">
                {Array.from({ length: totalPages }, (_, i) => i + 1).map((n) => (
                  <li key={n}>
                    <button
                      type="button"
                      onClick={() => setPageNumber(n)}
                      className={cn(
                        "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-[12.5px] transition-colors",
                        n === pageNumber
                          ? "bg-accent text-accent-foreground"
                          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                      )}
                    >
                      <span className="tabular-nums">Page {n}</span>
                      {regions.has(n) && (
                        <span className="inline-flex size-1.5 rounded-full bg-primary" />
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <div className="mt-auto space-y-2">
            <Button
              onClick={submit}
              disabled={regions.size === 0 || loading}
              className="w-full"
            >
              {loading ? "Extracting…" : "Run extraction"}
              {!loading && <ArrowRight />}
            </Button>
            <p className="text-center text-[11px] tabular-nums text-muted-foreground">
              {regions.size} {regions.size === 1 ? "region" : "regions"} defined
            </p>
          </div>
        </aside>

        <section className="relative flex min-w-0 flex-1 items-center justify-center overflow-auto bg-[radial-gradient(circle_at_center,oklch(0.97_0_0)_0%,oklch(0.93_0.003_260)_100%)] p-8">
          <div className="relative shadow-2xl ring-1 ring-border/40">
            <PdfPage
              // Remount on page change so render-info resets cleanly without
              // an effect-driven setState.
              key={pageNumber}
              file={pdfUrl}
              pageNumber={pageNumber}
              width={pageWidth}
              onLoad={(count) => setTotalPages(count)}
              onRender={(info) => setRenderState({ page: pageNumber, info })}
              onError={(err) => toast.error(`PDF render failed: ${err.message}`)}
            >
              {render && (
                <div
                  ref={overlayRef}
                  className={cn(
                    "absolute left-0 top-0 select-none",
                    dragging ? "cursor-crosshair" : "cursor-crosshair",
                  )}
                  style={{ width: render.width, height: render.height }}
                  onPointerDown={onPointerDown}
                  onPointerMove={onPointerMove}
                  onPointerUp={endDrag}
                  onPointerCancel={endDrag}
                >
                  {currentRect && (
                    <SelectionRect rect={currentRect} render={render} />
                  )}
                </div>
              )}
            </PdfPage>
            {!render && (
              <div className="flex h-[600px] w-[450px] items-center justify-center rounded-md bg-card/60 text-sm text-muted-foreground">
                <Crop className="mr-2 size-4 opacity-60" /> Loading page…
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function CropperHeader({
  filename,
  pageNumber,
  totalPages,
  regionCount,
  onPrev,
  onNext,
  onBack,
}: {
  filename: string;
  pageNumber: number;
  totalPages: number;
  regionCount: number;
  onPrev: () => void;
  onNext: () => void;
  onBack: () => void;
}) {
  return (
    <AppHeader
      filename={filename}
      onReset={onBack}
      meta={
        <>
          <span className="rounded-full border border-border/60 bg-card/60 px-2 py-0.5 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Crop · Step 2 of 2
          </span>
          {totalPages > 1 && (
            <div className="ml-1 flex items-center gap-1 rounded-md border border-border/60 bg-card/60 p-0.5">
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={onPrev}
                disabled={pageNumber <= 1}
                aria-label="Previous page"
              >
                <ChevronLeft />
              </Button>
              <span className="px-1 text-[12px] tabular-nums">
                {pageNumber} / {totalPages}
              </span>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={onNext}
                disabled={pageNumber >= totalPages}
                aria-label="Next page"
              >
                <ChevronRight />
              </Button>
            </div>
          )}
          <span className="text-[12px] tabular-nums text-muted-foreground">
            {regionCount} {regionCount === 1 ? "region" : "regions"}
          </span>
        </>
      }
    />
  );
}

function RegionStats({ region }: { region: Region }) {
  const { rect, scale } = region;
  const wPts = (rect.x1 - rect.x0) / scale;
  const hPts = (rect.bottom - rect.top) / scale;
  const x0Pts = rect.x0 / scale;
  const yPts = rect.top / scale;
  return (
    <dl className="space-y-1 font-mono text-[11px] tabular-nums text-foreground/85">
      <div className="flex justify-between">
        <dt className="text-muted-foreground">Size</dt>
        <dd>
          {wPts.toFixed(0)} × {hPts.toFixed(0)} pts
        </dd>
      </div>
      <div className="flex justify-between">
        <dt className="text-muted-foreground">Origin</dt>
        <dd>
          {x0Pts.toFixed(0)}, {yPts.toFixed(0)}
        </dd>
      </div>
    </dl>
  );
}

function SelectionRect({ rect, render }: { rect: Bbox; render: PdfRenderInfo }) {
  const w = rect.x1 - rect.x0;
  const h = rect.bottom - rect.top;
  return (
    <>
      {/* Letterbox dimming — four bands around the selection. */}
      <div
        className="pointer-events-none absolute left-0 top-0 right-0 bg-black/55"
        style={{ height: rect.top }}
      />
      <div
        className="pointer-events-none absolute left-0 right-0 bg-black/55"
        style={{ top: rect.bottom, height: render.height - rect.bottom }}
      />
      <div
        className="pointer-events-none absolute left-0 bg-black/55"
        style={{ top: rect.top, height: h, width: rect.x0 }}
      />
      <div
        className="pointer-events-none absolute right-0 bg-black/55"
        style={{
          top: rect.top,
          height: h,
          width: render.width - rect.x1,
        }}
      />

      <div
        data-role="move"
        className="absolute cursor-move rounded-sm ring-2 ring-primary/90 ring-offset-1 ring-offset-transparent"
        style={{ left: rect.x0, top: rect.top, width: w, height: h }}
      >
        {HANDLES.map((h) => (
          <ResizeHandle key={h} id={h} />
        ))}
      </div>
    </>
  );
}

function ResizeHandle({ id }: { id: HandleId }) {
  const positions: Record<HandleId, { style: React.CSSProperties; cursor: string }> = {
    nw: { style: { top: -4, left: -4 }, cursor: "nwse-resize" },
    n: { style: { top: -4, left: "50%", transform: "translateX(-50%)" }, cursor: "ns-resize" },
    ne: { style: { top: -4, right: -4 }, cursor: "nesw-resize" },
    e: { style: { top: "50%", right: -4, transform: "translateY(-50%)" }, cursor: "ew-resize" },
    se: { style: { bottom: -4, right: -4 }, cursor: "nwse-resize" },
    s: { style: { bottom: -4, left: "50%", transform: "translateX(-50%)" }, cursor: "ns-resize" },
    sw: { style: { bottom: -4, left: -4 }, cursor: "nesw-resize" },
    w: { style: { top: "50%", left: -4, transform: "translateY(-50%)" }, cursor: "ew-resize" },
  };
  const { style, cursor } = positions[id];
  return (
    <span
      data-handle={id}
      className="absolute size-2 rounded-[2px] border border-background bg-primary shadow-md"
      style={{ ...style, cursor }}
    />
  );
}
