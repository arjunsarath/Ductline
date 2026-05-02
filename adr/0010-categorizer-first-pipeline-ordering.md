# ADR-0010 — Categorizer-first pipeline ordering

**Status:** Accepted, 2026-05-02
**Deciders:** Arjun Sarath
**Depends on:** ADR-0007 (PDF-as-canvas), ADR-0008 (tiled detection)
**Extends:** ADR-0001 (hybrid detection stack), ADR-0003 (workflow-first)

## Context

V1 stage 4 (`app/pipeline/detect.py`) sends the entire drawing to the VLM. The VLM has no signal for *which part of the sheet is the plan view* and which parts are legend, schedule, notes, or section detail. As a result:

- Symbols inside the legend are detected as miniature ducts (legend rows often draw a small representative segment).
- Notes blocks with line-work or boxed text are detected as duct-like geometry.
- Section detail callouts and details inserts are treated as plan-view content.

These are the false positives surfaced during v1 testing. They are not a tuning problem — the model is being asked the wrong question (find ducts on this whole sheet) when the right question is narrower (find ducts in this rectangular plan-view region).

V1 also has only two region detectors (`app/cv/regions.py:find_title_block`, `find_schedule`). Legend, notes, and plan-view bounds are not identified anywhere.

## Decision

A new stage 3 — **Page Categorizer** — runs *before* duct detection and is mandatory in the default path. It produces:

```python
class PageLayout(BaseModel):
    title_block: RectPt | None
    schedule: RectPt | None
    legend: RectPt | None
    notes: list[RectPt]
    plan_view: RectPt   # the one we run detect on
```

### Pipeline ordering (new)

```
1. Ingest (smart-DPI, vector-aware)
2. Probe OCR (low-DPI global pass; cached)
3. Page Categorizer        ← NEW; depends on 1, 2
4. Legend Parser           ← NEW; depends on 3
5. Tiled Detect            ← scoped to layout.plan_view; depends on 3, 4
6. Text Extraction
7. Pressure-Class
8. Reviewer + Refinement
9. Assemble
```

### Categorizer implementation

Per ADR-0003, algorithmic-first:

- **Algorithmic primary.** Long horizontal/vertical Hough lines split the sheet into rectangles. Each rectangle is classified by:
  - Text-density heuristic (`notes` / `legend` are text-heavy; `plan_view` is line-work-heavy)
  - OCR keyword match against the region: `LEGEND`, `NOTES`, `SCHEDULE`, `PLAN`, `LEVEL`, `FLOOR`, `DETAIL`, `SECTION`
  - Position heuristic (title block conventionally bottom-right; legend conventionally right column; schedule conventionally adjacent to title block)
- **VLM fallback only** for rectangles the algorithmic pass cannot classify, via a typed `CategorizePageTool` schema. Same posture as v1's region-detect VLM fallback.

### Multi-plan-view handling (deferred edge case)

If the algorithmic pass identifies two or more rectangles that look like plan views (e.g., a sheet with both a roof plan and an enlarged mech-room plan), the categorizer:

1. Picks the largest by area as the active `plan_view`.
2. Surfaces `multi_plan_view_detected` in `ctx.errors`.
3. Stage 5 runs on the active plan view only.

This is a v2 limitation explicitly accepted in SOLUTION-DESIGN-V2 §11. Generalising to N plan views is a v3 candidate.

### Failure mode

If the categorizer finds no plan view at all (algorithmic pass exhausts its heuristics and the VLM fallback also returns nothing), the pipeline falls back to v1 behaviour: stage 5 runs on the whole sheet with a `categorizer_failed` warning. This preserves graceful degradation — v2 is never *worse* than v1.

## Consequences

**Positive**
- The detector operates on a tightly-bounded region. False positives in legends, notes, and section details are eliminated by construction (those regions never enter stage 5's input).
- The legend region is identified up front, which is the precondition for the legend parser (ADR-0009 reviewer benefits).
- Title block, schedule, and notes regions are now first-class outputs, available to any future stage that needs them.
- The categorizer's output is a single `PageLayout` value on the context — easy to introspect, easy to surface in the reasoning trace ("plan view: detected at PDF rect X; legend: detected at rect Y").

**Negative**
- Categorizer becomes a critical-path stage. If it fails, every downstream stage suffers. Mitigated by graceful degradation to v1 behaviour on failure.
- Algorithmic categorization on diverse drawing styles is empirical — we will iterate on the heuristics against the benchmark drawings. Mitigated by the typed VLM fallback for unclassified regions.
- Single plan view assumption is a real limitation. Acknowledged in V2 §11 and called out as a v3 candidate.
- An additional stage adds latency. Bounded — the algorithmic pass is fast (< 1 s on probe-resolution raster); VLM fallback only fires when needed.

## Alternatives considered

1. **Keep v1 ordering; filter false positives post-detection.** Rejected. Once the detector has produced ducts inside the legend region, there is no reliable way to know which were spurious without re-doing the categorization work. Filter post-hoc means filtering on the detector's output bbox — no better than running the categorizer first.
2. **Run categorizer and detect in parallel; intersect afterwards.** Rejected. Parallel work without dependency reduction is wasted compute on a slow local model. The detector's call cost is the dominant cost; running it on the whole sheet "in parallel" still pays that cost.
3. **Implicit categorization via prompt — tell the detector "ignore legends and notes."** Rejected. llama3.2-vision does not reliably honour exclusion clauses in prompts; we observed this in v1 prompt iteration. Categorization needs to happen *outside* the model, with the model only seeing the bounded region.
4. **Make the categorizer optional / feature-flagged.** Rejected. Without it, v2's tiling (ADR-0008) has no plan-view rect to tile. Tiling the full sheet brings back v1's false positives at greater cost.
5. **Generalize from day 1 to N plan views.** Rejected for v2. The single-plan-view case is the dominant one; the multi-plan-view case adds branching across stages 5–8 (per-view tiling, per-view stitching, per-view review) without commensurate benefit on the benchmark drawings. Deferred to v3.
