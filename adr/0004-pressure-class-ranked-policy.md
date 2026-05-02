# ADR-0004 — Pressure-class classification via 4-tier ranked-confidence policy

**Status:** Accepted, 2026-05-02
**Deciders:** Arjun Sarath
**References:** PRD §8 (architecture sketch), research findings cross-persona §1 ("reconcile, not predict")

## Context

The take-home brief requires assigning each duct a pressure class — Low, Medium, or High. The PRD already names a hybrid policy (explicit annotation > schedule lookup > heuristic fallback). The synthetic research sharpens this: the wedge isn't *predicting* pressure class but *reconciling* it across drawing + spec + schedule with reasoning shown.

A naive "ML model classifies pressure class" approach has three problems:

1. No training data.
2. Pressure class on a real drawing is not a vision problem — it's an information-extraction problem (read the explicit annotation; otherwise look up the schedule; otherwise infer).
3. An ML classifier with a single confidence score hides *why*. The research insight is that reasoning, not confidence numbers, is what builds trust.

## Decision

Pressure class is determined by a deterministic 4-tier ranked policy, applied to each segment in order. The first tier that returns a value wins. The `source` field on `PressureClass` records which tier fired.

### Tier 1 — Explicit annotation on or adjacent to the duct
- Trigger: PaddleOCR finds a text string within 30 px of the segment matching the regex `(?i)(LOW|MED(?:IUM)?|HIGH)\s*(?:PRESS(?:URE)?)?`
- Output: `value` from the regex group, `confidence: high`, `source: "ocr:near_segment(d=Npx)"`

### Tier 2 — Schedule lookup by system tag
- Trigger: segment has a system tag (e.g., `SA-1`, `RA-2`) extractable from nearby OCR text **and** the schedule region contains a row keyed by that tag with a pressure-class column
- Output: `value` from the schedule cell, `confidence: medium`, `source: "schedule:<sheet-id>/<row-id>"`

### Tier 3 — SMACNA velocity heuristic
- Trigger: tier 1 and tier 2 both failed, but the segment has a known dimension
- Logic: assume mid-range velocity (~2000 fpm for low, ~3500 fpm for medium, ~4500 fpm for high) per SMACNA defaults; pick the velocity tier whose CFM range (size × velocity) is most plausible for the duct's role
- Output: `value: heuristic result`, `confidence: low`, `source: "smacna:velocity_heuristic(N fpm)"`
- Always carry alternatives — the other two PC values are listed in `alternatives[]` so the user sees the inference is a guess

### Tier 4 — Low-confidence fallback (never "unknown")
- Trigger: dimension extraction failed and no tier 3 input is available
- Output: `value: "LOW"` (most-common-default), `confidence: low`, `source: "fallback:default_low"`, `alternatives: ["MEDIUM", "HIGH"]`
- Reasoning trace explicitly says "no signal — default applied"

## Implementation

```python
class PressureClassClassifier:
    def classify(self, segment: Segment, schedule: Schedule | None,
                 nearby_text: list[OCRMatch]) -> PressureClass:
        if pc := self._tier1_explicit(nearby_text):
            return pc
        if pc := self._tier2_schedule(segment.system_tag, schedule):
            return pc
        if pc := self._tier3_smacna(segment.dimension):
            return pc
        return self._tier4_fallback()
```

No inference, no model. Each tier is a pure function of its inputs.

## Consequences

**Positive**
- Output is always inspectable — `source` tells the user *why*.
- Reasoning trace is a natural artifact of the policy.
- The schedule-lookup tier (tier 2) is the path the synthetic research said was the *expected* case — building it correctly is the system's most defensible feature.
- No model retraining, no labeled data dependency.

**Negative**
- Heuristic tier 3 will be wrong sometimes. Always flagged `low` confidence and accompanied by alternatives — the user knows it's a guess.
- Schedule-region OCR is the load-bearing dependency for tier 2. If the schedule isn't found or parsed, tier 2 silently fails and tier 3 fires. Mitigated by surfacing the region-detect outcome in the reasoning trace.
- "Always classify" (tier 4 instead of "unknown") is a deliberate trade — we'd rather show a guess with clear reasoning than refuse to answer. Documented in the README.

## Alternatives considered

1. **ML classifier from drawing → PC.** No labeled data; even with one, hides the reasoning. Rejected.
2. **VLM classifies PC end-to-end.** Tempting, but prone to confident hallucination on the heuristic case. Rejected — pressure class is information-extraction, not vision.
3. **"Unknown" as a fourth state when all tiers fail.** Considered. Rejected because the PRD explicitly requires a class per segment; "unknown" leaks an open contract to downstream consumers. The fallback default + alternatives + reasoning trace is the more honest version.
4. **Confidence as a numeric score (0-1).** Rejected. Three named tiers (`high/medium/low`) map directly to the four policy tiers and are easier to surface in the UI as colors/badges than a continuous score.
