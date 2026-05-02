# ADR-0007 — PDF-as-canvas: vector source-of-truth, point-space coordinates

**Status:** Accepted, 2026-05-02
**Deciders:** Arjun Sarath
**Supersedes (partial):** ADR-0001 ingest assumptions for vector PDF inputs

## Context

V1 rasterizes PDFs to a 200 DPI PNG once at ingest (`app/pipeline/ingest.py`) and treats that raster as the source of truth for the rest of the pipeline. Three problems follow:

1. **Lossy first step.** A 36"×24" mechanical drawing rendered at 200 DPI is ~7,200 × 4,800 px; the VLM client then downscales to 1,568 px (`app/vlm/ollama.py:28`). Dimension callouts at 6 pt source size end up at ~1 px — unreadable. We can't recover detail we threw away at ingest.
2. **Pixel-locked coordinates.** Every `bbox` and `Geometry` is in image pixels. If the frontend renders at a different size — or if a downstream stage wants to re-render a region at higher DPI for the reviewer — coordinates have to be re-derived each time.
3. **No infinite zoom.** Engineers reviewing annotations want to zoom into a callout. A raster doesn't support that meaningfully past 100%.

V2's reviewer (ADR-0009) and tiled detector (ADR-0008) both want the ability to re-render an arbitrary region at an arbitrary DPI on demand. With a single fixed raster, both have to scale-up from a degraded source. The right shape is to keep the PDF as the canonical source and treat any raster as a *temporary view* of it.

## Decision

For PDF inputs (specifically: PDFs with a text layer or vector graphics — "vector PDFs"), the PDF itself is the source of truth through the pipeline.

- Open with `pymupdf` at ingest; keep the `Document` and `Page` open for the request lifetime.
- All coordinates in the API contract are **PDF points** (72/inch) for vector inputs.
- Tiles, crops, and reviewer inputs are rendered from the PDF on demand at chosen DPI via `page.get_pixmap(clip=rect_pt, dpi=local_dpi)` — lossless at any DPI.
- Frontend renders the PDF as the base layer with PDF.js; SVG overlay sits in PDF point space.

For non-vector inputs (PNG, JPG, and scanned PDFs — PDFs whose first page has < 50 chars of text and contains an image), v1's raster path is preserved verbatim. A single seam handles the split:

```python
class DrawingSource(BaseModel):
    kind: Literal["vector_pdf", "raster_pdf", "raster_image"]
    pdf_doc: Document | None
    page: Page | None
    page_size_pt: tuple[float, float] | None
    raster_probe: PILImage   # always populated — low-DPI render for stages
                             # that need a full-sheet image
    def render(self, rect_pt: RectPt, dpi: int) -> PILImage: ...
```

`ctx.source: DrawingSource` replaces v1's `ctx.image`. Stages call `ctx.source.render(rect, dpi)` and don't need to know whether the source is vector or raster.

The API response carries `coord_space: Literal["pdf_points", "pixels"]` so the frontend picks the correct renderer.

## Consequences

**Positive**
- Reviewer crops are lossless at any DPI — the dominant constraint behind ADR-0009 disappears.
- Per-tile DPI (smart-DPI in SOLUTION-DESIGN-V2 §5.2) is now actually achievable: each tile renders independently.
- Coordinates are canonical and stable; resizing the frontend doesn't break overlays.
- Frontend zoom on vector inputs is infinite (PDF.js, vector).
- Re-running detection on a low-confidence segment at higher DPI is a single function call, not a re-ingest.

**Negative**
- Two render paths in the frontend (`PdfCanvas` + existing `RasterCanvas`). Mitigated: same `<SegmentOverlay>` consumes both; the split is shallow.
- `pymupdf` becomes a hard backend dependency. Accepted — we already need it for the PDF text-layer fast path (font sizes for smart-DPI without OCR).
- Raster inputs (PNG/JPG/scanned PDFs) get none of the benefits. Accepted; v1 path is preserved for them.
- Coordinate-space awareness becomes a discipline: stages must convert at boundaries (VLM I/O is in normalized tile coords; everything else is points or pixels). Mitigated by `DrawingSource.render` being the only seam that needs to think in DPI.

## Alternatives considered

1. **Pre-render at very high DPI (e.g., 600).** Simplest. Rejected — file size on a 36"×24" sheet is ~150 MB at 600 DPI; memory pressure and round-trip latency become the bottleneck. Doesn't solve the coordinate-space or infinite-zoom problems.
2. **Multiple raster passes at different DPIs cached on context.** Cleaner than v1. Rejected — dual sources of truth, cache invalidation, and the same memory ceiling at high DPI.
3. **Convert PDF to SVG and use SVG as the canvas.** User's initial intuition. Rejected — VLMs do not read SVG; we'd rasterize SVG before each call anyway, gaining nothing and adding a conversion failure surface (PDF→SVG is not lossless for all PDFs).
4. **Defer the refactor to v3.** Considered. Rejected — the reviewer (ADR-0009) and tiling (ADR-0008) both depend on lossless re-render. Doing those first against pixel space means doing them twice.
