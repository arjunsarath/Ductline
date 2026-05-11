"use client";

import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// react-pdf needs the worker source URL set once at module load. We use the
// pdfjs-dist build that ships with react-pdf to keep the worker version in sync.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

export type PdfRenderInfo = {
  /** Rendered (CSS) size in pixels. */
  width: number;
  height: number;
  /** Native page size in PDF points. */
  pointWidth: number;
  pointHeight: number;
};

type Props = {
  file: File | Blob | string;
  pageNumber: number;
  /**
   * Optional fixed width in CSS pixels. If omitted, the page renders to fill
   * the container width (the previous default behavior).
   */
  width?: number;
  onRender: (info: PdfRenderInfo) => void;
  onLoad: (pageCount: number) => void;
  onError?: (err: Error) => void;
  children?: React.ReactNode;
};

// Engineering PDFs can have thousands of vector paths. Without this cap, retina
// (DPR≥2) tabs OOM-crashed Chrome on large drawings.
const RENDER_DPR = 1;

export default function PdfPage({
  file,
  pageNumber,
  width,
  onRender,
  onLoad,
  onError,
  children,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [observedWidth, setObservedWidth] = useState<number>(0);

  // ResizeObserver only runs when no explicit width is supplied. The
  // observer fires asynchronously, so setState here is the external-sync
  // pattern React allows.
  useEffect(() => {
    if (width !== undefined) return;
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (w > 0) setObservedWidth(w);
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, [width]);

  const containerWidth = width ?? observedWidth;

  return (
    <div
      ref={containerRef}
      className="relative"
      style={width !== undefined ? { width } : { width: "100%" }}
    >
      {containerWidth > 0 && (
        <Document
          file={file}
          onLoadSuccess={({ numPages }) => onLoad(numPages)}
          onLoadError={(err) => onError?.(err)}
          loading={
            <div className="p-6 text-sm text-muted-foreground">Loading PDF…</div>
          }
          error={
            <div className="p-6 text-sm text-destructive">
              Failed to load PDF.
            </div>
          }
        >
          <Page
            pageNumber={pageNumber}
            width={containerWidth}
            devicePixelRatio={RENDER_DPR}
            renderAnnotationLayer={false}
            renderTextLayer={false}
            onRenderSuccess={(page) =>
              onRender({
                width: page.width,
                height: page.height,
                // originalWidth/Height are the page's native size in PDF points.
                pointWidth: page.originalWidth,
                pointHeight: page.originalHeight,
              })
            }
            onRenderError={(err) => onError?.(err)}
          />
        </Document>
      )}
      {children}
    </div>
  );
}
