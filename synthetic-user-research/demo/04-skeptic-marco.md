# Skeptic Pass — Marco Reyes Interview

**Method:** Separate subagent. Did NOT see the persona card or the original hypothesis — only the transcript and the skeptic protocol. Critical that this is a different agent from the persona-embodied one so claims don't get graded by the entity that made them.

---

## Claim 1: Pressure class lives in specs/schedules/notes, not per-duct plan annotations

**Verdict: Survives.**
Counterfactual is concrete (Charlotte MOB job, M-001 schedule, legend note, Section 23 31 13), frequency is quantified ("one job in fifteen" for per-duct callouts), and the claim is consistent with how MEP coordination actually works in commercial work. Format-fit implication is uncomfortable for the product though: any extractor that only reads plan-view geometry is solving the wrong half of the problem. The spec PDF is the source of truth Marco actually opens.

## Claim 2: Conservative gauge padding costs 2-4% on metal and loses bids

**Verdict: Partial.**
Frequency × severity holds — bids lost by 1-2% in this market is industry-true, and 2-4% padding on an ambiguous section is plausible. But the leap to "one in ten bids I lost partly because of gauge" is unfalsifiable retrospection; bid losses have many causes (overhead, GC relationships, schedule risk premium) and Marco can't isolate gauge contribution post-hoc. Substitute test: a sharper junior estimator or an after-hours RFI also reduces this. Survives directionally, dies on the specific 1-in-10 attribution.

## Claim 3: The quiet cost (10-15% of takeoff hours on reconciliation) dominates rare catastrophic misreads

**Verdict: Survives — and this is the strongest claim in the transcript.**
Math checks: 8-12 hours × 30 bids × senior rate (~$120/hr loaded) = $30-45K/year in soft cost vs. $14K rework once every ~18 months. Chronic-mild × high-frequency beats acute-episodic in aggregate dollars. Counterfactual is what's actually happening today (senior estimator hours), and that line item shows up on a P&L. This is the buyable pain.

## Claim 4: TaksoAI's pressure-class gap is real and persistent

**Verdict: Survives.**
One year of usage, specific failure mode named (treats every duct as geometry, doesn't read spec context), and Marco distinguishes what it *does* solve (measuring) from what it doesn't (judgment). This is a credible incumbent-gap signal — not a "haven't tried it" complaint. The pitch-vs-reality gap ("they pitched it like it would, doesn't") is also the kind of specific grievance that survives scrutiny better than generic dissatisfaction.

## Claim 5: 90% accuracy + confidence flags won't change behavior; reasoning transparency will

**Verdict: Survives, with a caveat.**
The "120 wrong calls on 1,200 segments" math is correct and the prior-bad-experience anecdote (silently wrong fitting recognition) makes the skepticism earned, not theoretical. Caveat: stated preferences about future behavior are notoriously unreliable — Marco *says* he'd still verify, but if a transparent tool actually shipped and he saw 50 audited calls in a row come back clean, his verification rate would likely drop whether he predicted it or not. The directional claim (transparency > raw accuracy) survives; the absolute claim (no behavior change at 90%) is probably overstated.

## Claim 6: Spec-vs-drawing conflict detection > pressure-class prediction

**Verdict: Survives, and is the most product-relevant insight in the interview.**
This reframes the product from "predict the answer" (which competes with Marco's 18 years of pattern recognition and loses) to "find the disagreements" (which Marco genuinely can't do at scale because it requires reading 28 M-sheets against a 200-page spec book simultaneously). Format-fit is excellent — output is a flagged-conflict list that drops into his RFI workflow directly. This is differentiation from PlanSwift/Trimble/TaksoAI, all of which are extractors not reconcilers.

## Claim 7: Marco is buyer up to ~$200/seat/mo; above that, references > demos

**Verdict: Survives.**
Three clear bands stated (Tuesday-call, Frank-involved, two-reference-customers), all with concrete thresholds. The TaksoAI reference-call anecdote (Atlanta + Tampa contractors) corroborates. Buyer-vs-user test passes — Marco is genuine economic buyer in his band, gatekeeper above. One thing to verify in a follow-up: at the $50K+ tier, does Frank actually want references from *his* size firm and *his* market segment, or any references? That changes go-to-market routing.

## Claim 8: Per-bid / usage-based pricing is a non-starter without a cap

**Verdict: Survives.**
"30% volume swings quarter to quarter" is a falsifiable, industry-credible number, and Marco's reasoning is operational not philosophical — he can't budget against it. Cap-based usage pricing is acceptable, pure usage is not. Pricing implication is concrete: per-seat, or per-bid-with-annual-cap. Subscription with overage at a known ceiling.

---

## Top 3 reversed assumptions

**1. "Build a pressure-class prediction engine" was probably the original hypothesis. It dies.**
Marco doesn't want a tool to *predict* pressure class — he already does that in his head from system topology in <2 seconds. He wants a tool to *show its work* and flag disagreements between spec and drawing. The product is an auditor, not an oracle. Vendors who lead with "95% accurate AI prediction" are pitching the thing he distrusts most.

**2. "Plan-view extraction is the core technical problem." Reversed.**
The hard, valuable work is reading Section 23 31 13 spec PDFs and reconciling them against the M-series sheets and the duct schedule. A geometry-only tool (TaksoAI's current shape) plateaus at "saves measuring time, not judgment time." The spec-PDF NLP problem is the moat.

**3. "Catastrophic rework risk is the wedge." Reversed.**
Two events in three years is not a buying trigger — it's a war story. The real budget unlock is the 8-12 hours of senior estimator time per bid spent reconciling pressure class. ROI pitch should be hours-saved-per-bid against Frank's 12-month payback rule, not insurance against a $14K rework that happens twice a presidency.
