---
name: synthetic-user-research
description: Stand up grounded synthetic personas, interview them via embodied subagents, pressure-test claims with a skeptic subagent, and synthesize JTBDs / pains / workarounds. Reusable for both discovery (now) and solution validation (later).
---

# Synthetic User Research

A lightweight, reusable process for product discovery and solution validation when live interviews aren't yet scheduled. The point is not to *replace* real research — it's to stand up a defensible hypothesis set and then re-load the same personas later to validate solutions, without re-doing the framing every time.

---

## When to use

- **Discovery phase.** "What jobs, pains, and workarounds exist in [domain]?" Before live interviews — to harden the hypothesis you'll actually test in the field.
- **Solution validation phase.** "Does [solution X] address the JTBDs we surfaced?" Re-spawn the same persona, hand them the proposed solution, observe the reaction.
- **Pre-PRD assumption stress-test.** Stress-test load-bearing claims before they harden into roadmap.
- **Pre-pricing willingness-to-pay tests.** Before the founder calls a friend, to make sure the friend is asked the right question.

Not a substitute for real interviews. A scaffold that real interviews refine.

---

## The six-step loop

```
[1] Research plan       (purpose + hypothesis + questions)
        ↓
[2] Persona generation  (1-page card per role, grounded in standards/tools/literacy)
        ↓
[3] Interview           (subagent embodies persona; main agent asks questions)
        ↓
[4] Skeptic pass        (separate subagent pressure-tests load-bearing claims)
        ↓
[5] Synthesis           (JTBDs / pains / workarounds / reversed assumptions)
        ↓
[6] (Re-use)            (re-load persona for solution validation)
```

Run steps 1–5 once per discovery round. Run step 6 every time you have a solution to test.

---

## Step-by-step

### Step 1 — Research plan

Without a written plan, the interview drifts. Write three things:

- **Hypothesis.** The single load-bearing belief you want to test. Falsifiable.
- **Research questions.** Five to eight questions whose answers would update the hypothesis. Avoid yes/no — use "how", "what", "when", "what would have to be true".
- **Success criteria.** What finding would make you (a) commit, (b) update, (c) abandon the hypothesis.

Save to `demo/01-research-plan.md` (or similar). Reuse for solution validation.

### Step 2 — Persona generation

For each role you want to interview, write a 1-page card. Key fields:

- Name, role, firm size, tenure
- Standards literacy — specific codes/standards/methods they must know
- Daily tools — software, equipment
- Where the product touches their work
- Initial pain framing (what you *think* hurts)

Ground the persona in source material — for HVAC, that's SMACNA + ASHRAE; for legal it would be FRCP + state procedure; etc. A persona that can't speak the standards of their role will produce generic answers.

Save each persona to `demo/02-persona-{name}.md`.

### Step 3 — Interview (embodied subagent)

Spawn a subagent with this prompt structure:

```
You are [persona name + role]. [Paste persona card.]

The researcher will ask you questions about your work. Stay in character.
Answer with concrete texture from your daily work, grounded in your standards
literacy. Push back if a question contains a wrong assumption — real
practitioners do. Don't be performative. If asked about something you
wouldn't know, say so.

Format: question / answer pairs. Keep answers grounded and specific.
Length: ~1500-2000 words.

Researcher questions:
1. [question 1]
2. [question 2]
...
```

Save the returned transcript to `demo/03-interview-{name}.md`.

The questions come from the research plan. Don't pre-load conclusions in the questions.

### Step 4 — Skeptic pass

Spawn a separate subagent with this prompt structure:

```
You are a research skeptic. Read this interview transcript:
[paste transcript]

For each load-bearing claim the interviewee made, apply these tests:

- Substitutes test — is there an existing tool/service/junior hire that
  already absorbs this pain at acceptable cost?
- Buyer-vs-user test — is this person actually the budget holder?
- Frequency × severity test — chronic-mild rarely buys; acute-episodic does.
- Counterfactual test — what's actually happening today and how bad is the
  outcome when they do nothing?
- Format-fit test — would the proposed product's output land in their
  downstream tooling, or die at the export boundary?

Output: claim-level verdicts (survives / partial / dies) with one-line
rationale each. Be sharp. End with the top 3 reversed assumptions — claims
that *seemed* load-bearing but didn't survive.
```

Save to `demo/04-skeptic-{name}.md`.

The skeptic must be a separate subagent — don't let the persona-embodied subagent grade its own answers.

### Step 5 — Synthesis (main agent)

Pull, in three sections:

- **JTBDs** — `When [situation], I want to [motivation], so I can [outcome].` Frequency, severity, buyer power per JTBD.
- **Pains** — ranked by how often they hit and how badly.
- **Workarounds** — what they do today, and how disruptable each is.

Plus a fourth section: **Reversed assumptions.** Claims the skeptic killed. These are the most valuable output — they prevent the PRD from hardening around an untested belief.

Save to `demo/05-findings-{name}.md`. Aggregate across personas in `findings-cross-persona.md` once you have multiple rounds.

### Step 6 — Re-use for solution validation

When a prototype or solution sketch exists:

1. Re-load the persona card (no edits — same person, new question).
2. Spawn the persona subagent with the prompt:
   ```
   [Persona card.] You previously surfaced these JTBDs and pains:
   [paste prior findings.]
   The team has built/sketched this solution: [paste solution.]
   Walk through whether this would actually address [JTBD 1], [JTBD 2], ...
   What's missing? What would stop you adopting it?
   ```
3. Spawn the skeptic with: "Would this persona actually adopt this? Apply
   the same tests."
4. Synthesize: which JTBDs are addressed, which aren't, what's required
   for adoption.

The persona file is the load-bearing artifact. Keep it stable across rounds so longitudinal claims (what the persona said in discovery vs. solution validation) are comparable.

---

## Anti-patterns to avoid

- **One-shot synthesis without persona embodiment.** Produces plausible but generic output. The skeptic catches less because the original claims weren't grounded.
- **Letting the persona grade its own claims.** Use a separate skeptic subagent.
- **Skipping the research plan.** Drives generic interviews.
- **Editing the persona between rounds.** Breaks the longitudinal comparison.
- **Treating synthetic findings as ground truth.** They're a hypothesis set. Always validate with 5–10 real interviews per persona before locking the product wedge.
- **Skipping standards grounding.** A persona who can't speak SMACNA / ASHRAE / FRCP / GAAP / etc. will produce answers that pattern-match but don't reflect real-world friction.

---

## File layout

```
synthetic-user-research/
├── README.md                             ← entry point for PMs and engineers
├── SKILL.md                              ← this file (the process)
├── templates/                            ← blank starting points
│   ├── research-plan-template.md
│   ├── persona-card-template.md
│   ├── interview-prompt-template.md
│   ├── skeptic-prompt-template.md
│   └── findings-cross-persona-template.md
├── personas/                             ← reusable persona library
│   ├── marco-fabricator.md
│   ├── dana-mep-designer.md
│   ├── aisha-bim-coordinator.md
│   └── ben-cx-engineer.md
└── demo/                                 ← one worked example end-to-end
    ├── 01-research-plan.md               ← per round
    ├── 02-persona-{name}.md              ← snapshot of persona for this round
    ├── 03-interview-{name}.md            ← subagent output
    ├── 04-skeptic-{name}.md              ← subagent output
    └── 05-findings-{name}.md             ← synthesis
```

The `personas/` directory is the canonical persona library — re-load these for any round, including future solution-validation rounds. The `demo/` directory is one worked example. Each new research round can clone the structure or accumulate as `rounds/{date}-{purpose}/`.
