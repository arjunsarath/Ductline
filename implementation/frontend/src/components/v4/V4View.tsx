/**
 * Top-level V4 page. Owns: file selection, V4 session lifecycle, result
 * state, viewport, current selection, settings drawer, and the assumption
 * banner. Layout follows the V3 result view (top bar + viewer + side panel)
 * for visual consistency.
 *
 * "Live recompute" on settings save is implemented as a re-call of
 * POST /v4/sessions with the same PDF and updated op_vars (per the brief —
 * a separate recompute endpoint is not introduced).
 */

import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { runV4SessionStreaming } from "../../api/v4Client";
import { INITIAL_VIEWPORT, SCALE_MAX, SCALE_MIN, type Viewport } from "../viewport";
import { clamp } from "../canvasShared";
import type { OperationalVars, V4ProgressEvent, V4Result } from "../../types/v4";
import { DEFAULT_OP_VARS } from "../../types/v4";
import { V4AssumptionBanner } from "./V4AssumptionBanner";
import { V4Progress } from "./V4Progress";
import { V4SegmentPanel } from "./V4SegmentPanel";
import { V4SettingsDrawer } from "./V4SettingsDrawer";
import { V4Sidebar } from "./V4Sidebar";
import { V4TerminalPanel } from "./V4TerminalPanel";
import { V4Topbar } from "./V4Topbar";
import { V4UploadPane } from "./V4UploadPane";
import { V4Viewer } from "./V4Viewer";
import { V4MarkAreaScreen } from "./V4MarkAreaScreen";
import { V4PipelineTools } from "./V4PipelineTools";
import type { CropArea, FilterToggles } from "../../api/v4Client";
import type { V4Selection } from "./V4Overlay";
import { useDrawingDims, useFitDims } from "./dims";
import { useResolvedSelection } from "./selection";

const DEFAULT_MIN_ASPECT_RATIO = 6.0;
const DEFAULT_MIN_WHITE_PCT = 0.85;
const DEFAULT_EPSILON_FRAC = 0.02;
const DEFAULT_MAX_CORNER_COS = 0.25;
const DEFAULT_TOGGLES: FilterToggles = {
  oversized: true,
  aspectRatio: false,
  interior: false,
  content: false,
  rectangle: true,
};
const DEFAULT_RECT_DPI = 100;
const DEFAULT_OCR_DPI = 600;
const DEFAULT_INK_THRESHOLD = 90;
const DEFAULT_MIN_INK_PCT = 0.005;
const DEFAULT_MAX_INK_PCT = 0.30;
const DEFAULT_MIN_DUCT_ASPECT = 1.5;

type Status =
  | { kind: "idle" }
  | { kind: "loading_initial"; events: V4ProgressEvent[] }
  | { kind: "marking_area"; cleaned: V4Result }
  | { kind: "loading"; events: V4ProgressEvent[]; cleaned: V4Result }
  | { kind: "ready"; result: V4Result }
  | { kind: "error"; message: string; lastStage: string | null };

interface V4ViewProps {
  renderUploadHeader?: () => ReactNode;
}

export function V4View({ renderUploadHeader }: V4ViewProps = {}) {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [opVars, setOpVars] = useState<OperationalVars>(DEFAULT_OP_VARS);
  const [sourceNodeId, setSourceNodeId] = useState<string>("");
  const [selection, setSelection] = useState<V4Selection>(null);
  const [viewport, setViewport] = useState<Viewport>(INITIAL_VIEWPORT);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [debug, setDebug] = useState(false);
  const [minAspectRatio] = useState(DEFAULT_MIN_ASPECT_RATIO);
  const [minWhitePct] = useState(DEFAULT_MIN_WHITE_PCT);
  const [epsilonFrac] = useState(DEFAULT_EPSILON_FRAC);
  const [maxCornerCos] = useState(DEFAULT_MAX_CORNER_COS);
  const [toggles] = useState<FilterToggles>(DEFAULT_TOGGLES);
  const [cropArea, setCropArea] = useState<CropArea | null>(null);
  const [dpi, setDpi] = useState<number>(DEFAULT_RECT_DPI);
  const [ocrDpi, setOcrDpi] = useState<number>(DEFAULT_OCR_DPI);
  const [inkThreshold, setInkThreshold] = useState<number>(DEFAULT_INK_THRESHOLD);
  const [enableMinInk, setEnableMinInk] = useState(true);
  const [minInkPct, setMinInkPct] = useState<number>(DEFAULT_MIN_INK_PCT);
  const [enableMaxInk, setEnableMaxInk] = useState(true);
  const [maxInkPct, setMaxInkPct] = useState<number>(DEFAULT_MAX_INK_PCT);
  const [enableSquarish, setEnableSquarish] = useState(true);
  const [minDuctAspect, setMinDuctAspect] = useState<number>(DEFAULT_MIN_DUCT_ASPECT);
  const [winSize, setWinSize] = useState({
    w: typeof window !== "undefined" ? window.innerWidth : 1200,
    h: typeof window !== "undefined" ? window.innerHeight : 900,
  });

  useEffect(() => {
    const onResize = () =>
      setWinSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const runInitial = useCallback(
    async (target: File, atDpi: number, ink: number) => {
      const events: V4ProgressEvent[] = [];
      let lastStage: string | null = null;
      setStatus({ kind: "loading_initial", events: [] });
      const onEvent = (event: V4ProgressEvent) => {
        events.push(event);
        lastStage = event.stage;
        setStatus({ kind: "loading_initial", events: [...events] });
      };
      try {
        const cleaned = await runV4SessionStreaming(
          target,
          { stopAfter: "grey_removal", rectDpi: atDpi, inkThreshold: ink },
          onEvent,
        );
        setStatus({ kind: "marking_area", cleaned });
      } catch (err) {
        const message = err instanceof Error ? err.message : "v4 failed";
        const detail = lastStage ? `${message} (last stage: ${lastStage})` : message;
        setStatus({ kind: "error", message: detail, lastStage });
      }
    },
    [],
  );

  const runSession = useCallback(
    async (
      target: File,
      vars: OperationalVars,
      srcId: string,
      dbg: boolean,
      aspect: number,
      whitePct: number,
      tgls: FilterToggles,
      crop: CropArea | null,
      cleanedFromInitial: V4Result | null,
      atDpi: number,
      runVlm: boolean,
      ink: number,
    ) => {
      const events: V4ProgressEvent[] = [];
      let lastStage: string | null = null;
      setStatus({
        kind: "loading", events: [],
        cleaned: cleanedFromInitial as V4Result,
      });
      const onEvent = (event: V4ProgressEvent) => {
        events.push(event);
        lastStage = event.stage;
        setStatus({
          kind: "loading", events: [...events],
          cleaned: cleanedFromInitial as V4Result,
        });
      };
      try {
        const opts = {
          opVars: vars,
          sourceNodeId: srcId || undefined,
          debug: dbg,
          minAspectRatio: aspect,
          minWhitePct: whitePct,
          epsilonFrac,
          maxCornerCos,
          toggles: tgls,
          cropArea: crop,
          rectDpi: atDpi,
          ocrDpi,
          enableVlmOcr: runVlm,
          inkThreshold: ink,
          enableMinInk,
          minInkPct,
          enableMaxInk,
          maxInkPct,
          enableSquarish,
          minDuctAspect,
        };
        const result = await runV4SessionStreaming(target, opts, onEvent);
        setOpVars(result.op_vars);
        setStatus({ kind: "ready", result });
      } catch (err) {
        const message = err instanceof Error ? err.message : "v4 failed";
        const detail = lastStage ? `${message} (last stage: ${lastStage})` : message;
        setStatus({ kind: "error", message: detail, lastStage });
      }
    },
    [],
  );

  const onFile = useCallback(
    (f: File) => {
      setFile(f);
      setSelection(null);
      setViewport(INITIAL_VIEWPORT);
      setCropArea(null);
      void runInitial(f, dpi, inkThreshold);
    },
    [runInitial, dpi, inkThreshold],
  );

  const onMarkAreaConfirm = useCallback(
    (area: CropArea | null) => {
      if (!file) return;
      const cleaned = status.kind === "marking_area" ? status.cleaned : null;
      setCropArea(area);
      void runSession(
        file, opVars, sourceNodeId, debug,
        minAspectRatio, minWhitePct, toggles, area, cleaned, dpi, false, inkThreshold,
      );
    },
    [
      file, status, opVars, sourceNodeId, debug,
      minAspectRatio, minWhitePct, toggles, runSession, dpi,
    ],
  );

  const onSettingsSave = useCallback(
    (next: OperationalVars, srcId: string) => {
      setOpVars(next);
      setSourceNodeId(srcId);
      if (file) {
        void runSession(
          file, next, srcId, debug,
          minAspectRatio, minWhitePct, toggles, cropArea, null, dpi, false, inkThreshold,
        );
      }
      setDrawerOpen(false);
    },
    [
      file, debug, minAspectRatio, minWhitePct, toggles, cropArea, runSession,
      dpi,
    ],
  );

  const onToggleDebug = useCallback(
    (next: boolean) => {
      setDebug(next);
      if (file) {
        void runSession(
          file, opVars, sourceNodeId, next,
          minAspectRatio, minWhitePct, toggles, cropArea, null, dpi, false, inkThreshold,
        );
      }
    },
    [
      file, opVars, sourceNodeId, minAspectRatio, minWhitePct, toggles,
      cropArea, runSession, dpi,
    ],
  );

  const onDpiCommit = useCallback(
    (next: number) => {
      setDpi(next);
      setCropArea(null);
      if (file) {
        void runSession(
          file, opVars, sourceNodeId, debug,
          minAspectRatio, minWhitePct, toggles, null, null, next, false, inkThreshold,
        );
      }
    },
    [
      file, opVars, sourceNodeId, debug, minAspectRatio, minWhitePct, toggles,
      runSession,
    ],
  );

  const onRedefineArea = useCallback(() => {
    if (file) void runInitial(file, dpi, inkThreshold);
  }, [file, dpi, inkThreshold, runInitial]);

  const onRunVlmOcr = useCallback(() => {
    if (!file) return;
    void runSession(
      file, opVars, sourceNodeId, debug,
      minAspectRatio, minWhitePct, toggles, cropArea, null, dpi, true, inkThreshold,
    );
  }, [
    file, opVars, sourceNodeId, debug, minAspectRatio, minWhitePct,
    toggles, cropArea, dpi, runSession, inkThreshold,
  ]);

  const onPrefilterCommit = useCallback(
    (next: {
      enableMinInk: boolean;
      minInkPct: number;
      enableMaxInk: boolean;
      maxInkPct: number;
      enableSquarish: boolean;
      minDuctAspect: number;
    }) => {
      setEnableMinInk(next.enableMinInk);
      setMinInkPct(next.minInkPct);
      setEnableMaxInk(next.enableMaxInk);
      setMaxInkPct(next.maxInkPct);
      setEnableSquarish(next.enableSquarish);
      setMinDuctAspect(next.minDuctAspect);
      if (file) {
        // We don't depend on the closure-captured filter state because we
        // pass the new values directly through the next opts; runSession's
        // closure still reads our latest state on its next invocation.
        void runSession(
          file, opVars, sourceNodeId, debug,
          minAspectRatio, minWhitePct, toggles, cropArea, null, dpi, false,
          inkThreshold,
        );
      }
    },
    [
      file, opVars, sourceNodeId, debug, minAspectRatio, minWhitePct, toggles,
      cropArea, dpi, inkThreshold, runSession,
    ],
  );

  const onInkThresholdCommit = useCallback(
    (next: number) => {
      setInkThreshold(next);
      setCropArea(null);
      if (file) {
        void runSession(
          file, opVars, sourceNodeId, debug,
          minAspectRatio, minWhitePct, toggles, null, null, dpi, false, next,
        );
      }
    },
    [
      file, opVars, sourceNodeId, debug, minAspectRatio, minWhitePct, toggles,
      dpi, runSession,
    ],
  );

  const reset = useCallback(() => {
    setFile(null);
    setStatus({ kind: "idle" });
    setSelection(null);
    setViewport(INITIAL_VIEWPORT);
  }, []);

  const result = status.kind === "ready" ? status.result : null;
  const vlmAlreadyRun = !!result?.debug_ocr?.some(
    (m) => m.text && m.text.length > 0,
  );
  const drawingDims = useDrawingDims(result);
  const fit = useFitDims(drawingDims, winSize);
  const { segment: selectedSegment, terminal: selectedTerminal, segmentWarnings } =
    useResolvedSelection(result, selection);

  const onZoomBy = useCallback((factor: number) => {
    setViewport((v) => ({
      ...v,
      scale: clamp(v.scale * factor, SCALE_MIN, SCALE_MAX),
    }));
  }, []);

  const onRotate = useCallback(() => {
    setViewport((v) => ({ ...v, rotationDeg: (v.rotationDeg + 90) % 360 }));
    setSelection(null);
  }, []);

  if (!file) {
    return (
      <>
        {renderUploadHeader?.()}
        <V4UploadPane onFile={onFile} />
      </>
    );
  }

  if (status.kind === "loading_initial") {
    return (
      <main className="result-view v4-view">
        <div className="v4-loading"><V4Progress events={status.events} /></div>
      </main>
    );
  }

  if (status.kind === "marking_area") {
    return (
      <V4MarkAreaScreen
        cleaned={status.cleaned}
        onConfirm={onMarkAreaConfirm}
        onCancel={reset}
      />
    );
  }

  return (
    <main className="result-view v4-view">
      <V4Topbar
        filename={file.name}
        result={result}
        busy={status.kind === "loading"}
        debug={debug}
        onReset={reset}
        onOpenSettings={() => setDrawerOpen(true)}
        onToggleDebug={onToggleDebug}
      />

      <V4AssumptionBanner />

      <div className="result-body v4-body">
        {status.kind === "loading" && (
          <div className="v4-loading"><V4Progress events={status.events} /></div>
        )}
        {status.kind === "error" && (
          <div className="v4-loading v4-error">
            <p>V4 pipeline failed: {status.message}</p>
            <button type="button" className="button-ghost" onClick={reset}>
              Try another file
            </button>
          </div>
        )}
        {result && drawingDims && (
          <V4Viewer
            file={file}
            result={result}
            drawingW={drawingDims.width}
            drawingH={drawingDims.height}
            fitWidth={fit.w}
            fitHeight={fit.h}
            selection={selection}
            viewport={viewport}
            onViewportChange={setViewport}
            onSelect={setSelection}
            onRotate={onRotate}
            onZoomBy={onZoomBy}
          />
        )}
        {result && selectedSegment && (
          <V4SegmentPanel
            segment={selectedSegment}
            thresholds={result.op_vars.smacna_thresholds_in_wc}
            warnings={segmentWarnings}
            onClose={() => setSelection(null)}
          />
        )}
        {result && selectedTerminal && (
          <V4TerminalPanel
            terminal={selectedTerminal}
            onClose={() => setSelection(null)}
          />
        )}
        {result && !selectedSegment && !selectedTerminal && (
          <V4Sidebar result={result} />
        )}
      </div>

      {drawerOpen && (
        <V4SettingsDrawer
          initial={opVars}
          initialSourceNodeId={sourceNodeId}
          busy={status.kind === "loading"}
          onSave={onSettingsSave}
          onClose={() => setDrawerOpen(false)}
        />
      )}

      {result && (
        <V4PipelineTools
          rectDpi={dpi}
          ocrDpi={ocrDpi}
          inkThreshold={inkThreshold}
          enableMinInk={enableMinInk}
          minInkPct={minInkPct}
          enableMaxInk={enableMaxInk}
          maxInkPct={maxInkPct}
          enableSquarish={enableSquarish}
          minDuctAspect={minDuctAspect}
          cropActive={cropArea !== null}
          busy={status.kind === "loading"}
          rectanglesReady={(result.debug_ocr?.length ?? 0) > 0}
          vlmAlreadyRun={vlmAlreadyRun}
          onCommitRectDpi={onDpiCommit}
          onCommitOcrDpi={setOcrDpi}
          onCommitInk={onInkThresholdCommit}
          onCommitPrefilter={onPrefilterCommit}
          onRedefineArea={onRedefineArea}
          onRunVlmOcr={onRunVlmOcr}
        />
      )}
    </main>
  );
}


