/**
 * PdfCanvas smoke tests — verifies the vector path mounts, sets the SVG
 * viewBox to PDF-point space, renders one mark per segment, and wires up
 * onSelect on click. The pdfjs module is mocked in `src/test/setup.ts`.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PdfCanvas } from "../PdfCanvas";
import type { Viewport } from "../viewport";
import { makeResult, makeSegment } from "../../test/fixtures";

function makeFile(): File {
  return new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], "drawing.pdf", {
    type: "application/pdf",
  });
}

const baseViewport: Viewport = { scale: 1, tx: 0, ty: 0, rotationDeg: 0 };

function renderPdf(opts: {
  selectedId?: string | null;
  onSelect?: (id: string | null) => void;
} = {}) {
  const result = makeResult({
    coord_space: "pdf_points",
    page_size_pt: [612, 792],
    width_px: 6120,
    height_px: 7920,
    segments: [
      makeSegment({ id: "S-001" }),
      makeSegment({
        id: "S-002",
        geometry: {
          type: "bbox",
          points: [
            [200, 200],
            [400, 300],
          ],
        },
      }),
    ],
  });

  const onSelect = opts.onSelect ?? vi.fn();

  return {
    onSelect,
    ...render(
      <PdfCanvas
        file={makeFile()}
        pageSizePt={[612, 792]}
        rotation={0}
        result={result}
        selectedId={opts.selectedId ?? null}
        grayscale={false}
        viewport={baseViewport}
        onViewportChange={() => {}}
        onSelect={onSelect}
        onRotate={() => {}}
        onZoomBy={() => {}}
      />,
    ),
  };
}

describe("PdfCanvas", () => {
  it("renders a <canvas> as the base layer", async () => {
    const { container } = renderPdf();
    const canvas = container.querySelector("canvas");
    expect(canvas).not.toBeNull();
  });

  it("uses PDF-point space for the SVG viewBox once the page loads", async () => {
    const { container } = renderPdf();
    await waitFor(() => {
      const svg = container.querySelector("svg.viewer-overlay");
      expect(svg).not.toBeNull();
    });
    const svg = container.querySelector("svg.viewer-overlay")!;
    expect(svg.getAttribute("viewBox")).toBe("0 0 612 792");
  });

  it("renders one mark per segment", async () => {
    const { container } = renderPdf();
    await waitFor(() => {
      expect(container.querySelector("svg.viewer-overlay")).not.toBeNull();
    });
    // First segment is a polyline, second is a bbox -> rect.
    const polylines = container.querySelectorAll("svg.viewer-overlay polyline");
    const rects = container.querySelectorAll("svg.viewer-overlay rect");
    expect(polylines.length).toBe(1);
    expect(rects.length).toBe(1);
  });

  it("calls onSelect when a segment is clicked", async () => {
    const onSelect = vi.fn();
    const { container } = renderPdf({ onSelect });
    await waitFor(() => {
      expect(container.querySelector("svg.viewer-overlay polyline")).not.toBeNull();
    });
    const polyline = container.querySelector("svg.viewer-overlay polyline")!;
    fireEvent.click(polyline);
    expect(onSelect).toHaveBeenCalledWith("S-001");
  });

  it("renders the canvas controls overlay", async () => {
    renderPdf();
    expect(screen.getByLabelText("Zoom in")).toBeInTheDocument();
    expect(screen.getByLabelText("Zoom out")).toBeInTheDocument();
    expect(screen.getByLabelText("Rotate 90 degrees")).toBeInTheDocument();
  });
});
