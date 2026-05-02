# Interview — Dana Liu, PE (MEP design engineer)

**Method:** Embodied subagent. Persona card from `personas/dana-mep-designer.md`. Stayed in character.

---

**Q:** Walk me through how pressure class flows from your design into the deliverable. Where does it live in the drawings — schedule, legend, spec, per-duct?

**Dana:** Pressure class is a design decision I commit to during DD and lock in CDs. It originates from the fan selection and the system layout — for a typical office VAV system, supply upstream of the terminal units is medium-pressure, 2" or 3" w.g. depending on the external static; downstream of the VAV terminal unit it drops to 1" w.g. low-pressure. Returns are usually 1". Lab exhaust, depending on whether it's manifolded and how much stack effect we're dealing with, can run 4" or 6" — and that's where I get careful.

Where it lives: the duct schedule on M-001, or sometimes M-002 if the legend sheet eats the real estate. The schedule has columns for system, service, pressure class, seal class, leakage class, and insulation per 90.1 Table 6.8.2. I also note pressure class transitions on the plans themselves — you'll see a tag at the VAV like "2" w.g. UP / 1" w.g. DN" — and the spec, Section 23 31 13 Metal Ducts, references the schedule. So three places: schedule (authoritative), plans (locational), spec (performance and construction requirements). The schedule is the source of truth. If those three disagree, I have a problem, and it's my problem.

**Q:** When you get an RFI on duct construction, what was usually unclear — your error or the contractor's? Walk me through one.

**Dana:** Honest answer? It's split. Maybe 60/40 their read versus my ambiguity, but the 40 is the part that keeps me up. I'll give you a real one. Lab project in Cambridge, two years back. Manifolded fume hood exhaust, I'd specced 4" w.g. negative pressure class on the schedule. The plans showed the duct routing through a shaft, and at one point it reduced and tied into a riser I'd labeled — on a different sheet — as 3" w.g. because that section was downstream of the bypass damper and I'd done the calc showing the static there was lower. Contractor's RFI: "Confirm pressure class for riser DX-3 between levels 4 and 6. Schedule indicates 4" w.g.; plan tag on M-401 indicates 3" w.g. Please clarify for fabrication."

That's a fair RFI. The schedule was the catch-all; the plan tag was me being clever about a specific run. The contractor needed one number to give the sheet metal shop. I responded: 4" w.g. for the entire riser, treat the plan tag as superseded. Then I issued an ASI to clean up the drawing. Was it my error? Yes — I should have either held one class throughout or made the transition explicit with a note explaining why. The contractor wasn't wrong to ask. They can't fabricate to ambiguity, and I don't want them guessing.

**Q:** What's the cost of that ambiguity to your firm — to RFI volume, to your hours, to your liability?

**Dana:** Let me break it apart. RFI volume on a major project — call it a 200,000 sf lab — I'll see 80 to 150 mechanical RFIs total over CA. Of those, maybe 8 to 15 touch ductwork construction, and of those, 2 to 5 are pressure-class or seal-class clarifications that trace back to my doc set not being perfectly self-consistent. Each RFI, soup to nuts — log it, read it, check the model, check the spec, draft a response, route through the PM, sometimes sketch a clarification — is two to four hours of my time. At my billable rate plus the PM and the admin overhead, you're at $600 to $1,200 per RFI all-in. So pressure-class ambiguity alone is maybe $3,000 to $6,000 a project in pure response cost.

That's not the scary number. The scary number is the change order. If the contractor fabricated to the lower pressure class and I have to direct them to upgrade — even if the schedule was technically correct and they should have caught it — we're in a fight. Sometimes the firm eats it to keep the relationship and avoid a claim. I've seen $40K and $80K hits on jobs where the documentation was ambiguous enough that our E&O carrier said "settle."

Liability is the third leg. I stamp the drawings. If a duct fails — pulls apart, leaks unacceptably, fails a pressure test — and it traces to a documentation conflict, that's my license on the line. Not theoretically. So I'd rather spend the hour at end-of-CDs cross-checking than the day in a deposition.

**Q:** What software do you use across this — Revit MEP, MagiCAD, Bluebeam? Where does each let you down on pressure class and shop drawing review?

**Dana:** Revit MEP is the model. We use the built-in duct system types and I'll set pressure class as a system parameter, sometimes with MagiCAD overlaid for sizing because the native router is — fine, but MagiCAD's calc engine I trust more for medium-pressure mains where I'm doing static regain. Trane TRACE 3D Plus for loads. Bluebeam Studio for sheet markup and the QC pass before issue. ACC for the federated model and clash.

Where they let me down: Revit knows the pressure class. It's a parameter on the duct. But the deliverable is a PDF. The minute I plot to PDF, all that structured data — the pressure class on every segment, the system type, the seal class — collapses into a tag on a sheet, if I remembered to place the tag, and a row in a schedule, if the schedule view captured every system. The model is structured; the contract document is not. That's the gap. I have a coordinated, parameterized model that knows everything, and I hand over a 400-sheet PDF set that requires a human to verify internal consistency.

Bluebeam is good for markup and for someone catching that my duct schedule on M-001 says 2" w.g. and my spec section 23 31 13 says 3" w.g. — but only if a human reads both and notices. It does not cross-check. There's no engine that says "schedule and spec disagree on system SA-1 pressure class." That's a manual QC step, and on a deadline week with three projects issuing, it's the step that gets compressed.

Shop drawing review during CA: contractor sends a 200-page submittal of duct fabrication drawings. I'm checking that what they're building matches my pressure class, seal class, and that their gauge selection — which I defer to them on, that's SMACNA and their means and methods — corresponds to a pressure class consistent with mine. I'm not reading SMACNA gauge tables. I'm checking the cover sheet schedule against my schedule and spot-checking. If they got it wrong and I miss it, that's bad.

**Q:** If a tool surfaced spec-vs-drawing-vs-schedule conflicts in your OWN document set BEFORE you issued for permit, would you use it? What would have to be true?

**Dana:** Yes, in principle. That's exactly the QC layer that's missing. But a few things have to be true before I'd trust it on a stamped set.

One: it has to read the actual deliverable — the PDF set and the spec — not require me to re-export everything from Revit in some special format. Half my legacy work is AutoCAD MEP. If the tool only works on a perfect Revit model, it's solving the wrong problem.

Two: false positives kill these tools. If it flags fifty things and forty-five are noise — "the legend sheet uses different abbreviations than the spec" — I'll stop opening it by week three. The signal has to be tight. I'd rather it catch 70% of real conflicts with low noise than claim 100% with a flood.

Three: it cannot try to design. The minute it suggests "you should use 2" w.g. here," I'm out. Pressure class is a judgment call tied to fan static, layout, and economics. I want it to flag inconsistencies in what I've already decided, not second-guess the decision.

Four: it has to handle the ambiguity that's intentional. Sometimes a tag on a plan supersedes the schedule for a specific reason. The tool needs to let me acknowledge and override, and have that override stick across revisions.

Five: defensible. If it generates a QC report I can put in the project file, that's a liability story I can tell. "We ran the cross-check, here's the log." That has value beyond the catches.

**Q:** Who buys software at your firm? At what price-point does it become a firm-level decision vs. one you can champion through?

**Dana:** I have zero discretionary budget. Zero. Anything that touches our deliverable workflow goes through IT for security review, and the call gets made by the CIO with input from the practice leaders — mechanical, electrical, plumbing principals. Decision cycle is 12 to 18 months from first demo to deployment, longer if it's enterprise.

Price-point thresholds, roughly: under $5K total, a project manager might expense it as a one-off and nobody asks questions. $5K to $50K, that's a practice-leader conversation, and they'd want a pilot on one or two projects with a champion — that could be me — writing up the value. Above $50K, or anything that becomes a per-seat firm-wide rollout, that's CIO-and-principals, capital planning cycle, IT security review, probably a procurement RFP if we're being formal.

What gets me to champion something through that gauntlet: it has to either save real hours that I can point to on a timesheet, or reduce a liability exposure I can articulate to the principals. Coordination drag is the easiest sell because everyone feels it. "Replaces design judgment" is the easiest no. And honestly, if it's a tool that protects my stamp, I'll fight for it harder than for one that just makes my Tuesdays nicer.
