# Synthetic User Research — README

A reusable, agentic process for **standing up grounded user research before live interviews are scheduled**, and for **bringing solutions back to the same personas later** to validate them. Built for the HVAC duct detection thesis, generalizable to any product discovery problem where:

- The buyer/user personas span multiple workflows.
- Domain literacy (codes, standards, tools) is load-bearing — generic personas produce generic answers.
- You want to stress-test the going-in hypothesis before it hardens into roadmap.

This README has two paths:
- **PMs and researchers** — go to [§1](#1-for-pms-and-researchers).
- **Engineers** — go to [§2](#2-for-engineers).

---

## What this is, in 30 seconds

```
[1] Write a research plan      (hypothesis + questions + success criteria)
[2] Pick / write persona cards (grounded in role, standards literacy, tools, buying authority)
[3] Spawn an embodied subagent (it answers in character)
[4] Spawn a skeptic subagent   (separate agent, pressure-tests every load-bearing claim)
[5] Synthesize                 (JTBDs, pains, workarounds, reversed assumptions)
[6] Re-load same personas      (later, to test solutions)
```

Outputs are markdown files under `demo/` (or new round folders). Reusable persona cards live in `personas/`. Templates in `templates/`. Process is documented in `SKILL.md`.

This is **not** a substitute for live interviews. It's a scaffold — for hardening the question set, surfacing assumption risk, and making live interviews more efficient when they happen.

---

## 1. For PMs and Researchers

### 1.1 When to reach for this

- You have a thesis you want to stress-test before you stake the PRD on it.
- You're between funding events and want to walk into the next investor conversation with a defensible discovery story.
- You've drafted a PRD and want to find the load-bearing assumptions before you commit engineering time.
- You're about to schedule live interviews and want sharper questions.
- A prototype just shipped and you want a fast read on which JTBDs you actually addressed before the customer call.

### 1.2 When *not* to reach for this

- The market is unfamiliar and you can't ground personas in real standards/tools/literacy. Generic personas → generic findings.
- You're in late-stage validation where pricing and willingness-to-pay are the open questions. Synthetic personas overstate willingness to pay; use real conversations.
- You need a defensible case for an external audience (board, investors as primary evidence). Synthetic findings supplement live findings; they don't replace them.

### 1.3 Quickstart — your first round in 15 minutes

1. **Write the research plan.** Copy `templates/research-plan-template.md` to `demo/01-research-plan.md` (or a new folder). Fill in: one falsifiable hypothesis, 5–8 research questions, success criteria for *commit / update / abandon*. The questions are the load-bearing artifact — bad questions guarantee bad findings.

2. **Pick a persona.** From `personas/` — there are four ready-made HVAC personas. If you need a new one, copy `templates/persona-card-template.md` and ground it in: standards/codes the role must know, real tool names, firm size, daily workflow, buying authority. A persona without standards literacy will produce LLM-generic answers.

3. **Run the interview.** Open `templates/interview-prompt-template.md`, paste in the persona card and your research questions, and spawn a subagent (Task tool with general-purpose subagent_type, or a Claude Code agent in your CLI). The subagent embodies the persona and returns a question/answer transcript. Save to `demo/03-interview-{name}.md`.

4. **Run the skeptic.** Open `templates/skeptic-prompt-template.md`, paste in the transcript, spawn a *different* subagent. The skeptic applies five tests (substitutes, buyer-vs-user, frequency × severity, counterfactual, format-fit) and outputs claim-level verdicts plus the top 3 reversed assumptions. Save to `demo/04-skeptic-{name}.md`.

5. **Synthesize.** Pull JTBDs (using *When [situation], I want to [motivation], so I can [outcome]*), rank pains, list workarounds, capture reversed assumptions, and tie each to a PRD section that needs to change. Save to `demo/05-findings-{name}.md`.

For the cross-persona aggregation (round-level synthesis): copy `templates/findings-cross-persona-template.md` once you've run multiple personas.

### 1.4 How to evolve the thesis between rounds

The reversed-assumptions section of each round's findings is the most valuable artifact. When a synthetic persona kills a claim, treat it as a hypothesis to test — not as ground truth.

A typical evolution arc:
- **Round 1 — discovery.** Test the going-in hypothesis. Reversed assumptions reframe the wedge.
- **Round 2 — sharpened discovery.** Re-pose the wedge against the same personas with the new framing. Confirm the reframe holds across personas.
- **Round 3 — solution validation (synthetic).** Bring a prototype or wireframe back to the same personas. Use the step-6 prompt structure from `SKILL.md`.
- **Round 4 — live validation.** Schedule 5–10 interviews per persona. Lead with the questions where synthetic findings were *most uncertain* (the partial-survival skeptic verdicts).

The persona files are deliberately stable — don't edit them between rounds, because that breaks longitudinal comparison ("Marco said X in round 1, then Y in round 3 after seeing the prototype" is the kind of finding that justifies build decisions).

If a real interview surfaces evidence the persona card got something wrong (e.g., real fabricators turn out to have $500/mo discretionary not $200), update the card in a new file (`marco-fabricator-v2.md`) and note the version in the next round's research plan. Versioning over editing.

### 1.5 Validating the data — what to trust, what to interrogate

Synthetic findings have a known shape of bias. The skeptic catches some but not all. Watch for:

- **Stated vs. revealed preferences on future behavior.** "Would you stop verifying at 90% accuracy?" — answer is unreliable. Always corroborate with live observation.
- **Overstated willingness to pay.** Synthetic personas don't have real budgets. Treat dollar figures as ranges, validate live.
- **Pain framing that mirrors the prompt.** If the research questions lead, the persona will follow. Check that the JTBDs surface details the questions didn't seed.
- **Missing veto-buyers.** Synthetic personas usually don't surface the IT/procurement/legal hurdles. Add a procurement-skeptic pass for enterprise products.
- **Generic answers.** If a finding could apply to any vertical, the persona wasn't grounded enough. Re-ground with sharper standards literacy and re-run.

Cross-check synthetic findings against three external signals: published competitor positioning, real customer conversations (even informal ones), and forum/community evidence (Reddit, industry-specific subs, SMACNA/ASHRAE forums for HVAC, equivalent for your domain).

### 1.6 Common pitfalls

- **Skipping the research plan.** Generic interviews. Don't skip.
- **Letting the persona-embodied subagent grade its own claims.** Use a separate skeptic subagent. Always.
- **Editing the persona between rounds.** Breaks longitudinal comparison. Version, don't edit.
- **Treating synthetic findings as ground truth in external comms.** They're hypotheses. Cite them as such.
- **Running too many personas at once before a skeptic pass.** The skeptic sharpens the question set; running it after just one persona makes the next persona's interview better.

### 1.7 When to commit, update, or abandon

The research plan's success criteria are the contract. After each round, write the answer:

- **Commit** — JTBDs and reversed assumptions confirm the wedge. Update PRD §X, §Y, §Z. Schedule live validation interviews.
- **Update** — wedge directionally right, framing wrong. Reframe in the next round's research plan; persona cards stable.
- **Abandon** — reversed assumptions kill the central thesis. Spend a session reframing the problem, then start a new round 1 with new questions.

The cost of re-running the loop is hours, not weeks. Don't be precious about iterating.

---

## 2. For Engineers

### 2.1 What you're looking at

A skill — `SKILL.md` — that documents a multi-agent research process. The process orchestrates two subagent roles (persona-embodiment, skeptic) and produces structured markdown outputs that feed product decision-making.

The skill is intentionally *not* a slash command yet. It's a documented process + prompt templates + reference subagent invocations. The promotion path to a Claude Code plugin or a Cowork skill is straightforward (see §2.6).

### 2.2 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Main agent (researcher)                     │
│                                                                 │
│  reads research-plan.md  +  persona-card.md                     │
│           │                                                     │
│           ├──► spawns persona-embodiment subagent ──► transcript│
│           │                                                     │
│           ├──► spawns skeptic subagent (sees only transcript) ──┐
│           │                                                    │ │
│           └──► synthesizes findings.md ◄───────────────────────┘ │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

Two strict invariants:

1. **The skeptic never sees the persona card or the going-in hypothesis.** It grades the transcript on its own merits. This is what surfaces reversed assumptions — if the skeptic shared the persona's premises, it would rationalize them.

2. **The persona is reusable across rounds.** Same persona file in `personas/`, multiple rounds in `demo/` (or `rounds/{date}/`). The persona is the longitudinal subject; the rounds are the experiments.

### 2.3 Customization points

Things you can change without breaking the process:

- **Subagent type.** Currently `general-purpose`. For domain-heavy verticals you can wire a custom subagent type with system prompts pre-loaded with standards excerpts, regulatory text, etc.
- **Skeptic test catalog.** `templates/skeptic-prompt-template.md` defines five tests. Add domain-specific ones — e.g., for regulated markets add a "compliance-fit test"; for B2C add a "habit-formation test."
- **Persona library.** `personas/` has four HVAC personas. Add new ones for new verticals or roles. Standards literacy is the section most worth investing in.
- **Output format.** Findings are markdown today. If your downstream is a Notion DB or Linear project, write a small adapter that parses the findings frontmatter into the destination format.

### 2.4 Adding a new skeptic test

1. Edit `templates/skeptic-prompt-template.md`. Add the new test to the "Tests to apply" list with a one-line definition. The test must be applicable per-claim.
2. Run a regression: re-run an existing skeptic pass on a known transcript and confirm the new test changes verdicts as expected on at least one claim.
3. Document the test in `SKILL.md` § Step 4.

Useful tests we considered but didn't include in v1:
- **Coherence test.** Does this claim contradict another claim in the same transcript?
- **Status-quo bias test.** Is the persona overstating future-state behavior because they want the tool to exist?
- **Vendor-conflict test.** Would this finding hold if the buyer's incumbent vendor offered a similar feature in 6 months?

### 2.5 Swapping the model or provider

The subagent prompts in `templates/` are model-agnostic markdown. They work in:
- Claude Code's `Task` tool (current)
- The Claude API directly (if you build a Python harness)
- Cowork's Agent tool (Cowork mode)

If you change models, re-run a known-good interview on the new model and compare to the existing transcripts. The skeptic's verdicts are the regression metric — if the same skeptic on the same transcript gives materially different verdicts under a new model, you've changed the calibration.

### 2.6 Promoting to a plugin / slash command

If this stops being a one-off and becomes a recurring tool:

1. Convert `SKILL.md` to a `/synthetic-research` slash command. Frontmatter: `argument-hint: <persona-name>` and `description`.
2. Move templates to a `commands/synthetic-research/` directory.
3. Wire the persona-embodiment and skeptic prompts into Task tool calls inside the slash command body.
4. Add a `marketplace.json` entry if distributing as a Claude Code plugin.

The persona library can stay as data files in the plugin or be authored per-deployment.

### 2.7 Performance / cost notes

- A single persona round (1 interview + 1 skeptic + synthesis) is ~30K input tokens, ~10K output tokens. At Sonnet rates that's well under a dollar per round. Negligible.
- Token-cost driver is the persona card length. Keep cards under ~500 lines.
- Latency: each subagent call is ~30–90 seconds. A full round is 2–3 minutes wall-clock.
- Caching: if you re-run with the same persona card, prompt caching cuts subsequent calls significantly (provider-dependent).

### 2.8 Testing / regression

There aren't unit tests in the conventional sense — outputs are natural-language. The regression strategy:

- Pin a "golden" persona + research-plan + transcript + skeptic-pass tuple. Re-run after any template edit; spot-check that verdicts and reversed assumptions stay coherent.
- For high-stakes deployments, run the same persona twice with the same questions. The variance between runs tells you how stable the persona is. High variance → ground the persona harder.
- If a new skeptic test is added, regression-test it against ≥3 existing transcripts before committing.

### 2.9 Known limitations

- Synthetic personas can't surface what they wouldn't know — competitive intel, future market shifts, or unpublished buyer behaviors. Live research closes those gaps.
- The skeptic can be *too* harsh on quantitative claims (Marco's "1 in 10 lost bids" survived as partial — a real interview might confirm it). Don't over-update on skeptic verdicts alone.
- Multi-persona dynamics (e.g., how an MEP designer and a fabricator interact during a real RFI cycle) aren't captured. Use it for individual JTBDs, not workflow ethnography.
- Pricing / willingness-to-pay validation is unreliable from synthetic personas. Use real conversations.

---

## 3. File map (full)

```
synthetic-user-research/
├── README.md                                ← this file
├── SKILL.md                                 ← the process documentation
│
├── templates/                               ← blank starting points
│   ├── research-plan-template.md
│   ├── persona-card-template.md
│   ├── interview-prompt-template.md
│   ├── skeptic-prompt-template.md
│   └── findings-cross-persona-template.md
│
├── personas/                                ← reusable, version-stable persona library
│   ├── marco-fabricator.md
│   ├── dana-mep-designer.md
│   ├── aisha-bim-coordinator.md
│   └── ben-cx-engineer.md
│
└── demo/                                    ← one worked example
    ├── 01-research-plan.md
    ├── 02-persona-marco.md                  ← snapshot of persona for this round
    ├── 03-interview-marco.md
    ├── 04-skeptic-marco.md
    └── 05-findings-marco.md
```

For new rounds, mirror the demo/ structure under `rounds/{YYYY-MM}-{purpose}/` to keep history clean.

---

## 4. FAQ

**Why not just use ChatGPT / Claude in chat to "act as a fabricator"?**
You can. Two things this skill adds: (a) the separate skeptic agent (no quality control if the same agent makes and grades claims), and (b) the persistence of persona cards across rounds, so solution validation in 6 weeks compares to discovery from today.

**How is this different from a generic "user research workflow"?**
The standards-literacy grounding and the skeptic protocol. Most LLM persona prompts produce plausible-but-vague answers. Grounding in SMACNA tables / ASHRAE chapters / specific tool names produces texture. The skeptic catches drift back to genericism.

**Can I use this for a different vertical (legal, fintech, healthcare)?**
Yes. The personas/ library swaps out; the templates and SKILL.md are domain-agnostic. You'll need to ground new personas in the appropriate codes — FRCP and state procedure for legal, GAAP and FASB for fintech, ICD-10/CPT/HIPAA for healthcare. The skeptic test catalog stays.

**How do I know if my persona is grounded enough?**
Ask the persona-embodied subagent to cite a specific table, section, or product feature in its first answer. If it ducks ("I'd consult the relevant standards") instead of citing ("Section 23 31 13, paragraph 2.3"), the persona card is too thin.

**The skeptic killed my favorite finding. Now what?**
Read the rationale carefully. Sometimes the skeptic catches a real over-claim (good — fix the PRD). Sometimes the skeptic is being too harsh on a valid intuition. The remedy is the same in both cases: schedule a real interview that asks the question the skeptic raised.

**Can synthetic findings ever justify a build decision?**
Not on their own. Synthetic findings justify an *investment* (next round of live research, a prototype, a customer call). Build decisions need live validation. Treat synthetic as the cheaper-than-coffee filter that makes the expensive validation conversations more productive.

---

*Maintainer: Arjun. Created 2026-05 for the Techjay HVAC duct detection take-home. Generalizable beyond.*
