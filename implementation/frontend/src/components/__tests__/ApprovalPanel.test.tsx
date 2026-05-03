/**
 * ApprovalPanel interactive editor tests. Exercises the categorize
 * gate's editor affordances introduced in the V2 §5.8 inline-correction
 * follow-up:
 *
 *   1. Dragging a corner handle resizes the corresponding bbox edges
 *      (state mirrors the new geometry).
 *   2. Clicking the X delete control removes the rect from the layout.
 *   3. Clicking "+ Add legend block" enters draw mode, then a click+
 *      drag on the SVG creates a new legend rect.
 *
 * jsdom doesn't implement getScreenCTM/createSVGPoint by default; we
 * stub both at the SVG element level so toViewBox() returns identity-
 * mapped coordinates (clientX → viewBox x). That lets us drive pointer
 * deltas in viewBox space directly.
 *
 * Tests intentionally avoid asserting on the network layer — approveGate
 * is mocked via vi.mock so no real fetch fires.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalPanel } from "../ApprovalPanel";
import type { CategorizeApprovalPayload } from "../../api/client";

vi.mock("../../api/client", () => ({
  approveGate: vi.fn().mockResolvedValue(undefined),
  cancelDetection: vi.fn().mockResolvedValue(undefined),
}));

function basePayload(): CategorizeApprovalPayload {
  return {
    drawing_id: "test-drw",
    coord_space: "pixels",
    page_size_pt: null,
    raster_probe_size: [800, 600],
    raster_probe_data_url: "data:image/png;base64,iVBORw0KGgo=",
    rotation_applied: 0,
    layout: {
      plan_view: [100, 100, 700, 500],
      legend: [710, 100, 790, 400],
      schedule: null,
      title_block: null,
      notes: [],
    },
    errors: [],
  };
}

/** Stub jsdom's missing SVG helpers so the editor's screen-to-viewBox
 *  math returns identity-mapped coordinates. Also installs a PointerEvent
 *  shim so @testing-library's fireEvent.pointerXxx events propagate
 *  clientX/clientY via React's synthetic-event layer. */
function stubSvgGeometry() {
  // jsdom doesn't ship a PointerEvent constructor; @testing-library falls
  // back to window.Event which doesn't carry MouseEvent-like coordinate
  // fields (React's synthetic event reads clientX off the nativeEvent
  // and gets undefined). Aliasing to MouseEvent is the documented
  // workaround — MouseEvent supports clientX/clientY in eventInit and
  // dispatches as a real coordinate-bearing event.
  // PointerEvent typing in lib.dom requires extra fields MouseEvent
  // doesn't carry — but at runtime React only reads clientX/clientY,
  // so the alias is functionally complete. Cast through unknown to
  // bypass the lib.dom mismatch.
  const w = window as unknown as { PointerEvent?: unknown };
  if (!w.PointerEvent) {
    w.PointerEvent = window.MouseEvent;
  }

  // jsdom returns null for getScreenCTM by default; stub to identity.
  // SVGSVGElement.prototype is the base prototype for <svg> nodes.
  const protoSvg = SVGSVGElement.prototype as unknown as Record<string, unknown>;
  protoSvg.createSVGPoint = function () {
    const pt: {
      x: number;
      y: number;
      matrixTransform: (m: unknown) => { x: number; y: number };
    } = {
      x: 0,
      y: 0,
      matrixTransform(_m: unknown) {
        return { x: this.x, y: this.y };
      },
    };
    return pt;
  };
  // getScreenCTM returns an identity-ish object whose .inverse() is
  // also identity — toViewBox passes the result to matrixTransform
  // which we've already stubbed to passthrough.
  protoSvg.getScreenCTM = function () {
    return { inverse: () => ({}) } as unknown as DOMMatrix;
  };
  // setPointerCapture is a no-op in jsdom; stub to avoid throw.
  Element.prototype.setPointerCapture = () => {};
}

const drawingId = "test-drw";

describe("ApprovalPanel categorize editor", () => {
  beforeEach(() => {
    stubSvgGeometry();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("resizes a bbox when a corner handle is dragged", () => {
    const { container } = render(
      <ApprovalPanel drawingId={drawingId} gate={{ gate: "categorize", payload: basePayload() }} />,
    );

    // Initial plan_view rect: [100, 100, 700, 500]. The "se" corner
    // handle sits at (700, 500). Drag it to (740, 540) — width grows
    // 40px, height grows 40px.
    const seHandle = container.querySelector(
      'g[data-region="plan_view"] circle[data-handle="se"]',
    );
    expect(seHandle).not.toBeNull();
    fireEvent.pointerDown(seHandle!, { clientX: 700, clientY: 500, pointerId: 1 });
    const svg = container.querySelector("svg.approval-overlay")!;
    fireEvent.pointerMove(svg, { clientX: 740, clientY: 540, pointerId: 1 });
    fireEvent.pointerUp(svg, { clientX: 740, clientY: 540, pointerId: 1 });

    const planRect = container.querySelector('g[data-region="plan_view"] > rect');
    expect(planRect).not.toBeNull();
    // After resize via SE handle: width = 700-100 + 40 = 640; height = 500-100 + 40 = 440.
    expect(planRect!.getAttribute("width")).toBe("640");
    expect(planRect!.getAttribute("height")).toBe("440");
    // Origin unchanged.
    expect(planRect!.getAttribute("x")).toBe("100");
    expect(planRect!.getAttribute("y")).toBe("100");
  });

  it("removes a rect from the edit state when the delete X is clicked", () => {
    const { container } = render(
      <ApprovalPanel drawingId={drawingId} gate={{ gate: "categorize", payload: basePayload() }} />,
    );

    // Confirm the legend rect is present before the click.
    expect(container.querySelector('g[data-region="legend"]')).not.toBeNull();

    const legendDelete = container.querySelector(
      'g[data-region="legend"] g[data-action="delete"]',
    );
    expect(legendDelete).not.toBeNull();
    fireEvent.pointerDown(legendDelete!, { clientX: 790, clientY: 100, pointerId: 1 });

    // Legend group is gone — render path skips null rects.
    expect(container.querySelector('g[data-region="legend"]')).toBeNull();
    // Sidebar status switches to "absent" for legend.
    const legendItem = screen.getAllByText("legend").find(
      (el) => el.parentElement?.tagName.toLowerCase() === "li",
    );
    expect(legendItem).toBeDefined();
    const statusEl = legendItem!.parentElement!.querySelector(
      ".approval-overlay-absent",
    );
    expect(statusEl).not.toBeNull();
  });

  it('creates a new legend rect after "+ Add legend block" + draw drag', () => {
    // Start with no legend so the legend slot is empty before drawing.
    const payload: CategorizeApprovalPayload = {
      ...basePayload(),
      layout: {
        plan_view: [100, 100, 700, 500],
        legend: null,
        schedule: null,
        title_block: null,
        notes: [],
      },
    };
    const { container } = render(
      <ApprovalPanel drawingId={drawingId} gate={{ gate: "categorize", payload }} />,
    );

    // Click the "Add legend block" toolbar button to enter draw mode.
    const addLegend = container.querySelector(
      'button[data-add-kind="legend"]',
    ) as HTMLButtonElement | null;
    expect(addLegend).not.toBeNull();
    fireEvent.click(addLegend!);
    // Banner appears.
    expect(screen.queryByRole("status")).not.toBeNull();

    // Click + drag on the SVG to draw the rect.
    const svg = container.querySelector("svg.approval-overlay")!;
    fireEvent.pointerDown(svg, { clientX: 720, clientY: 120, pointerId: 1 });
    fireEvent.pointerMove(svg, { clientX: 780, clientY: 380, pointerId: 1 });
    fireEvent.pointerUp(svg, { clientX: 780, clientY: 380, pointerId: 1 });

    // Legend rect now present in the editor.
    const legendGroup = container.querySelector('g[data-region="legend"]');
    expect(legendGroup).not.toBeNull();
    const legendRect = legendGroup!.querySelector("rect");
    expect(legendRect).not.toBeNull();
    expect(legendRect!.getAttribute("x")).toBe("720");
    expect(legendRect!.getAttribute("y")).toBe("120");
    expect(legendRect!.getAttribute("width")).toBe("60");
    expect(legendRect!.getAttribute("height")).toBe("260");
    // Draw-mode banner cleared after the rect committed.
    expect(screen.queryByRole("status")).toBeNull();
  });
});
