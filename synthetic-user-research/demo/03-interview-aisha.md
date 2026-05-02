# Interview — Aisha Patel (VDC manager / BIM coordinator)

**Method:** Embodied subagent. Persona card from `personas/aisha-bim-coordinator.md`. Stayed in character.

---

**Q:** Walk me through your last drawing-revision reconciliation. Where does pressure class show up — in your model, the contract drawing, the spec? How do you handle the disagreements?

**Aisha:** Last one was two weeks ago, hospital project, AHU-3 zone on level 4. We got Rev 6 of the mech package — the engineer reissued sheets M4.01 through M4.08 plus an updated spec section 23 31 13. My job is to reconcile what changed against Rev 5, then update the federated model in Navisworks so we're not running clash tests against stale geometry.

Pressure class lives in three places, and that's the whole problem. On the contract drawings, the engineer schedules it on the duct schedule sheet — usually M0.02 — by system: supply medium-pressure 3 inch w.g., return low-pressure 1 inch, exhaust 2 inch. Sometimes they'll annotate a transition on the plan view if pressure class changes mid-run, but a lot of times they don't, and you have to infer it from the schedule plus the system tag. In the Revit model from the MEP firm, pressure class is a parameter on the duct family — sometimes populated, sometimes not, depends on whether the modeler set it up correctly. And then in the spec, division 23, you've got the SMACNA reference, gauge tables, reinforcement spacing — that's where the mech sub actually goes to fabricate.

Disagreements? On Rev 6 the engineer bumped a return main from low to medium pressure between gridline F and J because they upsized the AHU. The plan view got the new sizing. The schedule on M0.02 didn't get updated. The Revit model came across with mixed parameters — some segments said 1 inch, some said 2 inch w.g. I caught it because the duct sizes on the plan jumped and that didn't match what the schedule showed for return main sizing. I logged it as a coordination issue in ACC, kicked an RFI to the engineer through Procore, and we held the area until they responded. Took six days. Meanwhile the mech sub is calling me asking when they can release that level for fabrication.

**Q:** When the 2D drawing and the 3D model disagree on something material — size, system tag, pressure class — what do you actually do? Walk me through the most recent one.

**Aisha:** Contract drawing wins. Always. The model is a coordination tool, the 2D sealed sheets are the contract. So when they disagree, my workflow is: confirm the disagreement, document it with screenshots in Bluebeam, log it as an issue in ACC with the model element ID and the sheet reference, and then it goes to the engineer as an RFI if it's material.

Most recent — last Thursday actually. We had a 24x12 supply duct on the plan, M3.04. Same run in the Revit model came in as 26x12. Two-inch difference in width. Doesn't sound like much until you realize that run threads between a structural beam and a cable tray with about three inches of clearance. At 26 it clashes with the tray. At 24 it doesn't.

What I did: pulled both up side by side in Bluebeam Revu using the Sets feature to compare Rev 5 and Rev 6 of M3.04. The plan said 24. Opened Navisworks, selected the duct segment, checked properties — model said 26. Took screenshots of both, dropped them into an ACC issue, assigned to the mech sub and copied the engineer. Asked: which is the design intent? Engineer came back two days later and said the model was right, the plan annotation didn't update on Rev 6. So now the contract drawing is wrong, and we're waiting on a sheet reissue or an ASI to make the model match the contract. Until then that area is on hold for shop drawing submittal.

That's a small one. I've had pressure class disagreements where the model says low-pressure and the schedule says medium, and the mech sub has already started fabricating to low. Those get expensive.

**Q:** What's the cost of that drift — to your coordination schedule, to clash-decision quality, to the RFI volume going back to the engineer?

**Aisha:** It's the thing that eats my week. Let me give you real numbers from the hospital.

On a typical revision cycle — the MEP firm reissues every three to four weeks — I get somewhere between 40 and 80 sheets back. My team is me plus two BIM coordinators. We spend probably three full days per revision just figuring out what actually changed at the attribute level. Not what moved geometrically — Navisworks shows me that — but what changed in size, tag, system, pressure class. The visual diff in Bluebeam catches annotation changes on 2D, but it doesn't tell me the duct family parameter in Revit went from 2 inch w.g. to 3 inch w.g. when the geometry didn't move.

Clash-decision quality suffers because if I'm running clash tests on a model where I'm not sure the attributes are current, I'm making routing decisions on stale data. I'll move a duct to clear a beam, and then find out two weeks later the duct should've been a different pressure class which means different reinforcement spacing which means a bigger clearance envelope, and now my resolution doesn't work.

RFI volume — on this hospital we're running about 35 to 50 RFIs a month back to the engineer, and I'd say a quarter of those are attribute-disagreement RFIs. Sheet says one thing, model says another, spec implies a third. Each one is roughly a five-to-seven-day turnaround. That's holding up shop drawing submittal, which holds up fabrication, which holds up the two-week look-ahead.

If I had to put a number on it — drift on a $200M project probably costs us two to three weeks of coordination float across the run of the job. That's real money to the GC.

**Q:** What software do you use — Navisworks, ACC, Bluebeam, Procore, Revu? Where does each fall short on revision-to-revision attribute comparison?

**Aisha:** Navisworks Manage is my federated model. Clash detection, walk-throughs, the coordination meeting workhorse. It shows me geometry diffs between model versions if I append both, but it does not give me a clean attribute diff. I can select an element and see properties, but comparing properties on the same element across two model versions is manual. Click element in Rev 5, write down values. Click same element in Rev 6, compare. Nobody does this at scale.

ACC — Autodesk Construction Cloud — is where the model lives, where the issues live, where the RFIs route. ACC has model coordination and a Compare feature on sheets, but the sheet compare is visual. It highlights what looks different. It doesn't tell me a duct's pressure class parameter changed from 1 to 2 inch w.g.

Bluebeam Revu with Sets — this is my 2D revision compare tool. Overlay Rev 5 on Rev 6, color the differences. It's good. But it's pixels. If the engineer changed a duct schedule cell from "Low" to "Medium" and the cell is in the same place, Bluebeam catches it as a text change, sure, but it doesn't connect that schedule change to the model element. I have to make that connection manually.

Procore is RFI and issue tracking. Not a comparison tool.

Revit — I read it, I rarely author. Has a compare-models add-in but it's clunky and not part of my weekly workflow.

The gap, plainly: nothing I own does element-level attribute diff between revisions across the 2D contract drawing and the 3D model. That's the gap.

**Q:** If a tool gave you attribute-level diffs — size, pressure class, system tag — between drawing revisions, not just visual diffs, would you use it? What would the integration story have to look like for your firm?

**Aisha:** Yes. Genuinely yes. That's the gap I just described.

But — and this is where most pitches fall apart in the first thirty seconds — if it's a standalone tool, I'm out. I cannot add a sixth platform to my coordinators' workflow. They already context-switch between Navisworks, ACC, Bluebeam, Procore, and Revit. A standalone tool with no integration is a non-starter.

Integration story has to be: it pulls from ACC where my models and sheets already live. It writes findings back as ACC issues, with the element ID, the sheet reference, the attribute that changed, and the prior value. Ideally it surfaces inside Navisworks too — when I select a duct in the federated model, I want to see "this segment's pressure class changed from 1 to 2 inch w.g. on Rev 6, here's the source sheet." If it can route an RFI draft into Procore, even better, but ACC issues is the minimum.

The pitch I want to hear is "we plug into ACC, we add an attribute-diff layer on top of your existing revision workflow, your team doesn't change tools." If the pitch is "log into our portal," I've stopped listening.

Also — and this matters — the value frame has to be team productivity. If you tell me this saves Aisha three days a revision, my director shrugs. If you tell me this saves my coordination team six person-days a revision, reduces RFI volume by 20%, accelerates shop drawing release by a week — now I can take it to the VP of Operations. Budget approval is a team-productivity story, not an individual-tool story.

**Q:** Who buys software at your firm? At what price point does it become a real decision vs. something you can run through?

**Aisha:** I have discretionary authority up to about $500 a month per user. Below that, on a small pilot — say two to three seats — I can put it on the project budget, run it for a quarter, see if it sticks. That's a pilot, not a purchase.

Above $5,000 a month total, or $50,000 annually, it goes up the chain. VDC director signs off first, then VP of Operations, then IT does a security and integration review — and IT is where most pilots die. They want to see SOC 2, they want to see how the tool authenticates against our ACC tenant, they want to know what data leaves our environment. A vendor that can't answer those questions cleanly doesn't make it past IT.

Annual cycle is Q4. We do a software review every fall — what are we paying for, what's getting used, what's getting renewed, what's new on the radar for next year's budget. If you want to be in our 2027 budget, you need to be in front of me by August 2026 with a successful pilot already running. Cold outreach in November is too late.

The realistic path: get me a pilot for under $5K total, show me measurable team-productivity gains across one revision cycle on one project, and then I have a story to tell my director in Q4.
