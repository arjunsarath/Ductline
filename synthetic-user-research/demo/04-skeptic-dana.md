# Skeptic Pass — Dana Liu, PE Interview

**Method:** Separate subagent, did not see persona card or hypothesis — only the transcript and skeptic protocol.

---

## Claim 1: Pressure class lives in three places (schedule/tags/spec), schedule authoritative
**Holds, but generality is the question.** Dana's mental model is internally coherent and matches SMACNA convention, and her Cambridge lab example is concrete (specific drawing sheet M-401, specific values). **Substitutes test:** competing convention exists — some firms put pressure class only in spec + schedule and disallow plan tags, others tag every fitting. We have N=1; Dana's "schedule is source of truth" may be her firm's standard, not the industry's. **Counterfactual:** ask 3 more MEP firms whether plan tags are even present, and whether the schedule-wins rule is written down or tribal. If half don't tag plans at all, the three-place conflict surface shrinks materially. **Verdict:** load-bearing for the product premise, under-validated as universal.

## Claim 2: $3-6K/project response + $40-80K worst-case settlements
**Most fragile claim in the transcript.** The $600-1200/RFI all-in figure is a known industry estimate but bundles principal review time that wouldn't be billed differently if RFIs vanished — sunk salary, not recovered cash. **Frequency × severity:** 2-5 pressure-class RFIs/project at $600-1200 = $1.2K-6K, but only the top of that range is "pure response cost" she'd actually claw back. The $40-80K change-order anecdote is presented as recurring ("has seen") without frequency — once in a career? Once a year? E&O settlements are also paid by the carrier, not the firm directly; the firm feels it as deductible + premium creep, not a line item. **Counterfactual:** if you removed pressure-class conflicts entirely, would the firm staff one fewer engineer? Almost certainly no. **Verdict:** the dollar figures are directionally plausible but not budget-justifying on their own. This is where the pitch will get hardest scrutiny from a CIO.

## Claim 3: Stamp liability is a distinct motivator
**Strong on construct, weak on revealed preference.** Stamping is real (PE law, statute of repose 6-10 years in most states). But Dana already operates under that liability today and hasn't bought a tool to mitigate it — the manual QC step "gets compressed on deadline weeks" is the tell. **Buyer-vs-user test:** liability concern lives with the *firm* (E&O premiums, principals) more than the stamping engineer, who is indemnified by the firm in most employment arrangements. So this motivator may be real for principals (buyers) without showing up in Dana's daily behavior (user). That's actually consistent with her own framing — "reduces liability she can articulate to principals" — she's saying the *story* sells upward, not that she personally feels liability pain hour-to-hour. **Verdict:** holds as a *sales narrative*, weaker as a *user pull* signal.

## Claim 4: Revit→PDF boundary is where structured data dies
**Holds technically.** Revit parameters are queryable; PDFs are flat. **Format-fit test:** but Dana herself flagged that half her work is AutoCAD-legacy, where data was never structured to begin with. So "structured data dies at PDF" is half the story; for legacy projects the structure was never alive. The product has to handle both, and the AutoCAD path is harder (no parameter to recover). **Verdict:** real gap, but the framing oversells what's recoverable.

## Claim 5: Bluebeam doesn't cross-link schedule→model
**True and verifiable.** Bluebeam is markup/comparison, not a semantic linker. Low risk on this claim. **Substitutes test:** Newforma, BIM 360 Document Management, and Revizto do partial versions — worth checking before claiming greenfield.

## Claim 6: She'd adopt if FP-low and doesn't design
**Stated preference, not revealed.** Five conditions is a lot of conditions. The "70% catch with low noise > 100% with flood" line is sophisticated and credible — engineers genuinely hate noise. But the override-that-sticks-across-revisions requirement is a hard engineering problem (revision diffing on PDFs) that often sinks v1 products. **Counterfactual:** has she paid for any QC tool currently? If no, her stated willingness is cheap talk.

## Claim 7: CIO + practice leaders, 12-18 months, she champions
**Most reliable claim in the interview.** Matches every AEC-software GTM pattern. The price tiers ($5K PM expense / $5-50K practice-leader / $50K+ RFP) are specific enough to be testable and consistent with industry. **Verdict:** strong.

## Claim 8: Liability sells better than hours to principals
**Plausible but undertested.** Principals at AEC firms *do* respond to liability stories, but they also respond to utilization. Dana is telling us what *she* would pitch — she's not the principal. **Buyer-vs-user test:** need a principal interview before banking this.

---

## TOP 3 REVERSED ASSUMPTIONS

1. **The $3-6K/project + $40-80K settlement math is not a budget justification.** Response-cost dollars are mostly sunk salary, and settlement anecdotes lack frequency. Do not let this number anchor pricing or ROI claims without principal-level financial validation.

2. **Stamp liability is a sales narrative, not a user-pull signal.** Dana has lived with this liability for years without buying mitigation. Treat it as ammunition for the champion-to-principal conversation, not as evidence the user will pull the tool into daily workflow.

3. **The "three places" conflict surface is N=1 firm convention, not validated industry structure.** Some firms don't plan-tag at all; others tag everything. The product's core value prop depends on this being common — needs 3-5 more firm interviews before it's load-bearing.
