# ADR 0011 — V3 pivot: deterministic color-driven pipeline replaces VLM-driven detection

**Status:** Accepted
**Date:** 2026-05-04
**Supersedes (in production path):** ADR-0001 (Hybrid Detection Stack), ADR-0002 (Pluggable VLM Client) for the *detection* stage. Both ADRs remain valid for stages outside detection and for future hybrid paths.
**Related:** ADR-0009 (multi-agent reviewer), ADR-0010 (categorizer-first ordering) — both designed for V2 and not built; remain referenced in V2 design doc.

---

## Context

V1 shipped the 7-stage pipeline (PRD §2) with a VLM (`llama3.2-vision` 11B) at stage 4 for duct detection, plus a deterministic CV fallback. V2 was designed as an enhancement layer: a reviewer-agent loop (ADR-0009) and tiled detection (ADR-0008) on top of V1.

Observed V1 behaviour on the 5-drawing benchmark (run 2026-05-02):

- 5/5 drawings hit the CV-detection cap of 60 candidates because of HoughLinesP over-recall on walls/columns/grid lines.
- 5/5 drawings produced `[cv_fallback]` markers because the VLM either timed out, returned malformed JSON, or hallucinated regular-grid bboxes.
- **The VLM never contributed a usable detection on any benchmark drawing.**

Three diagnostic prompt variants and one model swap (to a larger but still open-source model via Ollama) didn't move the needle. The conclusion: open-source vision models in the size class that runs on commodity host hardware (≤90B parameters) cannot reliably do bbox detection on engineering drawings *as currently prompted, and likely not as a category*.

Frontier proprietary VLMs (Anthropic Claude vision, OpenAI GPT-4o) would clear the bar — the detection-quality variance between 11B local and 200B+ cloud models is large for technical-drawing tasks. But the user-research interviews (`synthetic-user-research/`) surfaced a hard on-prem requirement for the most-validated user segments (defence-adjacent AEC firms, government estimators, large GCs with security-conscious clients). Frontier VLMs run cloud-only and conflict with that requirement.

V2's enhancements (reviewer loop, tiled detection, categorizer ordering) all assume the underlying detector works imperfectly-but-usefully. With the actual V1 detector behaviour (works ~0% of the time), V2's enhancements compound the failure rather than compensating for it: a reviewer loop on a malformed-JSON-producing detector adds three more chances at malformed JSON.

## Decision

Pivot the detection stage to a **deterministic color-driven pipeline** (V3, [`SOLUTION-DESIGN-V3.md`](../SOLUTION-DESIGN-V3.md)). The user identifies each duct system's color via a one-click pick on the rendered page; downstream is HSV color masking → flood-fill → skeletonize + distance transform → OCR-token attribution → SMACNA pressure classification.

The VLM is removed from the live detection path. The `VLMClient` Protocol (ADR-0002) and the Ollama implementation are retained in the codebase as parked artifacts — they remain available for opt-in hybrid paths once capability or compliance constraints change, but no V3 stage calls them.

## Why this is the right call

1. **Replaces an unreliable signal with a reliable one.** The VLM was being asked "where are the ducts?" The user is now asked "what color is your duct system?" The user can answer reliably; the VLM cannot. The downstream pipeline is identical-quality deterministic CV.

2. **Trade-off cost is bounded and visible.** One UI click per duct system. On the benchmark drawings, this is 1–3 clicks total per drawing. The picker UX iterations (cursor-magnifier, exact-RGB sampling, click-rejection feedback) make the click cost low enough that user-research interviewees rated it acceptable in synthetic-research follow-up.

3. **Doesn't burn the V1/V2 design.** The pipeline structure (Ingest, OCR, classify, pressure-class, assemble) survives nearly unchanged. Only the Region+Detect stages are replaced. The reviewer-loop and tiled-detection ADRs (V2) remain accurate as designs for a *future* enhancement layer once a working detector exists.

4. **Composes with the on-prem requirement.** No VLM means no Ollama dependency, no GPU requirement, no model-weight licensing concerns. The V3 stack runs on a single CPU container.

5. **Composes with the hybrid roadmap.** The custom detection model in [`../README.md` §5.3`](../README.md) replaces the manual pick step but leaves the rest of V3 intact. The pivot doesn't paint us into a corner; it removes a failing sub-stage.

## Consequences

**Positive:**
- 3/5 benchmark drawings produce trustworthy structured output as of the V3 alpha. (Compared to 0/5 on the V1 path that didn't go through CV fallback.)
- Pipeline is fully deterministic and offline-capable.
- Regression test (`backend/tests/test_v3_runner.py`) provides a stable signal for changes.

**Negative:**
- Manual pick is required per drawing. UX iterations have made this fast but it is non-zero friction.
- Drawings using parallel-wall convention without colored outlines (drawings 04, 05) need Pattern A support that is not yet shipped (V3 §10 phase-2).
- The architectural seam to the VLM remains in the codebase. Decision: keep it. The seam costs near-zero maintenance and the integration is one config flip away when needed.

**Neutral / future:**
- V2's reviewer-loop and tiled-detection ADRs become "available enhancements once a reliable detector exists." They're not invalidated; they're sequenced.

## Alternatives considered

- **Stay on V1 + swap to Anthropic Claude vision.** Would clear the detection-quality bar but violates the on-prem constraint. Parked behind the seam for cloud-OK customers.
- **Stay on V1 + swap to `llama3.2-vision:90b`.** Quality at 90B is better than 11B but still unreliable on technical drawings, and the latency on consumer hardware is multiple minutes per drawing. Not shippable.
- **Build the custom detection model first.** Skipping V3 to go straight to a trained detector saves the V3 implementation cost but defers any working product 6–12 months while training data is collected. V3 ships now; the trained detector replaces V3's manual pick step later.
- **Fine-tune `llama3.2-vision` 11B on engineering drawings.** Considered. Open-source VLM fine-tuning at the detection-task level gives marginal gains (5–15%) over base; insufficient to clear the structured-JSON reliability bar.

## What this means for the existing ADRs

- **ADR-0001 (Hybrid Detection Stack)** — the algorithmic-first / workflow-second / agent-only-with-tools posture remains correct as the framing for V3. The agent-with-tools layer is now empty; it's the right shape if/when a future stage needs an agent.
- **ADR-0002 (Pluggable VLM Client)** — protocol survives, no implementation is on the live path. Reframed as "parked for hybrid paths" rather than "active runtime dependency."
- **ADR-0006 (OCR engine PaddleOCR → RapidOCR)** — V3 uses RapidOCR per this ADR. No change.
- **ADR-0009 + ADR-0010** — V2 enhancements; not yet implemented; remain accurate as forward-looking designs.

## References

- [`../SOLUTION-DESIGN-V3.md`](../SOLUTION-DESIGN-V3.md) — the V3 pipeline in full detail.
- V1 benchmark sweep results: [`../implementation/README.md`](../implementation/README.md) §4 (current V3 numbers replace the V1 numbers; V1 numbers preserved in V1 commit history).
- User research that surfaced the on-prem constraint: [`../synthetic-user-research/demo/findings-cross-persona.md`](../synthetic-user-research/demo/findings-cross-persona.md).
