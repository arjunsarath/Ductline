# Skeptic Pass — Aisha Patel Interview

**Method:** Separate subagent, did not see persona card or hypothesis — only the transcript and skeptic protocol.

---

## 1. Pressure class in 4 places, drifts independently

**Holds up, with caveats.** The mechanism is concrete and named (M0.02 schedule, plan-view tag on transitions, Revit family parameter, SMACNA spec reference) and the AHU-3 example is specific enough to be falsifiable. **Substitutes test:** the workaround today is human catch-on-mismatch (she noticed sizes jumped) — not a tool, which suggests real unmet need. **Counterfactual:** but is pressure class itself the load-bearing attribute, or is duct *size* the one she actually caught? In her own story, she found the drift via geometry mismatch on plan, not via pressure class fields. The "4 places" architecture may be true but the *triggering* drift in her example was sizing. Risk: we build pressure-class diff and discover sizing/CFM/material drift is the bigger surface. **Need to validate:** ask 3 more VDC managers to rank which attributes drift most.

## 2. 3 days × 3 people × every 3-4 weeks = buyable pain

**Partially survives — frequency-severity is real, but attribution is fuzzy.** 9 person-days per revision, 13× per year ≈ 117 person-days/yr ≈ ~$60-90K loaded labor on this one project. That clears a $50K/yr threshold easily. **But:** the "3 days" includes everything in revision reconciliation — geometry diff, attribute diff, Navisworks federation, ACC issue logging, RFI drafting. Attribute diff is a *slice*. If the tool collapses only the attribute-diff slice, savings might be 1-1.5 person-days, not 3. **Frequency × severity:** revision cadence (every 3-4 weeks) is solid for hospital MEP-heavy work but may not generalize to lighter projects. **Counterfactual:** would she actually reduce headcount or just redirect those 2 BIM coords? If the savings are absorbed (not banked), CFO won't see ROI on renewal. The 6-day area hold and 5-7 day RFI turnarounds are the real dollars — but those are downstream of catching drift earlier, not of the diff tool itself.

## 3. Visual-diff tools don't solve attribute-diff

**Strongest claim in the transcript. Survives.** She named four specific tools (Navisworks, ACC sheet compare, Bluebeam Sets, Revit compare-models) and articulated *why* each fails: pixel/geometry-only, no element-level attribute reasoning. This is a category gap, not a feature gap. **Substitutes test:** her current substitute is manual cross-referencing — expensive enough to justify her opening RFIs about it. The fact that Autodesk hasn't shipped this inside ACC despite owning the data is suspicious — either (a) hard problem, (b) low priority for them, (c) coming soon and will eat our lunch. Need to check ACC roadmap.

## 4. Must integrate with ACC + Navisworks; standalone is dead

**Holds — this is the format-fit constraint.** "Will not add a 6th platform" is a hard line and matches every enterprise-SaaS-fatigue pattern. **But** beware: "plug into ACC" is easier said than done. ACC's Forge/APS APIs gate certain workflows; writing issues back is supported, but federated-model attribute injection into Navisworks is custom-integration territory. **Risk:** the integration build is the product. If we underestimate, we ship a standalone tool with a thin ACC connector and she rejects it.

## 5. Value frame = team productivity, not individual

**Survives, but it's a sales-narrative claim, not a usage claim.** She's telling us how to *sell* it to her director, not how she'll *use* it. **Buyer-vs-user test:** Aisha is the user-champion; the buyer is VDC director + VP Ops. Team-productivity framing is correct for them. But individual time-savings is what makes Aisha a daily-active user. We need both narratives — pitch them differently to different rooms.

## 6. $500/mo discretionary; $5K/mo or $50K/yr triggers full review

**Survives directionally; specifics are one data point.** This is one VDC manager at one GC. Discretionary thresholds vary 2-3x across top-50 ENR firms. **Buyer-vs-user:** she has *user*-level authority (pilot seats on project budget), not *buyer* authority for enterprise rollout. The $50K cliff is where the actual deal lives, and that's a committee.

## 7. IT review kills most pilots

**Plausible but unverified at her firm.** "IT kills most pilots" is folk wisdom in construction tech — true in aggregate, but she didn't cite a specific killed pilot. **Counterfactual:** what tools *did* survive IT in the last 12 months at her firm? Those are the templates. SOC 2 + ACC tenant auth + data residency is table-stakes; if we don't have SOC 2 by pilot-end, we're dead. This is a build-cost claim more than a product claim.

## 8. Annual Q4 cycle, ~12-month sales cycle

**Survives as a planning constraint.** "August 2026 pilot to be in 2027 budget" is a clean, falsifiable timeline. **Risk:** assumes her firm runs on calendar-year budgets — many GCs use fiscal years that don't align. Confirm.

---

## TOP 3 REVERSED ASSUMPTIONS

1. **"Pressure class is the load-bearing attribute"** — her own example caught drift via *sizing*, not pressure class. The MVP attribute-diff scope may be wrong; size/CFM/material likely matter more, and we should validate the attribute-priority ranking before scoping.

2. **"3 days/revision × 3 people = our addressable savings"** — the attribute-diff slice is probably 1-1.5 person-days, not 3. The big dollars are downstream (6-day area holds, 5-7 day RFI cycles), which we only capture if we measurably *reduce RFI volume* — a much harder claim to prove in a 90-day pilot.

3. **"ACC integration is a checkbox"** — she treats it as table-stakes, but writing element-level attribute findings back into ACC issues *and* surfacing inside Navisworks is the actual product. Underestimating this turns us into a standalone tool with a thin connector — exactly the thing she said is dead on arrival.
