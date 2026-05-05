# Ductline — HVAC Duct Detection & Annotation

> [!IMPORTANT]
> **Looking to install or run the project?** Head straight to [`implementation/README.md`](./implementation/README.md) — prerequisites, dev setup, run instructions, and the API surface all live there.
>
> This document is product-level: what it does, why each version exists, what works today, and the production roadmap.

> **Status:** V3 in iterative validation. V1 + V2 archived as design + retrospective. Roadmap and standards-based timeline below.
> **Repo layout:** `/PRD.md` · `/SOLUTION-DESIGN.md` (V1) · `/SOLUTION-DESIGN-V2.md` · `/SOLUTION-DESIGN-V3.md` · `/adr/` · `/implementation/` · `/sample-HVAC/` (5 benchmark drawings)
> **Author:** Arjun Sarath

---

## 1. What this product does

Reads a single-page HVAC duct-layout drawing (PDF or image), detects each duct segment, extracts its dimension (`24"×16"`, `12" Ø`, …), classifies its pressure class against SMACNA tiers, and surfaces every detection with a reasoning trace the user can click into. The output is structured duct data that can drive cost estimation, fabrication cut lists, or as-built records — work that is currently done by hand at every estimator and shop.

The product wedge is **drawing → structured duct data** — replacing the manual re-keying that happens today across estimating, fabrication, and facilities.

---

## 2. Three approaches, one shipping path (V3)

Three architectures were attempted in sequence. The sequence matters for understanding the current state and why V3 looks the way it does.

### 2.1 V1 — Hybrid VLM + deterministic CV (built, observed limits)

> Full design: [`SOLUTION-DESIGN.md`](./SOLUTION-DESIGN.md). Implementation under `implementation/` previously used this path.

**Architecture:** 7-stage pipeline (Ingest → Quality → Region → Detect → Extract → Classify → Assemble). Stage 4 calls a vision-language model (VLM, `llama3.2-vision` 11B) once per drawing for duct bounding boxes, with a deterministic CV fallback (HoughLinesP + parallel-pair sweep).

**Why it doesn't work in production today:**

| Failure mode | Frequency on benchmark | Root cause |
|---|---|---|
| Malformed JSON from VLM | 4/5 drawings | Open-source 11B vision models can't reliably emit structured JSON for technical drawings |
| Hallucinated regular-grid bboxes | 2/5 drawings | Model parrots its training-distribution prior (web charts) instead of reading the input |
| Ollama timeout (≥3 min on host hardware) | 1/5 drawings | 11B model's vision pass exceeds soft latency budget on consumer hardware |
| CV-only fallback over-recalls | 5/5 drawings | HoughLinesP fires on walls, columns, grid lines, drawing borders — without OCR-proximity gating, every parallel pair becomes a candidate |

The 5-drawing sweep on V1 hit the 60-candidate cap on every drawing because of CV over-recall, with `[cv_fallback]` stage markers showing the VLM never actually contributed. **The seam (`VLMClient` Protocol, ADR-0002) survives.** Swapping to Anthropic Claude vision (or `llama3.2-vision:90b`) would likely meet the latency + JSON-quality bar — but that lands the product on a paid frontier API, which conflicts with the on-prem requirement that came out of user research with government/AEC firms.

**Why V1 is parked, not shipped:** the on-prem constraint plus the open-source VLM unreliability means V1's hybrid bet is unwinnable today. The 11B model isn't good enough; the 90B model is too slow on commodity hardware; the frontier proprietary models can't run on-prem. The pipeline stages around the VLM (ingest, OCR, classify, assemble) all work — the bet was the VLM call itself.

### 2.2 V2 — V1 + reviewer loop + region-aware detection (designed, not shipped)

> Full design: [`SOLUTION-DESIGN-V2.md`](./SOLUTION-DESIGN-V2.md).

V2 was the natural enhancement: a reviewer-agent second pass on low-confidence detections (ADR-0009), tiled detection with trail context (ADR-0008), and categorizer-first ordering (ADR-0010) so plan-view regions get sized differently from schedules and notes.

**Why V2 isn't being built right now:** V2's improvements all assume the underlying VLM detection works. They make a working detector more accurate; they don't fix a detector that produces malformed JSON 80% of the time. Each V2 enhancement is an additional VLM call — adding `n` more chances for the same failure. Building V2 on top of an unreliable VLM would compound the unreliability, not compensate for it.

V2 stays designed and named because every enhancement (reviewer loop, tiled detection, categorizer ordering) is genuinely valuable **once a reliable detector exists**. V3 is the path to a reliable detector.

### 2.3 V3 — color-driven deterministic pipeline (current, in iteration)

> Full design: [`SOLUTION-DESIGN-V3.md`](./SOLUTION-DESIGN-V3.md).

**Architecture posture:** the VLM is removed from the detection path entirely. The user picks the *color* of each duct system on the rendered page (a single click per system); a deterministic pipeline does the rest — HSV color masking → flood-fill → skeleton + distance transform → OCR-token attribution → SMACNA pressure-class classification.

This trades the failure mode of "VLM is unreliable" for the constraint of "user must identify the duct color once per system." On color-coded drawings (the dominant convention in commercial mechanical drawings) that constraint is fast and reliable.

**What works today (5-drawing benchmark):**

| Drawing | Convention | V3 result | Trustworthy widths? |
|---|---|---|---|
| 01 — `afdb-clean-cad` | Saturated colored outlines | 25 segments, dim + pressure class extracted | ✓ |
| 02 — `newwest-mixed-trades` | Black closed-outline ducts + callout boxes | 20 segments, 2 with extracted CFM | ✓ |
| 03 — `caddsultants-shop` | Saturated colored outlines | 35–60 segments (regression-tested) | ✓ |
| 04 — `asc2018-bid-set` | Parallel-wall dark ducts | 10 segments via centerline mode | ✗ — widths bogus |
| 05 — `federal-attachment` | Parallel-wall dark ducts, dense | 58 segments via centerline mode | ✗ — widths bogus |

Drawings 01–03 are production-quality. Drawings 04–05 attribute dim labels correctly but the pixel-width measurement is unreliable because the pipeline doesn't yet handle parallel-wall ducts with proper geometric width extraction (Pattern A — see §5).

---

## 3. Why these failure modes are not bugs

Three claims that need to be defensible:

**On V1 + V2:** open-source VLMs at sizes that run on-prem (≤90B parameters) do not reliably produce structured detections from engineering drawings. This isn't a bug to be fixed by better prompting — it's the current state of small-VLM capability. Frontier proprietary models (Claude vision, GPT-4o vision) are sufficient but require sending drawings off-prem, which the user-research interviews explicitly rule out for government, defence, and large AEC firms. The architectural seam (`VLMClient` Protocol) is preserved so the integration is one config flip away when capability or compliance change.

**On V3 dimensional accuracy on parallel-wall plans:** the deterministic pipeline assumes a duct is rendered as a *closed* shape — colored outline (Pattern B), colored centerline (Pattern C), or solid colored fill (Pattern A). Drawings 04 and 05 use **uncolored parallel walls** (two black lines forming the duct boundaries, with the duct labeled inline). To measure pixel width on this convention, the pipeline must detect *pairs of parallel lines* and compute the perpendicular gap. That's a Hough-transform + parallel-pair-clustering step that hasn't been implemented yet. Centerline mode produces the right *attribution* (each label snaps to the right duct location) but the width is the dilation thickness, not the duct width — so confidence flags from centerline mode are correctly suspect.

**On the pivot to "user picks a color":** an alternative would be auto-detecting the duct system color. We considered this and parked it: an auto-detector that gets the wrong color produces zero detections silently, which is harder to recover from than a UI that takes one click. The picker UX (cursor-following magnifier, exact-RGB sampling, click-rejection feedback) has been iterated on this trade-off — see [`SOLUTION-DESIGN-V3.md` §5.4](./SOLUTION-DESIGN-V3.md).

---

## 4. Production timeline (standards-based)

Timeline frames each milestone as **"the product handles drawings of standard X in Y months."** Standards here are the conventions the AEC industry actually uses, observed across the benchmark set and in [`competitor-research.md`](./competitor-research.md).

```
Now (M0):     V3 alpha — color-coded Pattern B drawings work end-to-end.
              Manual color pick. Single page. SMACNA pressure class.
              5-drawing benchmark: 3/5 trustworthy, 2/5 partial.

M1–M2:        Pattern A (parallel-wall ducts) via OCR-anchored Hough.
              Drawings 04–05 produce trustworthy widths.
              Coverage shifts from 3/5 to 5/5.

M3–M4:        Schedule + legend extraction.
              Equipment lists, room schedules, and material specs become
              structured output alongside duct geometry.

M5–M6:        Multi-page + cross-sheet topology.
              Most real mechanical sets are 8–40 sheets. Cross-references
              ("see M2.05 for diffuser detail") need to resolve, and ducts
              that branch across sheets need to stitch.

M7–M9:        Dark-line auto-detection (no manual color picking).
              For drawings without a color-coded system, a small classifier
              identifies "this drawing uses parallel-wall convention" and
              auto-configures the pipeline. Reduces user friction to one
              upload.

M10–M12:      Confidence calibration via labeled corpus.
              Build a 200–500 drawing labeled corpus across estimating
              firms; calibrate confidence flags so HIGH means HIGH and
              LOW means a human definitely needs to look. This is what
              makes the product trustworthy enough for unattended ingest.

Year 2 Q1:    Hybrid VLM-assisted detection (see §5.2).
Year 2 Q2:    Multi-trade drawings (HVAC + plumbing + electrical sharing
              a sheet) — the V3 pipeline gates by user-picked color so
              this is mostly a UX problem, not an algorithm problem.
Year 2 Q3:    Custom detection model (see §5.3).
Year 2 Q4:    Production-grade unattended ingest with SLA-bounded latency
              and confidence calibration.
```

Caveats: timelines assume one engineer full-time on detection/algorithms, plus part-time front-end + ops. Each "M*N*" milestone is **2–3 weeks of focused implementation + 1 week iteration on the benchmark**. Real-world drift (new drawing convention surfaces in a customer pilot, regulatory change, label noise in collected corpus) can push any milestone by a multiple. The timeline is what's defensible if the product moves at the pace V3 has shipped at.

---

## 5. Plans for improving the current model

Three improvement vectors, ordered by ROI.

### 5.1 V3 deterministic pipeline — incremental wins (M0–M6)

Each item below is a focused 1–3 week delivery. They compound.

| Improvement | Why | Effort |
|---|---|---|
| **OCR-anchored Pattern A** — for each dim label, find local parallel walls via Hough + pair-clustering and measure perpendicular gap as the pixel duct width. | Drawings 04+05 produce trustworthy widths. Coverage 3/5 → 5/5. | 2–3 weeks |
| **Pattern A auto-detection** — recognise "this drawing has parallel-wall ducts" from a sparse-detection signal and auto-flip the pipeline. | Removes a user decision. | 1 week |
| **Schedule + legend extraction** — separate stage that recognises tabular regions and extracts cell content. | Adds equipment lists + material specs to the structured output. | 3–4 weeks |
| **Confidence calibration** — collect labeled ground truth on the benchmark + 200–500 additional drawings; tune the band-pct thresholds and dim-text-vs-pixel-width tolerances against the labels. | `dim_confidence: high` becomes statistically reliable; downstream consumers can trust unattended HIGH detections. | 6 weeks |
| **Multi-page support** — frontend + backend changes so a multi-sheet PDF is processed sheet-by-sheet, with cross-sheet duct topology resolved (`see M2.05`-style references parsed). | Most real estimating jobs ship as 8–40 sheet sets, not single sheets. | 3–4 weeks |
| **Drawing rotation auto-correction** — currently we trust the source orientation. Some scanned drawings arrive rotated. Detect via OCR-text orientation majority and rotate before processing. | Robustness on scanned input. | 1 week |
| **Round-duct attribution improvements** — round dims (`13" Ø`) currently attribute via the same in-mask rule as rectangular; round ducts are often labeled with leader lines outside the mask, so a leader-line tracer would help. | Round-duct attribution rate improves on drawings with leader-style callouts. | 2 weeks |

### 5.2 Hybrid VLM-assisted (Year 2)

The architectural seam (`VLMClient` in V1, ADR-0002) survives in the codebase precisely so this option remains open. Two flavours:

1. **Frontier proprietary VLM behind an opt-in flag** — when a customer is fine sending drawings off-prem (commercial estimating firms, non-defence work), Claude vision or GPT-4o vision detects duct regions and the V3 deterministic pipeline measures them. This is straightforward to ship; cost is per-drawing API spend.
2. **Quantised + fine-tuned open-source VLM for on-prem** — take a 90B-class vision model, fine-tune (LoRA adapters) on a labeled corpus of engineering drawings (~5K–10K labeled drawings), quantise to 4-bit (Q4_K_M or Q4_0), deploy via Ollama or vLLM on a single 24GB GPU. Cost estimate: $20–50K data + compute, 6–9 month timeline, success conditional on capability of the base model. This is high-risk: open-source VLMs may not improve enough at the 90B scale to overtake the deterministic pipeline + manual color pick on accuracy.

The split is a business decision (cloud-OK customers vs on-prem-only customers), not a technical one. Both share the same pipeline downstream of detection.

### 5.3 Custom detection model (Year 2 H2)

A purpose-trained object detector for HVAC duct outlines on rendered drawings:

- **Architecture choice:** Faster R-CNN with FPN backbone (mature, calibrated confidence) or YOLOv8/v10 (faster inference). For rendered CAD drawings the speed advantage of YOLO matters less than calibration; lean toward Faster R-CNN.
- **Data:** 1,000–5,000 labeled drawings spanning the conventions observed in the sample set + customer-contributed drawings collected during V3 deployment. Labels per drawing: every duct outline as a polygon, every dim label as a bbox + transcribed text. Estimated labeling cost: $30–80 per drawing for technical labelers, so $30K–400K total depending on corpus size.
- **Training:** ~$5–10K compute on a labeled corpus of 5K drawings. Iterations across architecture, augmentation, and hyperparameters add 3–4 months calendar time.
- **Replaces:** the user color-picking step. Upload → segments come out.
- **Risks:** distribution shift (new customer drawings differ from training corpus), confidence calibration (object detectors notoriously over-confident), and the need to ship V3 deterministic as the fallback when the model says "no detection."

The custom model is a *replacement* for the picker UX, not for the rest of the V3 pipeline. Pressure-class classification, dim attribution, and SMACNA logic continue to live downstream of detection.

---

## 6. Why each pivot was the right call

Defensible reasoning for the architectural decisions, in case future contributors look at the design tree and ask "why didn't they just stay on V1?":

- **V1 → V2 was deferred** because the failure modes V2 addresses (false-positive over-recall, low confidence, small-text legibility) all assume the V1 detector is *imperfect-but-working*. The observed V1 detector wasn't working — it was producing malformed JSON 80% of the time. V2's reviewer loop and tiled detection compound an unreliable signal; they don't fix it.

- **V1 → V3 was the correct pivot** because V3 removes the failure mode entirely. Instead of asking the VLM "where are the ducts?" (which it gets wrong), the user tells the system "ducts are this color" (which is fast and reliable). The downstream pipeline then runs on a deterministic mask. Trade-off: one user click per duct system instead of zero clicks. Win: the pipeline produces real results on drawings 01–03 today.

- **V3 isn't the final form** because it doesn't yet handle parallel-wall ducts (drawings 04–05) and it requires a manual color pick. The roadmap above closes both gaps without breaking V3's core insight (deterministic pipeline downstream of a reliable signal). The custom detection model in §5.3 replaces the manual pick; OCR-anchored Pattern A in §5.1 closes the parallel-wall gap.

- **The user-research interviews in [`synthetic-user-research/`](./synthetic-user-research/) drove the on-prem constraint** that rules out frontier VLMs in V1's primary path. Without that constraint, V1 + Anthropic vision is shippable as-is. With it, V3 is the only viable path today.

---

## 7. Pointers

- **What runs today:** [`implementation/README.md`](./implementation/README.md) — operational details, API, run instructions.
- **V3 architecture:** [`SOLUTION-DESIGN-V3.md`](./SOLUTION-DESIGN-V3.md) — every stage, every regex, every threshold + the rationale.
- **Architectural decisions:** [`adr/`](./adr/) — chronological design decisions, including the V3 pivot rationale (ADR-0011, ADR-0012, ADR-0013 added with this round of docs).
- **Why V1 + V2:** [`SOLUTION-DESIGN.md`](./SOLUTION-DESIGN.md), [`SOLUTION-DESIGN-V2.md`](./SOLUTION-DESIGN-V2.md) — kept as design history, not implementation reference.
- **Sample drawings:** [`sample-HVAC/`](./sample-HVAC/) — 5 benchmark drawings spanning the conventions discussed throughout this README.
- **User research:** [`synthetic-user-research/`](./synthetic-user-research/) — the interviews that drove the on-prem requirement and the wedge framing.
- **Competitor scan:** [`competitor-research.md`](./competitor-research.md) — what exists in this space and where this product slots.
