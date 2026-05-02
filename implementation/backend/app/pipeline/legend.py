"""Legend — output of the Legend Parser (SOLUTION-DESIGN-V2 §5.4, §6.1).

This module currently contains only the data shape — the parser stage
itself lands in PR-4. The shape ships ahead of the stage so PR-5 (Tiled
Detect) can be developed in parallel: `ctx.legend` is allowed to be
None throughout v2 (the parser is a P1 amplifier, not a precondition),
and downstream consumers must handle that None gracefully.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Legend(BaseModel):
    """Drawing-specific symbol / abbreviation / line-style conventions.

    Populated by ``LegendParserStage`` (PR-4) when a legend region is
    identified; left as None when no legend exists or the parser
    degrades. Detector and reviewer prompts include this dictionary as
    LEGEND CONTEXT to ground their output in the conventions of the
    specific drawing under analysis.
    """

    line_styles: dict[str, str] = Field(default_factory=dict)
    symbols: dict[str, str] = Field(default_factory=dict)
    abbreviations: dict[str, str] = Field(default_factory=dict)
    units: Literal["inches", "mm", "unknown"] = "unknown"
