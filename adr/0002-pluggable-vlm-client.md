# ADR-0002 — Pluggable VLM client: Claude (prod) + Ollama (dev)

**Status:** Accepted, 2026-05-02
**Deciders:** Arjun Sarath

## Context

The pipeline depends on a vision-language model for one stage (duct detection, ADR-0001). Anchoring the build to a single hosted model introduces three risks:

1. **Reviewer reproducibility** — if Techjay reviewers don't have an Anthropic API key handy, the demo doesn't run.
2. **Iteration speed** — every prompt iteration during the build hits a paid API. Local iteration is faster and free.
3. **Vendor lock-in signal** — a take-home that hard-codes one model under-signals system thinking and ignores a real customer concern (research persona Aisha — "ACC + Navisworks IT review kills most pilots"; on-prem / local model support is part of the enterprise sale).

## Decision

Define a `VLMClient` Protocol with two implementations:

```python
class VLMClient(Protocol):
    def detect(self, image: PILImage, *, prompt_version: str = "v1") -> DetectionResult: ...
    def disambiguate_region(self, crop: PILImage, question: str) -> str: ...

class ClaudeVisionClient(VLMClient): ...     # uses anthropic SDK, claude-sonnet-4-6
class OllamaVisionClient(VLMClient): ...     # uses Ollama, llama3.2-vision or llava
```

Selection is via env var `VLM_PROVIDER=claude|ollama`. Default in `docker-compose.yml` is `ollama` so a reviewer can run the demo with no API keys; `ANTHROPIC_API_KEY` enables the Claude path for production-quality results.

Both clients return the same `DetectionResult` Pydantic model. The pipeline does not branch on provider.

## Consequences

**Positive**
- Reviewer can `docker compose up` and demo without keys.
- Prompt iteration during the build is free and fast on Ollama.
- README can show side-by-side outputs for the same drawing on Claude vs. Ollama — strong signal of provider abstraction working.
- Future providers (OpenAI, open-vocab detectors) slot in behind the same Protocol.

**Negative**
- Two implementations to keep in parity. Mitigated by a small `tests/test_vlm_parity.py` that runs both against a single fixture and asserts schema match (not output match).
- Local Ollama detection quality is materially weaker than Claude. Documented in the README accuracy table as expected.
- The default "free" path produces lower-quality output. Reviewers using Claude get the higher-quality demo. README explicitly addresses this.

## Alternatives considered

1. **Claude only.** Highest demo quality, requires API key, breaks reviewer experience.
2. **OpenAI GPT-4o.** Equivalent technical capability. Rejected because Claude tooling is mature and the prompt-engineering posture (structured tools, no JSON-from-prose) maps well to the Claude SDK.
3. **Ollama only.** Reviewer-friendly but weaker output. Rejected as the prod default — under-signals what the system can do.
4. **Hard-coded fallback chain (Claude → Ollama → CV-only).** Considered. Rejected as over-engineered for v1; explicit `VLM_PROVIDER` is simpler and more honest about which path the demo is using.
