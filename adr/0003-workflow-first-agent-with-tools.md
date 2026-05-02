# ADR-0003 — Workflow-first; agents only with typed tools

**Status:** Accepted, 2026-05-02
**Deciders:** Arjun Sarath

## Context

A naive build of this system would let an LLM/VLM run the whole pipeline: "here's an image, return JSON of ducts with dimensions and pressure class." That's tempting in a 2-day window. It is also the wrong shape for an interview submission — it under-signals system thinking, makes the output unpredictable, and obscures where accuracy actually comes from.

The user's stated engineering posture: *if something can be algorithmic, it will be; if something can be a workflow, it should be; agents only when needed, and with tools to make them predictable.*

This ADR captures that posture as an enforceable rule across the pipeline.

## Decision

Three rules, applied in order, when designing every pipeline stage:

1. **Algorithmic if possible.** A stage with a deterministic implementation (formula, lookup, regex, threshold) is implemented that way. No exceptions.
2. **Workflow if not.** A stage that requires deciding between deterministic sub-stages is a state machine over them. The pressure-class classifier (ADR-0004) is the canonical example.
3. **Agent (with typed tools) only when neither works.** When a VLM is invoked, it is invoked through a typed tool whose schema is defined as a Pydantic model. The model returns structured data, not prose. We do not parse JSON out of model text.

Stage-by-stage classification:

| Stage | Type | Rationale |
|---|---|---|
| Ingest | ALG | `pdf2image`, fixed DPI |
| Quality check | ALG | Numeric scores against thresholds |
| Region detect | ALG + VLM fallback | Title blocks usually rectangular bottom-right; VLM only if classical fails |
| Duct detection | AGT (one tool call) + ALG refinement | No labeled data; CV alone misses dashed/crowded |
| Text extraction | ALG | OCR + regex grammar |
| Schedule extraction | ALG + VLM fallback | Tables structured but format varies |
| Pressure-class | WF (state machine) | Pure ranked policy (ADR-0004) |
| Assemble | ALG | Deterministic merge |

## Tool interface for the one in-path agent

```python
class DetectDuctsTool(BaseModel):
    """The VLM is required to call this exactly once. No prose output."""
    segments: list[VLMSegment]

class VLMSegment(BaseModel):
    bbox: tuple[float, float, float, float]
    shape_hint: Literal["round", "rectangular", "unknown"]
    nearby_text: list[str]
```

The VLM is constrained to call `DetectDuctsTool`. Anything outside that contract is a pipeline error and the stage degrades to CV-only mode.

## Consequences

**Positive**
- Predictable failure modes. When a stage fails, we know which one and why.
- Reviewer-readable code — each stage is small, single-purpose, and named.
- Reasoning trace falls out of this for free: each stage records what it produced.
- Cost and latency are predictable — exactly one VLM hop in the default path.

**Negative**
- More code than a one-shot VLM call would require. Accepted.
- Stage seams need to be designed up front. Mitigated by the named seams in SOLUTION-DESIGN.md §5.
- VLM fallbacks at stages 3 and 6 add complexity. Mitigated by gating them on classical-pass failure and instrumenting how often they fire.

## Alternatives considered

1. **Single agent runs the whole pipeline.** Easiest to scaffold, weakest predictability, weakest interview signal. Rejected.
2. **Workflow with no VLM at all.** Most predictable, but no labeled training data means duct detection itself becomes unsolved. Rejected — VLM at stage 4 carries the build.
3. **Multiple agents, each with their own tools.** More principled in the long run. Rejected for v1 — over-engineered for a 2-day window when one agent stage is enough.
