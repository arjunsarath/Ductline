/**
 * Result view per Paper artboard 03 — top bar (brand + filename + zoom + toggles)
 * → optional quality banner → viewer + sidebar.
 *
 * State: selection, grayscale, sidebar collapse, and a UI-only zoom percentage
 * (the canvas auto-fits and zoom is decorative in v1 — UI-SPEC §"Open until
 * first drawing"). Keyboard: ←/→ cycle selection, Esc clear, g grayscale, s sidebar.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { DrawingResult } from "../types/api";
import { Brand } from "./Brand";
import { QualityBanner } from "./QualityBanner";
import { Sidebar } from "./Sidebar";
import { Viewer } from "./Viewer";
import {
  INITIAL_VIEWPORT,
  SCALE_MAX,
  SCALE_MIN,
  type Viewport,
} from "./viewport";

interface Props {
  filename: string;
  file: File;
  result: DrawingResult;
  onReset: () => void;
}

export function ResultView({ filename, file, result, onReset }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [grayscale, setGrayscale] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [viewport, setViewport] = useState<Viewport>(INITIAL_VIEWPORT);

  const zoomBy = useCallback((factor: number) => {
    setViewport((v) => {
      const next = clamp(v.scale * factor, SCALE_MIN, SCALE_MAX);
      // Anchor to current center: keep tx/ty so the visible center stays put.
      // Simpler than locking to a screen point and adequate for button clicks.
      return { ...v, scale: next };
    });
  }, []);

  const resetViewport = useCallback(() => setViewport(INITIAL_VIEWPORT), []);

  const rotate = useCallback(() => {
    setViewport((v) => ({ ...v, rotationDeg: (v.rotationDeg + 90) % 360 }));
    setSelectedId(null);
  }, []);

  const segmentIds = useMemo(() => result.segments.map((s) => s.id), [result.segments]);

  useEffect(() => {
    function handler(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.tagName === "INPUT" || target?.tagName === "TEXTAREA") return;

      if (event.key === "Escape") return setSelectedId(null);
      if (event.key === "g" || event.key === "G") return setGrayscale((g) => !g);
      if (event.key === "s" || event.key === "S")
        return setSidebarCollapsed((c) => !c);
      if (event.key === "ArrowRight" || (event.key === "Tab" && !event.shiftKey)) {
        setSelectedId((current) => stepSelection(segmentIds, current, +1));
        event.preventDefault();
      }
      if (event.key === "ArrowLeft" || (event.key === "Tab" && event.shiftKey)) {
        setSelectedId((current) => stepSelection(segmentIds, current, -1));
        event.preventDefault();
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [segmentIds]);

  const handleSelect = useCallback(
    (id: string | null) => setSelectedId(id),
    [],
  );

  return (
    <main className="result-view">
      <header className="result-topbar">
        <div className="result-topbar-left">
          <Brand />
          <button type="button" className="button-ghost" onClick={onReset}>
            ← New drawing
          </button>
          <div className="result-topbar-meta">
            <span className="mono">{filename}</span>
            <span>·</span>
            <strong>{result.aggregate.total} segments</strong>
          </div>
        </div>
        <div className="result-topbar-spacer" />
        <div className="result-topbar-right">
          <button type="button" className="button-ghost" onClick={resetViewport}>
            Reset
          </button>
          <button
            type="button"
            className="button-ghost"
            aria-pressed={grayscale}
            onClick={() => setGrayscale((g) => !g)}
            title="Toggle grayscale"
          >
            Grayscale
            <kbd className="kbd-hint">G</kbd>
          </button>
          <button
            type="button"
            className="button-ghost"
            aria-pressed={!sidebarCollapsed}
            onClick={() => setSidebarCollapsed((c) => !c)}
            title="Toggle sidebar"
          >
            Sidebar
            <kbd className="kbd-hint">S</kbd>
          </button>
        </div>
      </header>

      {result.quality.overall !== "high" && (
        <QualityBanner quality={result.quality} />
      )}

      <div className="result-body">
        <Viewer
          result={result}
          file={file}
          selectedId={selectedId}
          grayscale={grayscale}
          viewport={viewport}
          onViewportChange={setViewport}
          onSelect={handleSelect}
          onRotate={rotate}
          onZoomBy={zoomBy}
        />
        {!sidebarCollapsed && (
          <Sidebar
            result={result}
            selectedId={selectedId}
            onSelect={handleSelect}
          />
        )}
      </div>
    </main>
  );
}

function stepSelection(ids: string[], current: string | null, delta: number): string | null {
  if (ids.length === 0) return null;
  if (current === null) return delta > 0 ? ids[0] : ids[ids.length - 1];
  const index = ids.indexOf(current);
  const next = (index + delta + ids.length) % ids.length;
  return ids[next];
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
