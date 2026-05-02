# Interview — Marco Reyes

**Method:** Embodied subagent. Persona card from `02-persona-marco.md`. Questions from `01-research-plan.md`. Subagent stayed in character throughout, did not break to add meta-commentary.

---

**Q: Walk me through your last takeoff. Where does pressure class come from on the drawing — per-duct, schedule, title block, or implied?**

**Marco:** Last one was a three-story medical office building outside Charlotte, about 140 sheets, M-series was maybe 28 of those. Where does pressure class come from? Depends on the engineer, and that's the honest answer. On that job it was in two places — a note on the mechanical legend sheet that said "all supply ductwork upstream of VAV boxes shall be 2" w.g. construction, downstream 1" w.g., return and exhaust ½" w.g. unless noted otherwise" — and then a duct schedule on M-001 that called out the AHU discharge as 3" w.g. for the first twenty feet. So you've got a general note doing the heavy lifting and a schedule overriding it in specific spots.

That's the clean version. Plenty of jobs I get, the pressure class is *implied* — engineer puts CFM and dimension on the duct, maybe a static pressure on the fan schedule, and expects me to figure out the construction class from the design intent. Title block almost never has it. Per-duct callouts on the plan view? Maybe one job in fifteen, and usually only on the high-pressure runs near the AHU. Most of the time I'm reading the mechanical specifications — Section 23 31 13, sheet metal ductwork — and that's where the real construction requirements live. Leakage class, seal class, pressure class by system type. The drawing tells me the geometry, the spec tells me the gauge. If those two disagree, that's an RFI.

**Q: When the drawing is ambiguous on pressure class, what do you actually do? Default conservative, RFI, ask a colleague, something else?**

**Marco:** All three, in that order, depending on time. First thing — and people who haven't done this don't get it — I look at *system type and location in the system*. AHU discharge to the first major takeoff, that's medium pressure, period, I don't care what the note says. Downstream of the VAV, it's low. Return air, ½ inch. Kitchen exhaust, that's its own animal, grease duct is welded black iron, different code section entirely. Pattern recognition gets me 80% of the way.

For the ambiguous 20%, if I've got time and the GC relationship is good, I write an RFI. "Please confirm pressure class for ductwork between points X and Y." Clean, documented, covers me. If I'm two days from bid and the GC isn't going to chase the engineer in time — and that's most bids, let's be real — I default conservative. Bump up a gauge. On a 36x24 at the borderline, SMACNA Table 2-something, I think 2-30 or 2-31, you're at 20 ga for 1" and 18 ga for 2". I'll bid the 18. Costs me maybe 15% on that section's metal weight, but I'm not eating a rework if it turns out to be 2".

Asking a colleague — yeah, my junior estimator and I argue about this stuff constantly, and sometimes I'll call the engineer directly if I know them. Southeastern market, you know everybody after 18 years. That's faster than a formal RFI but it doesn't paper the file, so I follow up in writing.

**Q: What's the cost of that ambiguity — to your bid number, to your win-rate, to fab rework?**

**Marco:** Three different costs, and people conflate them. Bid number — if I default conservative on a job that's mostly low-pressure but has ambiguous medium-pressure zones, I'm probably 2 to 4% high on the metal. On a $400K ductwork bid, that's eight to sixteen grand. Doesn't sound like much until you realize ductwork bids in this market get won and lost by 1 to 2%. So yeah, conservative defaulting *does* cost me jobs. I don't have a clean number on win-rate impact but I'd guess one in ten bids where I felt squeezed, the gauge call is part of why I lost.

Fab rework — that's the worst one, because that's real money out the door, not theoretical. If we cut and seam 200 feet of 20 ga duct and then the submittal review comes back and the engineer says "no, that run is 2 inch, needs 18," we're scrapping coil or downgrading it to a return-air job somewhere else. I've had that happen maybe twice in the last three years on a serious scale. One job it was a $14K hit on metal plus labor to recut. The other one we caught it at shop drawing review before fab kicked off, so it was just an estimating embarrassment, not a cash hit.

The *quiet* cost is the time I spend on this stuff. I'd guess 10 to 15% of my takeoff hours on a typical bid is reconciling pressure class against spec against schedule against my own judgment. On an 80-hour takeoff, that's eight to twelve hours of senior estimator time per bid. Multiply across the 30-some bids a year I personally touch, that's real overhead.

**Q: What software have you tried for takeoff (PlanSwift, Trimble, TaksoAI, Bluebeam)? What did each not solve?**

**Marco:** PlanSwift is my workhorse, ten years on it. What it doesn't solve — it's a measuring tool. It counts linear feet and square feet. It does not understand what a duct *is*. I tell it "this polyline is 24x12 supply, 1 inch w.g.," it stores that, it gives me a quantity. Pressure class is something I tag manually. PlanSwift doesn't read the drawing, it lets me annotate the drawing.

Trimble Estimation MEP, formerly Quote Software — I use it when the GC requires it or when a customer wants the assembly-based output. It's more structured than PlanSwift, has duct assemblies built in with gauge logic. But the setup time is brutal and the assembly libraries need constant maintenance. I don't use it as my primary because the speed-of-takeoff is slower, and on a competitive bid speed matters. It also doesn't auto-read pressure class — you still tell it.

TaksoAI we've had for about a year. It's good at geometry. It'll pull duct runs off a PDF reasonably well, sizes too, sometimes. Where it falls down — and I told their CSM this on the last call — is exactly the question you're circling. It does not reliably pick up pressure class from specs or from system context. It treats every duct as a piece of geometry and leaves the construction class to me. So I use it for the quantity skeleton, then I overlay my own pressure class assignments. Saves me time on measuring, doesn't save me time on judgment. They pitched it like it would, and it doesn't, and that's the gap I keep flagging.

Bluebeam isn't really a takeoff tool for me, it's markup and review. I use it to red-line the drawings, communicate with my juniors, and review submittals. Solid product, does what it says.

**Q: If a tool was 90% accurate on pressure class with confidence flags on the rest, would you stop verifying? Or would you re-check regardless?**

**Marco:** No. I would not stop verifying. Let me tell you why, because the question kind of assumes I'm being inefficient by checking, and I'm not.

Ninety percent accurate sounds great in a marketing deck. On a bid with, call it, 1,200 duct segments, 90% accuracy means 120 wrong calls. Even if the tool *flags* the uncertain ones, I now have to verify the flagged ones AND spot-check the unflagged ones, because I don't trust that the confidence score is well-calibrated. I've been burned. The last AI tool we trialed — not naming it — claimed 95% on fitting recognition and the misses were silently wrong, no flag. That's worse than no tool, because it gives the junior estimator false confidence.

What would change my behavior is not 90% accuracy. It's *transparency about why* the tool made a call. If it tells me "this run is tagged 2 inch w.g. because the spec section 23 31 13 paragraph 2.3 says supply upstream of VAV is medium pressure, and this run is upstream of a VAV per the schematic" — now I can audit the logic in two seconds instead of re-deriving it. That's a 5x speedup on my reconciliation time, and I'd pay for it.

The other thing — I'd want it to flag *spec-versus-drawing conflicts* automatically. That's where the real money hides. If the legend says 1 inch and the schedule says 2 inch on the same run, a human catches that maybe 70% of the time under bid pressure. A tool that surfaces it 100% of the time is genuinely valuable. That's different from "predict pressure class," which is the framing vendors love and I'm skeptical of.

**Q: Who buys software at your firm? At what price-point does it become a board-level decision vs. your discretionary call?**

**Marco:** Me and the owner, Frank. That's it, there's no board — we're privately held, 50 employees, he and I make these calls over coffee. My discretionary spend is around $200 a month per user without asking anybody. So a $40-a-seat tool for me and two juniors, that's a Tuesday. I'll trial it, see if it sticks.

Above that — say $2,000 a month total, or any annual contract over $20K — Frank's in the conversation. Not because he understands the software, he doesn't, he came up estimating like me but he's been out of the daily takeoff for fifteen years. He's in the conversation because that's real money and he wants to know the ROI story. I have to walk him through it: what hours does this save, what bid wins does it enable, what's the payback period. If I can show payback under 12 months on hours saved alone, he signs. If the case is "this might help us win more bids," he's more skeptical, because that's a soft number.

Anything north of $50K a year — that's a real decision, takes a couple weeks, and Frank wants to talk to two reference customers before we sign. TaksoAI was in that range and we did exactly that. Talked to a contractor in Atlanta and one in Tampa before pulling the trigger. Honestly the references were the deciding factor, not the demo. Demos are theater. References are people willing to tell you what actually broke.

One more thing — pricing models matter as much as price. Per-seat I can budget. Per-bid or per-square-foot I can't, because my volume swings 30% quarter to quarter. Anybody pitching me a usage-based model better have a cap, or I'm out before the conversation starts.
