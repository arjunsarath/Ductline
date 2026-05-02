# Skeptic Pass — Ben Okonkwo, CxA Interview

**Method:** Separate subagent, did not see persona card or hypothesis — only the transcript and skeptic protocol.

---

## Claim 1: Retrocx has a categorically different drawing-input problem
**Substitutes test:** Holds. Bluebeam, Procore, CxAlloy all confirmed as non-substitutes for parsing. No CxA workflow currently solves "scan from 1987 mylar." **Counterfactual:** What if owners increasingly have as-built Revit from 2010+ retrofits? Ben's "Dropbox of PDFs scanned in 2009" anchors on pre-2000 buildings — but a 2015-built campus going through retrocx in 2026 would not fit this story. The "categorically different" framing is true for ~pre-2005 stock but blurs as as-built BIM penetrates. **Verdict:** Directionally correct but the addressable subset of retrocx is narrower than the claim implies — call it the pre-2005 subset, not all retrocx.

## Claim 2: Pressure-class derivation = 60-100 hrs / 25% of fee
**Frequency × severity:** This is the strongest claim in the transcript. Specific dollar figures ($40-90K bids, $140/hr blended), specific hours, specific consequence (margin-killer). **Counterfactual:** Is it pressure class specifically, or the whole inventory? Ben says "inventory + pressure-class derivation" — these are bundled. If a tool nails geometry/dimensions but punts on pressure class, does it move the needle? Unclear from transcript. **Buyer-vs-user:** Ben is both buyer and user (sole proprietor), so no misalignment risk — but n=1. We should not treat his hour breakdown as an industry benchmark. Worth probing: is the sub doing 80% of those hours at lower rate, in which case the margin math is softer than $140/hr blended suggests? **Verdict:** Survives as a real pain but the 25% number is one CxA's mental accounting, not a validated benchmark. Pressure class may be inseparable from inventory in his head but separable in the product.

## Claim 3: Scanned PDFs are table stakes
**Format-fit test:** Strongly supported — three independent references (banker's box, 2009 scans of 1987 mylars, "shaky calibration on scanned PDF"). **Substitutes test:** Vector-only tools already exist (some Revit plugins, Togal.AI variants); none have penetrated his workflow because his inputs aren't vector. **Verdict:** Survives. This is the cleanest claim in the interview.

## Claim 4: Honest confidence flags are non-negotiable
**Counterfactual:** Ben already runs a 1-3 confidence column in Excel manually. He's projecting his existing practice onto the tool spec. That's signal — but it also means a tool without flags isn't unusable, it just forces him to add the column himself. **Buyer-vs-user:** For a sole CxA with personal liability, yes. For a 50-person firm where a junior does inventory and a PM signs reports, the confidence flag matters less because internal QC catches it. So "non-negotiable" is segment-specific. **Verdict:** Survives for Ben's segment; over-generalizing to enterprise is a mistake.

## Claim 5: New construction value prop is weaker
**Counterfactual:** Ben asserts new-con has "decent drawings, often Revit model, design team produced duct schedule." This is the design-intent view. CxAs running functional performance testing still need to verify installed duct against design — and contractor as-builts on new-con are notoriously sketchy. Ben underweights this because his new-con work is presumably with better GCs in PNW. **Verdict:** Weakens. New-con may still have a verification use case Ben isn't surfacing, possibly because his new-con clients are above-average. Worth probing 3-4 more new-con CxAs before discounting that segment.

## Claim 6: Per-project pricing fits intermittent users
**Substitutes test:** AutoCAD LT, Bluebeam itself sell per-seat and intermittent users still buy. Ben's preference may be stated rather than revealed. **Frequency:** He says retrocx is ~50% of work — that's not intermittent, that's half his book. The "intermittent" framing contradicts his own utilization. **Verdict:** Suspect. Ben prefers per-project because cash-flow-wise it matches engagement billing, not because usage is rare. A $150/mo seat would likely get used heavily.

## Claim 7: Long-tail CxA is real but small TAM
**Buyer-vs-user:** Ben volunteers this against his own interest ("$15K/yr is rounding error to 200-person shop"). Credible. **Counterfactual:** ACG + AABC + BCA membership rolls are public — the long-tail count is verifiable, not assertion. **Verdict:** Survives, but quantify before believing.

## Claim 8: Generic big-GC pitches kill adoption
**Format-fit test:** Plausible but unfalsifiable from a single interview. He cites no specific tool that lost him this way. Pattern-match to general SaaS folklore ("built for enterprise, retrofitted down"). **Verdict:** Holds as a pricing/positioning hypothesis, not as evidence.

---

## TOP 3 REVERSED ASSUMPTIONS

1. **"60-100 hrs / 25% of fee" is a benchmark.** It's one sole proprietor's mental math with bundled pressure-class + inventory hours and a sub doing unknown share at unknown rate. Treat as anecdote, not metric — re-validate with 5+ CxAs before quoting.

2. **New construction is "less interesting."** Ben's PNW new-con clients likely have above-average drawing quality. Installed-vs-design verification on functional performance testing is a use case he didn't surface. Don't write off new-con on one CxA's view.

3. **Per-project pricing fits because usage is intermittent.** His own utilization (50% retrocx) contradicts intermittent. The real preference is cash-flow matching to engagement billing, which is solvable with usage-based or annual-with-monthly-out, not strictly per-project.
