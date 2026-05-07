/**
 * Floating pipeline-tools panel — V4.5 dual-branch (rectangle + circle).
 * Exposes raster/binary inputs, the area-mask action, and three filter
 * sections (rectangle filters for ducts, circle filters for terminals,
 * 3-digit OCR for terminal CFM). Slider commits on release; toggles fire
 * the request immediately.
 */

import { useEffect, useState } from "react";

interface RectFilters {
  enableMinInk: boolean;
  minInkPct: number;
  enableMaxInk: boolean;
  maxInkPct: number;
  enableSquarish: boolean;
  minDuctAspect: number;
}

interface Props {
  rectDpi: number;
  inkThreshold: number;
  enableCircle: boolean;
  minCircularity: number;
  enableDivider: boolean;
  minDividerInkPct: number;
  enableThreeDigit: boolean;
  enableMinInk: boolean;
  minInkPct: number;
  enableMaxInk: boolean;
  maxInkPct: number;
  enableSquarish: boolean;
  minDuctAspect: number;
  enableVlmOcr: boolean;
  cropActive: boolean;
  busy: boolean;
  onCommitRectDpi: (next: number) => void;
  onCommitInk: (next: number) => void;
  onCommitPrefilter: (next: RectFilters & {
    enableCircle: boolean;
    minCircularity: number;
    enableDivider: boolean;
    minDividerInkPct: number;
    enableThreeDigit: boolean;
    enableVlmOcr: boolean;
  }) => void;
  onRedefineArea: () => void;
}

export function V4PipelineTools(props: Props) {
  const {
    rectDpi, inkThreshold,
    enableCircle, minCircularity,
    enableDivider, minDividerInkPct,
    enableThreeDigit,
    enableMinInk, minInkPct,
    enableMaxInk, maxInkPct,
    enableSquarish, minDuctAspect,
    enableVlmOcr,
    cropActive, busy,
    onCommitRectDpi, onCommitInk, onCommitPrefilter, onRedefineArea,
  } = props;

  // Collapsed-by-default for the demo: most users won't need to tune
  // individual filter knobs. A small "Debug" badge expands the full panel.
  const [expanded, setExpanded] = useState(false);
  const [rectDraft, setRectDraft] = useState(rectDpi);
  const [inkDraft, setInkDraft] = useState(inkThreshold);
  const [circDraft, setCircDraft] = useState(minCircularity);
  const [divDraft, setDivDraft] = useState(minDividerInkPct);
  const [inkPctDraft, setInkPctDraft] = useState(minInkPct);
  const [maxInkPctDraft, setMaxInkPctDraft] = useState(maxInkPct);
  const [aspectDraft, setAspectDraft] = useState(minDuctAspect);

  useEffect(() => setRectDraft(rectDpi), [rectDpi]);
  useEffect(() => setInkDraft(inkThreshold), [inkThreshold]);
  useEffect(() => setCircDraft(minCircularity), [minCircularity]);
  useEffect(() => setDivDraft(minDividerInkPct), [minDividerInkPct]);
  useEffect(() => setInkPctDraft(minInkPct), [minInkPct]);
  useEffect(() => setMaxInkPctDraft(maxInkPct), [maxInkPct]);
  useEffect(() => setAspectDraft(minDuctAspect), [minDuctAspect]);

  const commitRectDpi = () => {
    if (rectDraft !== rectDpi) onCommitRectDpi(rectDraft);
  };
  const commitInk = () => {
    if (inkDraft !== inkThreshold) onCommitInk(inkDraft);
  };
  const baseFilter = {
    enableCircle, enableDivider, enableThreeDigit,
    minCircularity: circDraft, minDividerInkPct: divDraft,
    enableMinInk, enableMaxInk, enableSquarish, enableVlmOcr,
    minInkPct: inkPctDraft, maxInkPct: maxInkPctDraft,
    minDuctAspect: aspectDraft,
  };
  const commitPrefilter = () => {
    const drifted =
      circDraft !== minCircularity
      || divDraft !== minDividerInkPct
      || inkPctDraft !== minInkPct
      || maxInkPctDraft !== maxInkPct
      || aspectDraft !== minDuctAspect;
    if (drifted) onCommitPrefilter(baseFilter);
  };
  const onToggleCircle = (next: boolean) =>
    onCommitPrefilter({ ...baseFilter, enableCircle: next });
  const onToggleDivider = (next: boolean) =>
    onCommitPrefilter({ ...baseFilter, enableDivider: next });
  const onToggleThreeDigit = (next: boolean) =>
    onCommitPrefilter({ ...baseFilter, enableThreeDigit: next });
  const onToggleMinInk = (next: boolean) =>
    onCommitPrefilter({ ...baseFilter, enableMinInk: next });
  const onToggleMaxInk = (next: boolean) =>
    onCommitPrefilter({ ...baseFilter, enableMaxInk: next });
  const onToggleSquarish = (next: boolean) =>
    onCommitPrefilter({ ...baseFilter, enableSquarish: next });
  const onToggleVlmOcr = (next: boolean) =>
    onCommitPrefilter({ ...baseFilter, enableVlmOcr: next });

  if (!expanded) {
    return (
      <button
        type="button"
        className="v4-debug-panel__toggle"
        onClick={() => setExpanded(true)}
        aria-label="Show pipeline debug tools"
      >
        Debug
      </button>
    );
  }

  return (
    <aside
      className="v4-debug-panel"
      role="region"
      aria-label="Pipeline tools"
    >
      <header className="v4-debug-panel__head">
        <strong>Pipeline</strong>
        <button
          type="button"
          className="v4-debug-panel__collapse"
          onClick={() => setExpanded(false)}
          aria-label="Collapse pipeline tools"
        >
          ×
        </button>
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
          DPI for contour detection. Lower = faster, fewer false positives
          from thin grey lines. Default 100. Changing this clears the area.
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
        <span className="v4-debug-panel__label-line">Rectangle filters (ducts)</span>
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
        <label className="v4-debug-panel__check">
          <input
            type="checkbox"
            checked={enableVlmOcr}
            disabled={busy}
            onChange={(e) => onToggleVlmOcr(e.target.checked)}
          />
          <span style={{ flex: 1 }}>VLM OCR (duct labels)</span>
        </label>
        <p className="v4-debug-panel__hint">
          Reads the rectangle dimension grammar (e.g. 22"x14", 14"ø) via
          masked-clip Tesseract→VLM ladder. Slow on a fresh kept set.
        </p>
      </div>

      <div className="v4-debug-panel__row">
        <span className="v4-debug-panel__label-line">Circle filter (terminals)</span>
        <label className="v4-debug-panel__check">
          <input
            type="checkbox"
            checked={enableCircle}
            disabled={busy}
            onChange={(e) => onToggleCircle(e.target.checked)}
          />
          <span style={{ flex: 1 }}>Apply circle filter</span>
        </label>
        <label htmlFor="v4-circularity">
          Min circularity
          <span className="v4-debug-panel__value">{circDraft.toFixed(2)}</span>
        </label>
        <input
          id="v4-circularity"
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={circDraft}
          disabled={busy || !enableCircle}
          onChange={(e) => setCircDraft(Number(e.target.value))}
          onMouseUp={commitPrefilter}
          onTouchEnd={commitPrefilter}
          onKeyUp={commitPrefilter}
        />
        <p className="v4-debug-panel__hint">
          4π·A/P². Circle≈1.0, square≈0.79, triangle≈0.6.
        </p>
      </div>

      <div className="v4-debug-panel__row">
        <span className="v4-debug-panel__label-line">Divider filter</span>
        <label className="v4-debug-panel__check">
          <input
            type="checkbox"
            checked={enableDivider}
            disabled={busy}
            onChange={(e) => onToggleDivider(e.target.checked)}
          />
          <span style={{ flex: 1 }}>Has horizontal divider</span>
        </label>
        <label htmlFor="v4-divider">
          Min centre-row ink
          <span className="v4-debug-panel__value">
            {(divDraft * 100).toFixed(0)}%
          </span>
        </label>
        <input
          id="v4-divider"
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={divDraft}
          disabled={busy || !enableDivider}
          onChange={(e) => setDivDraft(Number(e.target.value))}
          onMouseUp={commitPrefilter}
          onTouchEnd={commitPrefilter}
          onKeyUp={commitPrefilter}
        />
        <p className="v4-debug-panel__hint">
          Air terminals (A5) are circles bisected by a horizontal line. Keeps
          contours where the centre band's densest row is ≥ this fraction ink.
        </p>
      </div>

      <div className="v4-debug-panel__row">
        <span className="v4-debug-panel__label-line">Three-digit OCR filter</span>
        <label className="v4-debug-panel__check">
          <input
            type="checkbox"
            checked={enableThreeDigit}
            disabled={busy}
            onChange={(e) => onToggleThreeDigit(e.target.checked)}
          />
          <span style={{ flex: 1 }}>Has 3-digit number</span>
        </label>
        <p className="v4-debug-panel__hint">
          Per-bbox OCR ladder — Tesseract @600 → VLM @600, 900, 1200 DPI.
          Drops anything that never reads a standalone 3-digit token.
          OCR results cached by image hash; second run on same image is free.
        </p>
      </div>
    </aside>
  );
}
