/**
 * Collapsible assumption banner — SOLUTION-DESIGN-V4 §11 acceptance:
 *   "Assumption list visible on the upload page (or in the result panel)
 *    with each MVP assumption flagged."
 *
 * Copy is verbatim from §2 (A1–A15).
 */

import { useState } from "react";

const ASSUMPTIONS: { id: string; text: string }[] = [
  { id: "A1", text: "Dimension labels live inside the duct fill (one label per segment)." },
  { id: "A2", text: "Only one numeric token lives inside a duct interior (no other measurements share the space)." },
  { id: "A3", text: "Rectangular duct labels are always WxH (width × height)." },
  { id: "A4", text: "No insulation/double-line wrap pattern. If encountered, the inner bbox is the duct." },
  {
    id: "A5",
    text: "Air terminal symbol = circle with horizontal divider; top half = type letter (ignored in MVP), bottom half = numeric CFM.",
  },
  {
    id: "A6",
    text: "A segment is a region bounded by two perpendicular cross-cut bars at its ends. Transitions, elbows, tees, Y-branches, and equipment boxes are connectors, not segments.",
  },
  {
    id: "A7",
    text: "Solid-touching ducts are connected. Dashed rendering = duct passes underneath; logically a single segment, displayed with alpha overlay so the overlap appears darker.",
  },
  { id: "A8", text: "Drawing is to scale. Label text is axis-aligned (0° or 90°), never angled to follow the duct." },
  {
    id: "A9",
    text: "Unlabeled segments (e.g., bent continuation at the same size) are sized by direct pixel measurement × scale, not by inheritance.",
  },
  {
    id: "A10",
    text: "A single segment can host N air terminals along its length (vents in a dining-room run). CFM varies along the segment; segment length is the full run.",
  },
  {
    id: "A11",
    text: "Connector materials (rigid vs flex) vary; for MVP both are treated as a generic connector with a default equivalent length the user can override.",
  },
  { id: "A12", text: "Grey-shaded regions in the drawing are non-HVAC architectural fill and are stripped during preprocessing as noise." },
  { id: "A13", text: "Open-ended ducts have no airflow unless tagged with a terminal symbol or a user-entered CFM." },
  { id: "A14", text: "All CFM values for MVP are read from terminal symbols (not from plan-note prose)." },
  { id: "A15", text: "Single-page PDF only. The user picks the page on upload if the source has more than one." },
];

export function V4AssumptionBanner() {
  const [open, setOpen] = useState(false);
  return (
    <div className={`v4-assumption-banner${open ? " is-open" : ""}`}>
      <button
        type="button"
        className="v4-assumption-toggle"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="v4-assumption-chevron" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        <strong>MVP assumptions (A1–A15)</strong>
        <span className="muted">click to {open ? "collapse" : "expand"}</span>
      </button>
      {open && (
        <ol className="v4-assumption-list">
          {ASSUMPTIONS.map((a) => (
            <li key={a.id}>
              <span className="v4-assumption-tag mono">{a.id}</span>
              <span>{a.text}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
