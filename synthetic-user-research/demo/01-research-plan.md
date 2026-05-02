# Research Plan — HVAC Duct Detection, Discovery Round 1

**Phase:** discovery
**Date:** 2026-05-02
**Owner:** Arjun

## Hypothesis

Pressure-class extraction is the highest-value gap in HVAC duct takeoff because TaksoAI and PlanSwift handle quantity but require manual pressure-class reconciliation. The chronic conservative gauge padding when pressure class is ambiguous costs sub-contractors more in lost win-rate than the rare catastrophic misread costs in fab rework.

## Research questions

1. Walk me through your last takeoff. Where does pressure class come from on the drawing — per-duct, schedule, title block, or implied?
2. When the drawing is ambiguous on pressure class, what do you actually do? Default conservative, RFI, ask a colleague, something else?
3. What's the cost of that ambiguity — to your bid number, to your win-rate, to fab rework?
4. What software have you tried for takeoff (PlanSwift, Trimble, TaksoAI, Bluebeam)? What did each not solve?
5. If a tool was 90% accurate on pressure class with confidence flags on the rest, would you stop verifying? Or would you re-check regardless?
6. Who buys software at your firm? At what price-point does it become a board-level decision vs. your discretionary call?

## Success criteria

- **Commit:** Marco confirms (a) pressure class is the primary friction in the takeoff, not duct geometry; (b) chronic padding costs more than catastrophic misreads; (c) discretionary buying authority sits with the estimating principal at firm sizes 20–200 employees.
- **Update:** Mixed — pain is real but value prop reframes from "pressure class extraction" to e.g., "drawing-to-takeoff structured handoff" or "shop drawing reconciliation."
- **Abandon:** Marco indicates TaksoAI's gap is closing fast / pressure class is a non-issue in his bid math / catastrophic misreads dominate the cost calculus and a 90% tool would not move the needle.

## Personas in scope (this round)

- Marco Reyes — mechanical sub-contractor / fabricator
- (subsequent rounds: MEP designer, BIM coordinator, Cx engineer)

## Out-of-scope

- Pricing model (deferred to a separate willingness-to-pay round once value prop confirmed)
- Detailed UX (deferred)
- Buying journey of larger GCs (separate persona)
