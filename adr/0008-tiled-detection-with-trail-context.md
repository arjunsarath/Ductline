# ADR-0008 — Tiled detection with trail context

**Status:** Accepted, 2026-05-02
**Deciders:** Arjun Sarath
**Depends on:** ADR-0007 (PDF-as-canvas), ADR-0010 (Categorizer-first ordering)
**Replaces:** v1 single-call detect in `app/pipeline/detect.py`

## Context

V1's stage 4 sends the entire drawing to the VLM in one call. `OllamaVisionClient.detect` (`app/vlm/ollama.py:37`) downscales the input to a 1,568 px long edge before the request, on the premise that this matches llama3.2-vision's "native input edge."

That premise is wrong in two ways:

1. **The actual native window is smaller.** llama3.2-vision processes images as 4 patches of 560² (= ~1,120 px effective). Sending 1,568 px forces the model to downscale internally — the upscale we paid for at 200 DPI is wasted.
2. **One window cannot cover a sheet at usable detail.** A typical mechanical sheet at the DPI required to keep small text legible (300+) is 7,000–10,000 px on the long edge. Squeezing that into 1,120 px makes 6 pt dimension text 1–2 px tall.

The hallucination guards in `app/vlm/ollama.py:140-162` (duplicate-bbox, tenth-grid, count-limit checks) exist *because* llama3.2-vision is already failing on full-sheet inputs in v1. We are defending against a problem we created at the I/O boundary.

V2 needs to match the model to its sweet spot, not fight the downscale.

## Decision

Detection runs on **tiles**, not the full sheet. Specifically:

- Stage 5 (the renamed v2 detect stage) operates on the `plan_view` region identified by the categorizer (ADR-0010), never on the whole sheet.
- The plan-view rect is split into **1,100 px square tiles** with **15% overlap**. 1,100 px matches the model's native 4-patch window; 15% overlap is large enough that any duct > 150 px appears whole in at least one tile.
- Each tile is rendered directly from the PDF (or cropped from the cached raster for non-vector inputs) at a per-tile DPI computed from the smallest text in that tile (SOLUTION-DESIGN-V2 §5.2). Empty tiles get a low DPI; text-dense tiles get high.
- Each tile gets a **trail context** in its prompt:
  - Tile coordinates: `"tile (row 2, col 3) of (5, 4)"`
  - Legend mapping from ADR-0010's stage 4 (line styles, symbols, abbreviations)
  - List of segment IDs + bboxes already found in tiles above and to the left of this one — the model continues numbering and is anchored to the visual style of segments already accepted in this drawing
- After all tiles run, results are **stitched in PDF point space**:
  - Each tile's normalized bbox is mapped back to PDF points using the tile's `rect`.
  - Cross-tile duplicates are deduplicated by IoU > 0.4 — keep the segment whose bbox is most central to its tile.
  - One global stitch-pass VLM call (the full plan_view at lower DPI + the merged segment list) reconciles cross-tile splits where a single duct appeared partially in two tiles.
- The stitched output goes to the reviewer (ADR-0009).

## Consequences

**Positive**
- Each tile is sent to the VLM at the DPI the *model can use* — small-text loss disappears.
- Trail context gives the model continuity (numbering, style anchor) and prevents re-detection in overlap regions.
- Stitching is a deterministic merge step in PDF point space — no model involvement.
- Per-segment retry / refinement (ADR-0009) becomes natural: re-render that one tile, re-call.

**Negative**
- VLM call count rises from 1 to ~12–15 per drawing for a typical mechanical sheet (4×3 tile grid + stitch). On local Ollama this adds ~60 s of wall-clock time per drawing. Mitigated by parallel execution where the model server allows it; otherwise accepted within the v2 latency budget.
- IoU dedup tuning (`0.4` starting point) is empirical and will need adjustment per drawing class. Mitigated: we instrument cross-tile dedup decisions and surface them in the reasoning trace for the first few benchmark drawings.
- Per-tile prompt has to vary (coordinates, trail) — prompt management becomes versioned templates with parameter substitution. Accepted; templates live in `app/vlm/prompts/`.
- Tiles that span two visual regions (e.g., half plan-view, half title-block edge) confuse the model. Mitigated by ADR-0010's plan-view bounding — tiles only exist *inside* the plan-view rect.

## Alternatives considered

1. **Bigger model with bigger context window.** Rejected at the architectural seam — even on Claude vision (which tiles internally up to ~3,072 px), the same fundamentals apply, just at a different scale. Tiling is the correct shape regardless of model.
2. **Single high-DPI tile of just the plan-view region.** Rejected. Plan views on mechanical sheets are typically 6,000+ px on the long edge at the DPI we need; one tile at that size still hits the model's downscale.
3. **No overlap, just adjacent tiles.** Rejected. Ducts crossing tile boundaries get split into two independent segments with different IDs; stitching becomes ambiguous.
4. **Server-side tiling by Ollama.** Rejected. Ollama's downscale logic is not tile-aware; it crops or resamples without preserving small features at boundaries. We need explicit control.
5. **Tile, but no trail context — independent calls.** Rejected. Without trail context, segment numbering collides across tiles and the model has no style anchor; we get N independent answers that don't share conventions.
6. **Tile and merge with no global stitch pass.** Rejected. Cross-tile splits (one duct appearing in two adjacent tiles) need a model-aware reconciliation; pure IoU dedup misses cases where the bboxes don't overlap but the underlying duct is continuous.
