# ADR-0009 — Multi-agent reviewer with bounded refinement loop

**Status:** Accepted, 2026-05-02
**Deciders:** Arjun Sarath
**Depends on:** ADR-0007 (PDF-as-canvas — required for lossless reviewer crops), ADR-0008 (tiled detection)
**Extends:** ADR-0003 (workflow-first; agents only with typed tools)

## Context

V1 produces low confidence on most segments. The cause is structural, not a tuning failure: confidence is derived from OCR confidence alone (`app/pipeline/extract.py:_bucket_ocr_confidence`), and the pipeline is linear — there is no second look at a detection that is geometrically and contextually correct but lacks an OCR callout. There is also no mechanism to *flag* a detection that survived the geometric checks but is implausible by domain rules (a duct segment that terminates in open space, a `⌀` callout on a rectangular geometry).

Two things need to happen:

1. A **second pass** that judges each segment against MEP domain priors and the legend conventions of *this drawing*, not generic visual pattern matching.
2. A **mechanism to elevate confidence** when the system is right, and downgrade it (with a critique) when it isn't.

This pass also has to run on llama3.2-vision in v1 of v2 — an 11B local model. That constrains the design heavily: small models fabricate continuous confidence scores, struggle with long structured output, and oscillate when looped without bounds.

## Decision

A new stage 8 — **Reviewer + Refinement Loop** — runs after the deterministic pipeline (stages 1–7) has produced a draft.

### Reviewer call (per segment)

The reviewer is a separate agent invocation (`ReviewerClient` Protocol, parallel to `VLMClient`) that takes:

- A high-DPI crop of the segment + ~300 px padding, rendered fresh from the source via ADR-0007's `DrawingSource.render`
- The segment's metadata (geometry, dimension, pressure class, reasoning trace so far)
- The legend mapping from stage 4 (when present)
- A system prompt encoding MEP domain priors:
  - Every duct must terminate at another duct (fitting), an air-handling unit, a plenum, a riser, or a terminal device.
  - A round-duct callout (`⌀`) cannot apply to a rectangular geometry, and vice versa.
  - Two parallel "ducts" closer than ~6" apart are usually one duct's two walls misdetected as separate runs.
  - Schedule callouts must fall within the schedule's enumerated sizes for the matched system.

Output is **discrete** — no continuous scores:

```python
class ReviewerVerdict(BaseModel):
    verdict: Literal["plausible", "implausible", "uncertain"]
    reason: str   # one sentence
```

### Confidence math (deterministic in code, not in the model)

```
plausible    → bump confidence band up   (low → medium, medium → high)
implausible  → bump confidence band down (high → medium, medium → low)
uncertain    → no change
```

We do not let the model emit a continuous confidence score because small models fabricate floats. The reviewer says *whether* it agrees; the system decides *what that means* for the band.

### Refinement loop

If the verdict is not `plausible`, the segment goes through a refinement call:

```python
vlm.refine_segment(crop, critique=verdict.reason, previous=segment)
```

The refinement call asks the detector VLM to reconsider just this segment given the reviewer's critique. The output is one segment with possibly revised geometry/shape/nearby_text. The reviewer then re-runs.

### Bounds

| Bound | Default | Rationale |
|---|---|---|
| `reviewer_max_iterations` (per segment) | 2 | Self-Refine / Reflexion literature: most gain at 1→2; 2→3 marginal on small models. Configurable up to 3 for Claude later. |
| Oscillation early-exit | IoU > 0.95 between iterations | Stuck model produces near-identical geometry; further iterations are wasted calls. |
| `reviewer_per_drawing_budget` | 40 total VLM calls | Hard stop. Surfaced as a warning. Prevents pathological drawings from running for 30 minutes. |
| Initial-verdict trigger | Loop only on `implausible` / `uncertain` | `plausible` segments don't need a second look — they're already accepted with a confidence bump. |

### V2 does not reject

A segment ending the loop with verdict `implausible` stays in the result with `confidence: low` and the critique threaded into the reasoning trace as `stage: "reviewer_critique"`. Users see the multi-agent reasoning and curate visually. Hard rejection waits for v3, where it can be paired with feedback capture (V2 §12.1).

## Consequences

**Positive**
- Confidence becomes informative. A `high` segment is now corroborated by two independent passes, not just one OCR match.
- The reviewer's critiques are the v2 selling point — they appear in the reasoning trace and demonstrate engineer-style review of the auto-detection.
- Discrete verdicts + deterministic confidence math = no fabricated floats and no provider-specific tuning required.
- The same `ReviewerClient` Protocol works for llama3.2-vision now and Claude later; only the prompt and iteration cap change.

**Negative**
- Latency. Up to 40 VLM calls per drawing on local Ollama → minutes. Within v2's accepted budget; outside v1's 30 s P50 target. Surfaced in SOLUTION-DESIGN-V2 §11.
- Prompt iteration risk on llama3.2-vision. Mitigated by per-prompt-version verdict-distribution tracking — if a prompt drift causes the reviewer to flip on >80% of segments, we treat that pass as failed and fall back to v1 confidence.
- Reviewer can be wrong. Mitigated by *not* giving it rejection power in v2; worst-case it adjusts confidence incorrectly, never deletes work.
- More moving parts than v1. Accepted — this is the load-bearing v2 capability.

## Alternatives considered

1. **Single high-effort detector with chain-of-thought in the detect prompt.** Rejected. llama3.2-vision is bad at long structured output; CoT inside detect produces unreliable JSON. Separating "detect" and "review" into different calls keeps each call within the model's competence.
2. **Continuous confidence score from the reviewer.** Rejected. Small models fabricate floats — there's no statistical relationship between "0.73" and "0.81" coming out of an 11B model. Discrete verdicts are honest.
3. **Whole-drawing review in one call.** Rejected. 30+ segments at once exceeds llama3.2-vision's reliable structured-output range; outputs become truncated or hallucinated. Per-segment review is small enough to be reliable.
4. **Unbounded loop until convergence.** Rejected. Oscillation is real on small models; without a cap, pathological cases burn the per-drawing budget on one segment.
5. **Reviewer with rejection power.** Rejected for v2. With no feedback capture (deferred to v3), an incorrect rejection silently destroys information. Confidence-only adjustment is reversible by users; rejection is not.
6. **Use a different model class entirely (e.g., a fine-tuned classifier) as the reviewer.** Rejected for v2 — no labeled reviewer-corpus exists. This becomes the v3 path (V2 §12.2) once feedback capture builds the corpus.
