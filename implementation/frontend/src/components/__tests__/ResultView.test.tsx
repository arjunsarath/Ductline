/**
 * ResultView segment_reviewed merge tests (SOLUTION-DESIGN-V2 §5.6).
 *
 * The reviewer streams per-segment updates after the preliminary
 * result lands. ResultView merges them via `applySegmentUpdate` so
 * the rendered segments reflect the latest reviewer verdict and
 * confidence band — verified here through the Sidebar's per-segment
 * row, which surfaces the pressure_class and reviewer-touched
 * reasoning_trace.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SegmentReviewedPayload } from "../../api/client";
import { ResultView } from "../ResultView";
import { makeResult, makeSegment } from "../../test/fixtures";

const PRELIMINARY_SEGMENT = makeSegment({
  id: "DUCT-1",
  pressure_class: {
    value: "LOW",
    confidence: "medium",
    source: "schedule:DUCT-SCHED-2/row-A",
    alternatives: [],
  },
  // Preliminary state — reviewer hasn't run yet.
  review_verdict: "not_reviewed",
  review_iterations: 0,
});

function buildResult() {
  return makeResult({ segments: [PRELIMINARY_SEGMENT] });
}

function rasterFile(): File {
  return new File([new Uint8Array([1, 2])], "x.png", { type: "image/png" });
}

describe("ResultView segment_reviewed merge (V2 §5.6)", () => {
  it("renders the preliminary confidence when no segmentUpdates are present", () => {
    const result = buildResult();
    render(
      <ResultView
        filename="x.png"
        file={rasterFile()}
        result={result}
        onReset={() => {}}
      />,
    );
    // Sidebar pill shows the original "medium" confidence band.
    const pill = document.querySelector(".confidence-pill.conf-medium");
    expect(pill).not.toBeNull();
    expect(pill?.textContent).toBe("medium");
    // No "high" pill: nothing has been promoted.
    expect(document.querySelector(".confidence-pill.conf-high")).toBeNull();
  });

  it("applies a segment_reviewed update to the matching segment in-place", () => {
    const result = buildResult();
    const update: SegmentReviewedPayload = {
      segment_id: "DUCT-1",
      verdict: "plausible",
      iterations: 1,
      pressure_class: {
        value: "LOW",
        // Reviewer bumped medium → high (V2 §5.6 confidence ladder).
        confidence: "high",
        source: "schedule:DUCT-SCHED-2/row-A",
        alternatives: [],
      },
      reasoning_trace: [
        { stage: "vlm_detect_tile", evidence: "initial detection" },
        {
          stage: "reviewer_critique",
          evidence: "plausible: terminates at AHU",
          iteration: 1,
        },
      ],
    };

    render(
      <ResultView
        filename="x.png"
        file={rasterFile()}
        result={result}
        segmentUpdates={{ "DUCT-1": update }}
        reviewerStatus={{ current: 1, total: 1, running: true }}
        onReset={() => {}}
      />,
    );

    // The sidebar row should now render with the bumped "high"
    // confidence pill — proving the merge applied.
    const highPill = document.querySelector(".confidence-pill.conf-high");
    expect(highPill).not.toBeNull();
    expect(highPill?.textContent).toBe("high");
    expect(document.querySelector(".confidence-pill.conf-medium")).toBeNull();

    // The reviewer banner is visible while running.
    const banner = screen.getByRole("status");
    expect(banner.textContent).toContain("Reviewer running");
    expect(banner.textContent).toContain("1 / 1");

    // The newest reasoning step (last in the trace) is the reviewer
    // critique — the sidebar row's trail shows it verbatim.
    const trace = document.querySelector(".sidebar-row-trace");
    expect(trace?.textContent).toContain("reviewer_critique");
    expect(trace?.textContent).toContain("plausible: terminates at AHU");
  });

  it("ignores updates whose segment_id has no match in the preliminary result", () => {
    const result = buildResult();
    const orphan: SegmentReviewedPayload = {
      segment_id: "DUCT-UNKNOWN",
      verdict: "implausible",
      iterations: 2,
      pressure_class: {
        value: "LOW",
        confidence: "low",
        source: "schedule:DUCT-SCHED-2/row-A",
        alternatives: [],
      },
      reasoning_trace: [],
    };

    render(
      <ResultView
        filename="x.png"
        file={rasterFile()}
        result={result}
        segmentUpdates={{ "DUCT-UNKNOWN": orphan }}
        reviewerStatus={null}
        onReset={() => {}}
      />,
    );

    // No mutation: the rendered confidence is still "medium".
    expect(
      document.querySelector(".confidence-pill.conf-medium")?.textContent,
    ).toBe("medium");
    expect(document.querySelector(".confidence-pill.conf-low")).toBeNull();
    // Banner is hidden when reviewerStatus is null / not running.
    expect(screen.queryByRole("status")).toBeNull();
  });
});
