/**
 * Vitest setup. Adds @testing-library/jest-dom matchers and a global mock
 * for `pdfjs-dist` so PdfCanvas can mount in jsdom without spinning up the
 * worker (which would explode without the bundler resolving the `?url`
 * worker entry).
 *
 * Tests that need to override the mock can call `vi.doMock("pdfjs-dist", …)`
 * before importing the SUT.
 */

import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// Vite's `?url` import returns a string at runtime; jsdom doesn't run Vite,
// so we shim the worker URL module to a noop string. This is registered
// before any module resolves the worker import.
vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({
  default: "mock-pdf-worker.mjs",
}));

// Default mock: a minimal pdfjs surface that resolves immediately. Tests
// that need to drive the loading task can `vi.mocked(...)` after import.
vi.mock("pdfjs-dist", () => {
  const mockPage = {
    getViewport: ({ scale }: { scale: number }) => ({
      width: 600 * scale,
      height: 800 * scale,
    }),
    render: () => ({
      promise: Promise.resolve(),
      cancel: () => {},
    }),
  };
  const mockDoc = {
    getPage: () => Promise.resolve(mockPage),
    destroy: () => Promise.resolve(),
  };
  return {
    GlobalWorkerOptions: { workerSrc: "" },
    getDocument: () => ({
      promise: Promise.resolve(mockDoc),
    }),
  };
});

// Stable devicePixelRatio for predictable canvas sizing in tests.
Object.defineProperty(window, "devicePixelRatio", {
  configurable: true,
  value: 1,
});

// jsdom's Blob/File implementations don't always expose .arrayBuffer().
// PdfCanvas calls it during load — return an empty buffer (the pdfjs mock
// doesn't actually inspect it). Real browsers ship arrayBuffer() natively.
Blob.prototype.arrayBuffer = function arrayBuffer() {
  return Promise.resolve(new ArrayBuffer(0));
};

// jsdom returns null from <canvas>.getContext("2d"); PdfCanvas only uses the
// returned context to pass to pdfjs (which is mocked) — return a minimal
// stub so the call site doesn't bail out early.
if (typeof HTMLCanvasElement !== "undefined") {
  const proto = HTMLCanvasElement.prototype as unknown as {
    getContext: unknown;
    __ctxStub?: boolean;
  };
  if (!proto.__ctxStub) {
    proto.getContext = () => ({}) as unknown;
    proto.__ctxStub = true;
  }
}
