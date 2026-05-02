# Cross-Persona Findings — Round 1, Discovery

**Phase:** discovery
**Date:** 2026-05-02
**Personas:** Marco Reyes (fabricator/estimator), Dana Liu (MEP designer), Aisha Patel (BIM coordinator), Ben Okonkwo (CxA)
**Owner:** Arjun

---

## 1. Hypothesis update

**Going-in hypothesis:** Pressure-class extraction is the highest-value gap in HVAC duct takeoff because TaksoAI handles quantity but requires manual pressure-class reconciliation. Chronic conservative gauge padding costs sub-contractors more than rare catastrophic misreads.

**Confirmed:** The gap exists. All four personas independently surfaced documentation conflicts on pressure class as a meaningful pain. TaksoAI's plateau on geometry-only is a real wedge.

**Updated:** The wedge is **reconciliation, not prediction**. Across all four personas, the pain is "the spec, the schedule, the plan, and the model disagree" — not "I can't extract the value." Marco does prediction in his head; Dana writes the values; Aisha and Ben need them as ground truth. The product is an **auditor that shows its work**, not an oracle.

**Killed:** Three sub-claims didn't survive scrutiny:
- *Catastrophic-rework framing.* Two events in 3 years is a war story, not a budget trigger.
- *Confidence flags reduce verification.* They redirect verification (triage) — they don't replace it.
- *Plan-view extraction is the technical wedge.* The unsolved problem is reading the spec PDF (Section 23 31 13) against the schedule and plan.

---

## 2. JTBDs ranked across personas

| # | JTBD | Personas | Frequency | Severity | Buyer power | Skeptic |
|---|---|---|---|---|---|---|
| 1 | Reconcile pressure class across drawing + spec + schedule with reasoning shown | Marco, Dana, Aisha, Ben | Per-bid / per-revision / per-engagement | High | High (Marco), Med (others) | Survives |
| 2 | Surface spec-vs-drawing-vs-schedule conflicts before they become RFIs | Marco, Dana, Aisha | Per-issue / pre-permit / per-revision | High | Marco = High, Dana/Aisha = Med | Survives |
| 3 | Attribute-level diff (size, pressure class, system tag) between drawing revisions | Aisha (primary), Marco (secondary) | Every 3-4 weeks | High | Med (committee) | Survives — but attribute set may be broader than just pressure class |
| 4 | Build a structured duct inventory from scanned legacy PDFs | Ben | 5-10 retrocx engagements/yr | Very High | High (sole proprietor) | Survives — but TAM is long-tail |
| 5 | Show reasoning behind any tag (cite the source — schedule cell, spec section, AHU schedule) | All four | Continuous | Med-High | High | Survives — strongest universal value-prop signal |
| 6 | Flag intentional-ambiguity overrides that stick across revisions | Dana | Continuous | Med | Low (champion only) | Survives — but engineering complexity is high |

The strongest JTBDs cluster around **conflict surfacing and reasoning transparency**, not extraction itself.

---

## 3. Universal vs. persona-specific pains

**Universal (3+ personas):**
- Pressure class lives in multiple places (schedule, plan, spec, model parameter) that drift independently.
- Existing tools (PlanSwift, TaksoAI, Bluebeam, Navisworks, ACC) explicitly do not cross-link these places.
- "Trust requires reasoning, not confidence scores." Burned by silent AI misses.
- PDFs strip the structured data that exists upstream in Revit / model authoring.

**Persona-specific:**
- *Marco:* chronic conservative gauge padding when ambiguous; per-bid time pressure.
- *Dana:* liability through her stamp; manual QC pass that gets compressed on deadline weeks.
- *Aisha:* drawing-vs-model drift; integration constraint (ACC + Navisworks or dead).
- *Ben:* scanned-PDF input quality; fixed-fee margin erosion on retrocx inventory.

---

## 4. Workarounds — disruption potential

| Workaround | Persona(s) | Disruption potential | Why |
|---|---|---|---|
| Conservative gauge padding | Marco | High | Direct margin recapture |
| Manual QC pass before drawing issuance | Dana | High | The QC step is the wedge for the design phase |
| Bluebeam Sets visual diff | Aisha, Marco, Ben | Medium | Coexists; complements not replaces |
| Excel inventory + manual entry | Ben | Very High | Direct replacement of the margin-killer |
| Outsourced takeoff services | Marco | High (quality-sensitive shops) | Replace via in-house automation |
| TaksoAI for geometry only | Marco | Adjacent | Extend, don't replace |
| Manual property checks in Navisworks | Aisha | Medium | Complement; integration is the wedge |
| Pitot traverse + 3D scan | Ben | Adjacent (different data) | Co-exist for field-vs-design |
| RFI cycles | All four | Reduce, not eliminate | Latency reduction is the value |

---

## 5. Reversed assumptions (the load-bearing finding)

These claims seemed load-bearing in the going-in hypothesis or PRD but didn't fully survive.

| # | Original assumption | Reframe | Source persona |
|---|---|---|---|
| 1 | Predict pressure class | Reconcile pressure class across sources, show reasoning | Marco |
| 2 | Plan-view extraction is the technical core | Spec-PDF NLP (Section 23 31 13 reading) is the moat | Marco |
| 3 | Catastrophic rework is the wedge | Senior-estimator hours per bid is the wedge | Marco |
| 4 | Confidence flags reduce verification | Reasoning transparency reduces verification; flags triage it | Marco |
| 5 | $3-6K/project + $40-80K settlement math justifies a sale | Mostly sunk salary; weak ROI without principal-level validation | Dana |
| 6 | Stamp liability is a daily user-pull motivator | Sales narrative for principals, not user pull | Dana |
| 7 | Pressure class is THE attribute for diff | Size / CFM / material may matter more — re-validate attribute priority | Aisha |
| 8 | ACC integration is a checkbox | ACC + Navisworks integration **is** the product | Aisha |
| 9 | Per-project pricing fits because usage is intermittent | Cash-flow framing not usage; subscription with cancel-anytime works | Ben |
| 10 | New-construction Cx is a weak segment | Installed-vs-design verification on FPT may make it a v2 segment | Ben |

---

## 6. Platform / PRD impact

| PRD section | Current text | Change | Source |
|---|---|---|---|
| §3 Problem statement | "Extract structured duct data from drawings" | "Reconcile pressure class across drawing + spec + schedule, surface conflicts, show reasoning" | Marco, Aisha, Dana |
| §5 Goals | Detection / extraction / classification | Add P0: ingest mechanical specifications PDF (23 31 13) alongside drawings | Marco, Dana |
| §5.2 Non-goals | (implicit) "no spec ingestion" | Reverse — spec ingestion is the moat | Marco |
| §6 User stories | Eli (estimator) speed-of-takeoff stories | Add: Marco-style reconciliation; Dana-style pre-issue QC; Aisha-style attribute-diff | All four |
| §7 Functional reqs | F-01 through F-07 (extract / annotate / classify) | Add F-NEW: spec ingest; F-NEW: conflict detection; F-NEW: reasoning trace per tag | All four |
| §9 Success metrics | Detection recall, extraction accuracy, classification accuracy | Add: conflict-detection precision/recall; reasoning-trace completeness | All four |
| §11.2 Open questions | Buyer persona: estimator / fabricator / BIM | **Resolve:** primary = Marco (fabricator/estimator at 20–200 emp shop). Aisha (ACC integration) is v2 partner persona. | Marco |
| §11.2 Open questions | Wedge: HVAC depth vs. drawing-extraction breadth | **Resolve:** HVAC depth via spec+drawing reconciliation; pressure class is the most spec-coded attribute | Marco, Aisha |

---

## 7. Real-interview validation plan (next step)

Synthetic findings are hypotheses. Schedule live interviews to test the load-bearing claims most exposed to being wrong.

| Persona | Sample size | Top 3 claims to test | Recruitment channel |
|---|---|---|---|
| Marco-equivalent (mech sub estimating principals, 20–200 emp) | 5–8 | (1) chronic-padding cost framing; (2) reasoning-transparency vs. accuracy preference; (3) PlanSwift/Trimble/TaksoAI export priorities | SMACNA chapter intros, MCAA, LinkedIn |
| Dana-equivalent (MEP design engineers at 100+ emp firms) | 4–6 | (1) is "three places" common across firms; (2) does the QC angle survive principal-level scrutiny; (3) what tools have actually made it through IT | ASHRAE chapters, SE2050 network |
| Aisha-equivalent (VDC managers at top-100 GCs) | 4–6 | (1) is pressure class actually the load-bearing attribute or is sizing; (2) what integrations have survived IT review; (3) team-productivity pitch — does it actually unlock budget | AGC BIMForum, LinkedIn VDC groups |
| Ben-equivalent (independent CxAs and small Cx firms) | 4–6 | (1) hour breakdown on retrocx inventory; (2) new-con FPT verification opportunity; (3) per-seat vs. per-project pricing with real numbers | ACG, AABC, BCA member rolls |

---

## 8. Solution-validation hooks (round 2+)

When prototype exists, re-load each persona and ask:

- **Marco:** "Walk me through using this on the next bid that lands. Where does it fit your PlanSwift workflow, and where does it not?"
- **Dana:** "If this generated a pre-issue QC report you could attach to the project file, what specifically would you need it to catch (and not flag) to defend it to your principals?"
- **Aisha:** "Here's the ACC integration shape. Does this actually surface inside your weekly clash workflow, or does it sit on a side dashboard?"
- **Ben:** "Here's the inventory output for one of your retrocx projects. Compare to your sub's manual output — what do you trust, what do you not?"
