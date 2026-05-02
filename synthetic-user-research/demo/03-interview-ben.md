# Interview — Ben Okonkwo, CxA (commissioning engineer)

**Method:** Embodied subagent. Persona card from `personas/ben-cx-engineer.md`. Stayed in character.

---

**Q:** Walk me through onboarding a retrocommissioning project. How do you actually build a duct inventory from the drawings the building owner gives you?

**Ben:** First thing — and I tell every owner this on the kickoff call — I need whatever drawings they have, in whatever shape they're in. For new construction Cx, you get clean Revit exports, half the time native files. Retrocommissioning is a different animal entirely. I'll get a banker's box of half-size prints, or more commonly now, a Dropbox folder with PDFs that somebody scanned in 2009 off mylars from 1987. Skewed, missing sheets, M-101 there but M-103 gone, schedules cut off at the page edge.

So I open the package in Bluebeam Revu and just start cataloging. Sheet index first — what do I actually have. Then I go floor by floor through the mechanical plans and start a duct inventory in Excel. I built my own template years ago: tag, system (AHU-1, AHU-2, exhaust riser, etc.), location, nominal size, shape, material if called out, pressure class if called out, insulation, and a confidence column I fill in 1 to 3. One means I read it clearly off the drawing. Three means I'm guessing from context.

For a 200,000 sq ft office retrofit, that inventory might run 800 to 1,500 line items by the time I'm done with mains, branches, and terminal runouts. It takes one of my subs three to five days, and then I QC behind them. That's before I've set foot on the roof.

**Q:** When pressure class is missing from the drawings — typical for pre-2000 buildings — what do you actually do? How do you derive it for the leakage testing scope?

**Ben:** Right, this is the question. SMACNA leakage class is meaningless without a pressure class to anchor the allowable CFM/100. And on pre-2000 drawings, half the time the mechanical schedule just doesn't list it, or the note says "per SMACNA" and walks away.

What I actually do is triangulate. I pull external static from the AHU schedule if it's legible — that gives me a hint. Anything upstream of the terminal box on a VAV system, I'm assuming 2" or 3" w.g. pressure class minimum, often 4" on the supply main near the unit. Downstream of the VAV, low pressure — 1/2" or 1". Return and general exhaust, usually 1/2". Kitchen and lab exhaust is its own conversation.

Then I cross-check against the TAB report if one exists from the original commissioning, which on a pre-2000 building it usually doesn't, or it's a Xerox of a Xerox. I'll look at the design CFM versus duct cross-section to back-calculate velocity and reason about whether the pressure class assumption is sane. ASHRAE Standard 111 gives me the framework for how to validate the TAB data I'm inheriting.

Then I caveat it. The retrocx report explicitly states the assumed pressure class per system, the basis for the assumption, and that the leakage testing per ASHRAE 215 was conducted at the corresponding test pressure. If the owner wants me to test at a higher class, that's a change order. I am not eating that risk.

**Q:** What's the cost of that to your engagement — to your fixed-fee margin, to the Cx report quality, to your liability?

**Ben:** Honestly? It's the single biggest margin-killer on retro. I bid these jobs fixed-fee, usually $40K to $90K depending on size, and the duct inventory and pressure-class derivation can chew through 60 to 100 hours of labor between me and a sub before I've witnessed a single test. On a $60K engagement at a blended $140/hr, that's a quarter of the fee gone before the field work starts.

Report quality suffers because I'm writing assumptions into the deliverable. An owner reading my retrocx report sees "assumed Class 3 leakage at 2" w.g. test pressure based on inferred construction pressure class" and a sophisticated owner — a hospital facilities director, a university — knows what that means. A less sophisticated owner just sees a hedge and wonders why they paid me.

Liability is real but manageable as long as I document. The CPMP credential and ASHRAE Guideline 0 process give me cover if I follow the process and disclose assumptions. Where I'd get hurt is if I asserted a pressure class without basis and a duct failed at operating pressure later. That's why the confidence flags in my inventory matter — they're not just for me, they're a paper trail.

**Q:** What software do you use across this (CxAlloy, Bluebeam, Excel, Procore)? Where does each fall short for inventory-building?

**Ben:** CxAlloy is my system of record for issues, FPT scripts, and the Cx report structure. About $120 a month per user, I pay it myself. It's good for what it does — issue tracking, owner-facing reports — but it has zero capability for parsing drawings or building a duct inventory. None. I don't expect it to.

Bluebeam Revu is where I live during drawing review. Markups, takeoffs, measurement tools. I can do a manual duct takeoff in Bluebeam — measure runs, tag fittings — and it's better than nothing, but on a scanned PDF where the scale bar is half-illegible, the measurement tool is only as good as my calibration, and I'm calibrating off a dimension string I can barely read. It's a markup tool, not a structured-data tool. Output is a marked-up PDF and a CSV of measurements I have to manually reconcile to my Excel inventory.

Excel is where the inventory actually lives. Pivot tables, conditional formatting on the confidence column, formulas to roll up total surface area for leakage testing scope. It works but it's all manual data entry. Every line item is a person typing.

Procore I use when the GC mandates it on new construction. It is not built for what I do. I tolerate it.

The gap across all four — none of them read a PDF drawing and tell me what's on it. That's the hole.

**Q:** If a tool ingested a folder of old PDF drawings and gave you a structured duct inventory with sizes and pressure-class confidence flags, would you use it on retrocommissioning? On new construction? What would have to be true?

**Ben:** Retrocommissioning, yes, immediately, if it actually worked. That's where the pain is. I'd pay for it on a per-project basis or a low monthly if the throughput justified it. New construction, less interesting — I usually have decent drawings and often a Revit model, and the design team has already produced a duct schedule. The inventory is mostly a transcription exercise on new construction, not a discovery exercise.

What would have to be true. One, it has to handle scanned PDFs, not just born-digital. Skewed scans, raster, lousy resolution. If it only works on clean vector PDFs you've solved the easy half of the problem. Two, the confidence flags have to be honest — I want to see "size read directly from callout" versus "size inferred from adjacent dimension" versus "size unknown, defaulted." If it overstates confidence I'll catch it on the second project and never use it again. Three, output has to be an Excel or CSV that drops into my existing template. I am not changing my workflow to accommodate yours. Four, pressure class inference has to show its work — what AHU schedule it pulled from, what assumption it made about supply versus return. I need to defend it in a report.

Five, and this is the dealbreaker — the time to load a project, run it, and QC the output has to be less than what it takes my sub to do it manually. If I'm spending 40 hours QCing a tool's output instead of 80 hours building the inventory from scratch, fine, that's a win. If I'm spending 70 hours QCing because the tool hallucinates duct that isn't there, I'm done.

**Q:** What's your discretionary software band? At what price-point would you stop self-approving?

**Ben:** I'm a sole proprietor. There's nobody to ask. But I have my own thresholds.

Under $200 a month per seat, I'll trial it on the strength of a 30-minute demo. That's CxAlloy territory and below — I don't agonize over it. If it earns its keep on one project I keep it. If it doesn't I cancel.

$200 to $500 a month per seat, I want to see it solve a specific problem on a specific live project before I commit. I'll do a paid pilot on one engagement. I want a month-to-month, no annual lock-in, and I want to be able to cancel from a settings page, not by emailing a sales rep.

Above $500 a month per seat, or anything with a $5K-plus annual commitment, I think hard. Not because I can't approve it — I can, it's my company — but because that's real money against a fixed-fee margin that's already thin on retro. At that level I want references from other independent CxAs, not from a national engineering firm. Big firms have different economics. A $15K/yr tool is a rounding error to a 200-person MEP shop and a serious bet for me.

And honestly — the thing that kills most pitches I get isn't price, it's that the tool is clearly built for a 50-person GC and somebody's trying to retrofit the pricing for the long tail. Per-project pricing fits my business better than per-seat for anything I'd use intermittently. If I do four retros a year and the tool only matters during the first month of each, charge me per project. Don't charge me for eight months I'm not using it.
