# Findings — Marco Reyes (round 1, discovery)

**Method:** Persona embodiment + skeptic pass. Synthesized by main agent from transcript (`03-interview-marco.md`) and skeptic verdicts (`04-skeptic-marco.md`).

---

## Jobs-to-be-done (skeptic-filtered)

| Rank | JTBD | Frequency | Severity | Buyer power |
|---|---|---|---|---|
| 1 | When I'm reconciling pressure class across drawing + spec + schedule on a bid, I want a tool to show me which sources agree and where they disagree, so I can spend my 8–12 hours per bid on judgment rather than reading. | Per-bid (~30/yr) | High aggregate ($30–45K/yr in senior estimator time) | High |
| 2 | When the spec book and the plan view disagree on pressure class for a run, I want that conflict surfaced explicitly with both source citations, so I can write a precise RFI instead of finding it at submittal review. | Per-bid | High (drives the RFI volume) | High |
| 3 | When a tool tags a duct as a particular pressure class, I want to see the *reasoning* (which spec section, which schedule reference, which proximity inference), so I can audit it in 2 seconds. | Continuous during takeoff | Medium-high (trust mechanism) | High |
| 4 | When I'm pricing a competitive bid, I want enough confidence in pressure class that I'm not padding 2–4% on metal as a safety margin, so I'm not silently losing bids. | Per-bid | Medium-high (chronic) | High |

## Pains (ranked by transcript + skeptic confirmation)

1. **Reconciling pressure class across spec, schedule, legend note, and plan-view** — 10–15% of takeoff time per bid. *Confirmed strongest claim.*
2. **Pattern of conservative gauge padding** when pressure class is ambiguous. *Survives directionally; specific 1-in-10 lost-bid attribution is unfalsifiable.*
3. **Spec-vs-drawing conflicts** that surface only at submittal review — too late to bid correctly. *Surfaced as the highest-value product opportunity.*
4. **Tools that claim accuracy without showing reasoning** — burned by silent misses on a prior AI tool. Trust is now a binary gate.
5. **TaksoAI gap** — solves geometry, not judgment. Pitch-vs-reality grievance is specific.

## Workarounds — what Marco does today

- **Pattern recognition by system topology.** "AHU discharge to first major takeoff = medium pressure, downstream of VAV = low" — gets 80% of segments without reading the spec.
- **Default conservative when in doubt.** Bumps a gauge. Costs 2–4% on metal. Trades win-rate for rework-avoidance.
- **Direct call to the engineer** when relationship allows. Faster than RFI but doesn't paper the file.
- **Junior estimator + senior review.** Catches gauge errors, but expensive — and the chronic time cost is what shows up on the P&L.
- **PlanSwift + manual annotation.** Workhorse measuring tool with manual pressure-class tagging.
- **TaksoAI for geometry only.** Adopted but didn't replace the judgment layer.

## Reversed assumptions (PRD-impacting)

These are the most valuable output. Each invalidates a load-bearing belief that was implicit in the v0.1 PRD or in the original hypothesis.

1. **"Predict pressure class" → "Reconcile pressure class."** The product is an auditor, not an oracle. Marco does the prediction in his head from system topology. He needs a tool that reads the spec PDF + drawing + schedule, surfaces agreements and conflicts, and shows its work.
2. **"Plan-view extraction is the core technical problem" → "Spec-PDF NLP is the moat."** TaksoAI plateaus at geometry. The unsolved problem is reading 200-page mechanical specs against the M-series sheets. That's the differentiation surface.
3. **"Catastrophic rework risk is the wedge" → "Senior estimator hours are the wedge."** Two rework events in three years is a war story, not a buying trigger. The ROI pitch should target the 8–12 hours per bid × 30 bids/yr = ~$30–45K/yr in senior time.
4. **"Confidence flags reduce verification effort" → "Reasoning transparency reduces verification effort."** Marco won't stop verifying based on a confidence score he didn't calibrate himself. He will trust a tool that cites its sources well enough that he can audit the call in 2 seconds.

## Implications for PRD

The current v0.1 PRD (in `/Techjay/PRD.md`) needs updates in three places:

- **§3 Problem Statement** — reframe from "extract structured duct data from drawings" to "reconcile pressure class across drawing + spec + schedule, surface conflicts, show reasoning." The drawing extraction is necessary but not differentiating.
- **§5 Goals** — add "ingest the mechanical specifications PDF (Section 23 31 13 et al.) alongside the drawing" as a P0. Currently absent.
- **§5.2 Non-goals** — re-examine. Current non-goal "no spec ingestion" (implicit) is wrong direction.
- **§6 User Stories** — Eli's stories are about *speed of takeoff*. They should add a story about *spec-vs-drawing conflict detection* and *reasoning transparency*.
- **§9 Success Metrics** — accuracy targets are about prediction. Add metrics about conflict-detection recall and reasoning-trace completeness.
- **§11.2 Open questions** — "wedge: HVAC depth vs. drawing-extraction breadth" should narrow further: the wedge is "spec + drawing reconciliation," and the depth is HVAC because pressure class is the most spec-coded attribute.

## What this round didn't cover (next rounds)

- Validation across other personas (BIM coordinator, Cx engineer, MEP designer). Marco speaks for the fabricator/estimator wedge only.
- Larger firms (200+ employees). Marco's discretionary buying authority breaks at this size.
- Geographies outside SE US — different state codes, different SMACNA chapter conventions.
- Pricing model validation — Marco indicated discomfort with usage-based without a cap; need to test specific price points.
- Solution validation — when a prototype exists, re-spawn Marco and ask: "Does this address [JTBD 1, 2, 3]? What's missing?"

## Followup interview prompts (for round 2 with Marco)

Pre-baked prompts ready for the next round, when there's a solution to validate. These reuse the same persona card.

1. *Show Marco a screen mockup of conflict detection.* "Walk me through how you'd use this tomorrow on the next bid that lands."
2. *Show Marco a reasoning panel.* "Is this enough audit trail for you to trust the tag, or do you need more?"
3. *Show Marco a price tag of $X/seat/mo.* "Where does this fit in your discretionary band? What would Frank ask?"
4. *Pose adoption friction:* "If this took 4 hours of setup per bid set vs. 0 for PlanSwift, where's your cutoff?"
