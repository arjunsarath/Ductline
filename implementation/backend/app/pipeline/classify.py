"""Stage 6 — Pressure-class classifier (ADR-0004, SOLUTION-DESIGN §4 row 6).

Deterministic ranked-policy state machine. The VLM never decides pressure
class — every value comes from one of four explicit tiers:

  1. Explicit annotation near the duct          → confidence: high
  2. System tag → schedule row lookup           → confidence: medium
  3. Dimension → SMACNA velocity heuristic      → confidence: low
  4. Default LOW + alternatives surfaced        → confidence: low

`source` names which tier fired so the UI can cite it in the popover trace.
"""

from __future__ import annotations

import re

from app.pipeline.base import PipelineContext, PipelineStage, VLMSegmentDraft
from app.schemas import (
    Confidence,
    Dimension,
    PressureClass,
    PressureClassValue,
    ReasoningStep,
)

# Annotation vocabulary — engineering drawings abbreviate aggressively.
_LOW_KEYWORDS = (
    "LOW PRESS",
    "LOW PRESSURE",
    "LOW",
    "L.P.",
    "L.P",
    "LP",
)
_MEDIUM_KEYWORDS = (
    "MED. PRESS",
    "MEDIUM PRESS",
    "MEDIUM PRESSURE",
    "MED.",
    "MED",
    "MEDIUM",
    "M.P.",
    "M.P",
    "MP",
)
_HIGH_KEYWORDS = (
    "HIGH PRESS",
    "HIGH PRESSURE",
    "HIGH",
    "H.P.",
    "H.P",
    "HP",
)

# System tag pattern: SA-1, RA-2, EA-3, EX-12, OA-4 etc.
_SYSTEM_TAG_PATTERN = re.compile(r"\b([A-Z]{1,3})-?(\d{1,3})\b")


class PressureClassClassifier(PipelineStage):
    name = "pressure_class"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        for draft in ctx.segments_draft:
            dimension = ctx.dimensions.get(draft.segment_id)
            ctx.pressure_classes[draft.segment_id] = self._classify(
                draft, dimension, ctx.schedule_rows
            )
        return ctx

    def _classify(
        self,
        draft: VLMSegmentDraft,
        dimension: Dimension | None,
        schedule_rows: list[list[str]],
    ) -> PressureClass:
        # Tier 1 — explicit annotation in the segment's nearby text.
        annotation_match = _match_pressure_keyword(draft.nearby_text)
        if annotation_match is not None:
            value, evidence = annotation_match
            draft.reasoning_trace.append(
                ReasoningStep(
                    stage="schedule_lookup",
                    evidence=f'tier 1: "{evidence}" found near segment',
                )
            )
            return _result(value, "high", f'annotation:"{evidence}"')

        # Tier 2 — system tag → schedule row lookup.
        system_tag = _extract_system_tag(draft.nearby_text)
        if system_tag is not None:
            schedule_match = _match_schedule_row(system_tag, schedule_rows)
            if schedule_match is not None:
                value, row_index = schedule_match
                draft.reasoning_trace.append(
                    ReasoningStep(
                        stage="schedule_lookup",
                        evidence=(
                            f"tier 2: system tag {system_tag} matched "
                            f"schedule row {row_index}"
                        ),
                    )
                )
                return _result(
                    value, "medium", f"schedule:row-{row_index}({system_tag})"
                )

        # Tier 3 — dimension → SMACNA velocity heuristic.
        if dimension is not None:
            value = _smacna_velocity_heuristic(dimension)
            draft.reasoning_trace.append(
                ReasoningStep(
                    stage="schedule_lookup",
                    evidence=(
                        f"tier 3: SMACNA velocity heuristic on "
                        f"{dimension.value}"
                    ),
                )
            )
            return _result(
                value, "low", f"smacna:velocity_heuristic({dimension.value})"
            )

        # Tier 4 — default LOW with alternatives.
        draft.reasoning_trace.append(
            ReasoningStep(
                stage="schedule_lookup",
                evidence="tier 4: no annotation, no schedule match, no dimension",
            )
        )
        return PressureClass(
            value="LOW",
            confidence="low",
            source="default",
            alternatives=["MEDIUM", "HIGH"],
        )


# ── Tier helpers (pure). ─────────────────────────────────────────────────────


def _result(value: PressureClassValue, confidence: Confidence, source: str) -> PressureClass:
    return PressureClass(value=value, confidence=confidence, source=source)


def _match_pressure_keyword(
    nearby_text: list[str],
) -> tuple[PressureClassValue, str] | None:
    """Order matters — match the most specific (longest) keyword first so
    'LOW PRESS' wins over a stray 'LOW' substring inside it.
    """
    haystack = " ".join(nearby_text).upper()
    for keyword in _HIGH_KEYWORDS:
        if keyword in haystack:
            return "HIGH", keyword
    for keyword in _MEDIUM_KEYWORDS:
        if keyword in haystack:
            return "MEDIUM", keyword
    for keyword in _LOW_KEYWORDS:
        if keyword in haystack:
            return "LOW", keyword
    return None


def _extract_system_tag(nearby_text: list[str]) -> str | None:
    for text in nearby_text:
        match = _SYSTEM_TAG_PATTERN.search(text.upper())
        if match:
            return f"{match.group(1)}-{match.group(2)}"
    return None


def _match_schedule_row(
    system_tag: str, schedule_rows: list[list[str]]
) -> tuple[PressureClassValue, int] | None:
    normalized_tag = system_tag.upper().replace("-", "")
    for index, row in enumerate(schedule_rows):
        cell_blob = " ".join(row).upper().replace("-", "")
        if normalized_tag not in cell_blob:
            continue
        # Reuse keyword detection over the full row.
        keyword_match = _match_pressure_keyword(row)
        if keyword_match is not None:
            return keyword_match[0], index
    return None


def _smacna_velocity_heuristic(dimension: Dimension) -> PressureClassValue:
    """Rough mapping from cross-section to likely pressure class.

    Without drawing scale we can't compute velocity directly. The heuristic
    here uses the duct's largest dimension as a proxy: small ducts tend to
    serve low-pressure terminal branches, large ducts are more likely on
    medium- or high-pressure trunks. Always emitted with low confidence.
    """
    largest = _largest_dimension_inches(dimension)
    if largest is None:
        return "LOW"
    if largest <= 18:
        return "LOW"
    if largest <= 36:
        return "MEDIUM"
    return "HIGH"


def _largest_dimension_inches(dimension: Dimension) -> int | None:
    numbers = [int(n) for n in re.findall(r"\d+", dimension.value)]
    return max(numbers) if numbers else None
