/**
 * Viewer routing tests — confirms the thin router picks the right canvas
 * based on `result.coord_space`. Heavy-lifting tests live in PdfCanvas /
 * RasterCanvas-equivalent suites.
 */

import { render, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Viewer } from "../Viewer";
import type { Viewport } from "../viewport";
import { makeResult } from "../../test/fixtures";

const baseViewport: Viewport = { scale: 1, tx: 0, ty: 0, rotationDeg: 0 };

function renderViewer(opts: {
  coord_space: "pdf_points" | "pixels";
  pageSize?: [number, number] | null;
  withFile?: boolean;
}) {
  const result = makeResult({
    coord_space: opts.coord_space,
    page_size_pt: opts.pageSize ?? null,
  });
  const file = opts.withFile
    ? new File([new Uint8Array([0x25, 0x50])], "x.pdf", { type: "application/pdf" })
    : null;

  return render(
    <Viewer
      result={result}
      file={file}
      selectedId={null}
      grayscale={false}
      viewport={baseViewport}
      onViewportChange={() => {}}
      onSelect={() => {}}
      onRotate={() => {}}
      onZoomBy={() => {}}
    />,
  );
}

describe("Viewer routing", () => {
  it("renders the PdfCanvas branch (a <canvas>) when coord_space is pdf_points and a File is present", async () => {
    const { container } = renderViewer({
      coord_space: "pdf_points",
      pageSize: [612, 792],
      withFile: true,
    });
    await waitFor(() => {
      expect(container.querySelector("canvas")).not.toBeNull();
    });
    // Raster path uses an <img> with the data URL — must be absent.
    expect(container.querySelector("img.viewer-raster")).toBeNull();
  });

  it("renders the RasterCanvas branch (an <img>) when coord_space is pixels", () => {
    const { container } = renderViewer({
      coord_space: "pixels",
      withFile: true,
    });
    const img = container.querySelector("img.viewer-raster");
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toContain("data:image/png;base64,");
  });

  it("falls back to RasterCanvas when coord_space is pdf_points but no File is provided", () => {
    const { container } = renderViewer({
      coord_space: "pdf_points",
      pageSize: [612, 792],
      withFile: false,
    });
    // Defensive fallback path — should render the raster img, not a canvas.
    expect(container.querySelector("img.viewer-raster")).not.toBeNull();
    expect(container.querySelector("canvas")).toBeNull();
  });
});
