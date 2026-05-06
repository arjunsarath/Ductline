/**
 * Floating pipeline-tools panel — exposes the rasterization DPI for OCR
 * tuning and lets the operator return to the mark-area step. Slider commits
 * on release; redefine-area is one click.
 */

import { useEffect, useState } from "react";

interface Props {
  rectDpi: number;
  ocrDpi: number;
  inkThreshold: number;
  enableMinInk: boolean;
  minInkPct: number;
  enableMaxInk: boolean;
  maxInkPct: number;
  enableSquarish: boolean;
  minDuctAspect: number;
  cropActive: boolean;
  busy: boolean;
  rectanglesReady: boolean;
  vlmAlreadyRun: boolean;
  onCommitRectDpi: (next: number) => void;
  onCommitOcrDpi: (next: number) => void;
  onCommitInk: (next: number) => void;
  onCommitPrefilter: (next: {
    enableMinInk: boolean;
    minInkPct: number;
    enableMaxInk: boolean;
    maxInkPct: number;
    enableSquarish: boolean;
    minDuctAspect: number;
  }) => void;
  onRedefineArea: () => void;
  onRunVlmOcr: () => void;
}

export function V4PipelineTools(props: Props) {
  const {
    rectDpi, ocrDpi, inkThreshold,
    enableMinInk, minInkPct, enableMaxInk, maxInkPct,
    enableSquarish, minDuctAspect,
    cropActive, busy, rectanglesReady, vlmAlreadyRun,
    onCommitRectDpi, onCommitOcrDpi, onCommitInk, onCommitPrefilter,
    onRedefineArea, onRunVlmOcr,
  } = props;
  const [rectDraft, setRectDraft] = useState(rectDpi);
  const [ocrDraft, setOcrDraft] = useState(ocrDpi);
  const [inkDraft, setInkDraft] = useState(inkThreshold);
  const [inkPctDraft, setInkPctDraft] = useState(minInkPct);
  const [maxInkPctDraft, setMaxInkPctDraft] = useState(maxInkPct);
  const [aspectDraft, setAspectDraft] = useState(minDuctAspect);

  useEffect(() => setRectDraft(rectDpi), [rectDpi]);
  useEffect(() => setOcrDraft(ocrDpi), [ocrDpi]);
  useEffect(() => setInkDraft(inkThreshold), [inkThreshold]);
  useEffect(() => setInkPctDraft(minInkPct), [minInkPct]);
  useEffect(() => setMaxInkPctDraft(maxInkPct), [maxInkPct]);
  useEffect(() => setAspectDraft(minDuctAspect), [minDuctAspect]);

  const commitRectDpi = () => {
    if (rectDraft !== rectDpi) onCommitRectDpi(rectDraft);
  };
  const commitOcrDpi = () => {
    if (ocrDraft !== ocrDpi) onCommitOcrDpi(ocrDraft);
  };
  const commitInk = () => {
    if (inkDraft !== inkThreshold) onCommitInk(inkDraft);
  };
  const baseFilter = {
    enableMinInk, enableMaxInk, enableSquarish,
    minInkPct: inkPctDraft, maxInkPct: maxInkPctDraft,
    minDuctAspect: aspectDraft,
  };
  const commitPrefilter = () => {
    if (
      inkPctDraft !== minInkPct
      || maxInkPctDraft !== maxInkPct
      || aspectDraft !== minDuctAspect
    ) {
      onCommitPrefilter(baseFilter);
    }
  };
  const onToggleMinInk = (next: boolean) =>
    onCommitPrefilter({ ...baseFilter, enableMinInk: next });
  const onToggleMaxInk = (next: boolean) =>
    onCommitPrefilter({ ...baseFilter, enableMaxInk: next });
  const onToggleSquarish = (next: boolean) =>
    onCommitPrefilter({ ...baseFilter, enableSquarish: next });

  return (
    <aside
      className="v4-debug-panel"
      role="region"
      aria-label="Pipeline tools"
    >
      <header className="v4-debug-panel__head">
        <strong>Pipeline</strong>
      </header>

      <div className="v4-debug-panel__row">
        <label htmlFor="v4-rect-dpi">
          Rectangle DPI
          <span className="v4-debug-panel__value">{rectDraft}</span>
        </label>
        <input
          id="v4-rect-dpi"
          type="range"
          min={50}
          max={300}
          step={25}
          value={rectDraft}
          disabled={busy}
          onChange={(e) => setRectDraft(Number(e.target.value))}
          onMouseUp={commitRectDpi}
          onTouchEnd={commitRectDpi}
          onKeyUp={commitRectDpi}
        />
        <p className="v4-debug-panel__hint">
          DPI for rectangle detection. Lower = faster, fewer false positives
          from thin grey lines. Default 100. Changing this clears the area.
        </p>
      </div>

      <div className="v4-debug-panel__row">
        <label htmlFor="v4-ocr-dpi">
          OCR DPI
          <span className="v4-debug-panel__value">{ocrDraft}</span>
        </label>
        <input
          id="v4-ocr-dpi"
          type="range"
          min={300}
          max={900}
          step={50}
          value={ocrDraft}
          disabled={busy}
          onChange={(e) => setOcrDraft(Number(e.target.value))}
          onMouseUp={commitOcrDpi}
          onTouchEnd={commitOcrDpi}
          onKeyUp={commitOcrDpi}
        />
        <p className="v4-debug-panel__hint">
          DPI used to re-rasterize each rectangle for OCR. Higher = clearer
          text, but slower. Bboxes are scaled by ocr/rect.
        </p>
      </div>

      <div className="v4-debug-panel__row">
        <label htmlFor="v4-ink">
          Ink threshold
          <span className="v4-debug-panel__value">{inkDraft}</span>
        </label>
        <input
          id="v4-ink"
          type="range"
          min={0}
          max={255}
          step={1}
          value={inkDraft}
          disabled={busy}
          onChange={(e) => setInkDraft(Number(e.target.value))}
          onMouseUp={commitInk}
          onTouchEnd={commitInk}
          onKeyUp={commitInk}
        />
        <p className="v4-debug-panel__hint">
          Pixels darker than this become ink. Default 90. Lower = stricter,
          only true blacks; higher pulls in greys.
        </p>
      </div>

      <div className="v4-debug-panel__row">
        <span className="v4-debug-panel__label-line">Drawing area</span>
        <div className="v4-debug-panel__crop-row">
          <button
            type="button"
            className="v4-debug-panel__btn v4-debug-panel__btn--ghost"
            onClick={onRedefineArea}
            disabled={busy}
          >
            {cropActive ? "Redefine area" : "Define area"}
          </button>
        </div>
        <p className="v4-debug-panel__hint">
          {cropActive ? "Active mask in place." : "Full page."}
        </p>
      </div>

      <div className="v4-debug-panel__row">
        <span className="v4-debug-panel__label-line">Pre-filters (no OCR)</span>
        <label className="v4-debug-panel__check">
          <input
            type="checkbox"
            checked={enableMinInk}
            disabled={busy}
            onChange={(e) => onToggleMinInk(e.target.checked)}
          />
          <span style={{ flex: 1 }}>Drop empty rectangles</span>
        </label>
        <label htmlFor="v4-min-ink">
          Min ink density
          <span className="v4-debug-panel__value">
            {(inkPctDraft * 100).toFixed(2)}%
          </span>
        </label>
        <input
          id="v4-min-ink"
          type="range"
          min={0}
          max={0.05}
          step={0.001}
          value={inkPctDraft}
          disabled={busy || !enableMinInk}
          onChange={(e) => setInkPctDraft(Number(e.target.value))}
          onMouseUp={commitPrefilter}
          onTouchEnd={commitPrefilter}
          onKeyUp={commitPrefilter}
        />
        <label className="v4-debug-panel__check">
          <input
            type="checkbox"
            checked={enableMaxInk}
            disabled={busy}
            onChange={(e) => onToggleMaxInk(e.target.checked)}
          />
          <span style={{ flex: 1 }}>Drop ink-saturated rectangles</span>
        </label>
        <label htmlFor="v4-max-ink">
          Max ink density
          <span className="v4-debug-panel__value">
            {(maxInkPctDraft * 100).toFixed(0)}%
          </span>
        </label>
        <input
          id="v4-max-ink"
          type="range"
          min={0.05}
          max={0.95}
          step={0.05}
          value={maxInkPctDraft}
          disabled={busy || !enableMaxInk}
          onChange={(e) => setMaxInkPctDraft(Number(e.target.value))}
          onMouseUp={commitPrefilter}
          onTouchEnd={commitPrefilter}
          onKeyUp={commitPrefilter}
        />
        <label className="v4-debug-panel__check">
          <input
            type="checkbox"
            checked={enableSquarish}
            disabled={busy}
            onChange={(e) => onToggleSquarish(e.target.checked)}
          />
          <span style={{ flex: 1 }}>Drop squarish</span>
        </label>
        <label htmlFor="v4-aspect">
          Min aspect ratio
          <span className="v4-debug-panel__value">{aspectDraft.toFixed(1)}</span>
        </label>
        <input
          id="v4-aspect"
          type="range"
          min={1}
          max={10}
          step={0.1}
          value={aspectDraft}
          disabled={busy || !enableSquarish}
          onChange={(e) => setAspectDraft(Number(e.target.value))}
          onMouseUp={commitPrefilter}
          onTouchEnd={commitPrefilter}
          onKeyUp={commitPrefilter}
        />
      </div>

      <div className="v4-debug-panel__row">
        <span className="v4-debug-panel__label-line">Step 2 — VLM OCR</span>
        <div className="v4-debug-panel__crop-row">
          <button
            type="button"
            className="v4-debug-panel__btn"
            onClick={onRunVlmOcr}
            disabled={busy || !rectanglesReady}
          >
            {vlmAlreadyRun ? "Re-run VLM OCR" : "Run VLM OCR"}
          </button>
        </div>
        <p className="v4-debug-panel__hint">
          {vlmAlreadyRun
            ? "VLM has read every kept rectangle — click any blue box."
            : "Sends each kept rectangle to qwen3-vl. ~1–2 min, parallel."}
        </p>
      </div>
    </aside>
  );
}
