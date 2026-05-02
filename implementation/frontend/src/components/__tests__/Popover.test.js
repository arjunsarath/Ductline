import { jsx as _jsx } from "react/jsx-runtime";
/**
 * Popover ReviewerSection tests (V2 §5.7). Exercises:
 *   - no reviewer steps → no Reviewer section (today's-backend behaviour)
 *   - plausible verdict → green tone class
 *   - implausible verdict → red tone class
 *   - iteration > 1 → iter N text rendered
 *   - iteration === 1 → no iter text
 *   - existing Dimension + Pressure-class sections still render (sanity)
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Popover } from "../Popover";
import { makeResult, makeReviewerStep, makeSegment, } from "../../test/fixtures";
function renderPopover(segment = makeSegment()) {
    return render(_jsx(Popover, { segment: segment, anchor: { x: 100, y: 100 }, onClose: () => { } }));
}
describe("Popover ReviewerSection", () => {
    it("does not render the Reviewer section when no reviewer steps exist", () => {
        // Today's main backend doesn't emit reviewer steps — the popover should
        // look identical to v1.
        renderPopover();
        expect(screen.queryByText("Reviewer")).toBeNull();
    });
    it("uses the plausible tone when verdict is plausible", () => {
        const segment = makeSegment({
            review_verdict: "plausible",
            review_iterations: 1,
            reasoning_trace: [
                ...makeResult().segments[0].reasoning_trace,
                makeReviewerStep({
                    evidence: "geometry tracks centerline; legend supports system tag",
                    iteration: 1,
                }),
            ],
        });
        const { container } = renderPopover(segment);
        expect(screen.getByText("Reviewer")).toBeInTheDocument();
        expect(container.querySelector(".critique-plausible")).not.toBeNull();
    });
    it("uses the implausible tone when verdict is implausible", () => {
        const segment = makeSegment({
            review_verdict: "implausible",
            review_iterations: 2,
            reasoning_trace: [
                makeReviewerStep({
                    evidence: "callout shape (round) conflicts with rectangular geometry",
                    iteration: 1,
                }),
            ],
        });
        const { container } = renderPopover(segment);
        expect(container.querySelector(".critique-implausible")).not.toBeNull();
    });
    it("renders 'iter N' suffix when iteration > 1", () => {
        const segment = makeSegment({
            review_verdict: "uncertain",
            reasoning_trace: [
                makeReviewerStep({ iteration: 2, evidence: "second-pass geometry" }),
            ],
        });
        renderPopover(segment);
        expect(screen.getByText(/iter 2/)).toBeInTheDocument();
    });
    it("does not render an iter suffix when iteration === 1", () => {
        const segment = makeSegment({
            review_verdict: "plausible",
            reasoning_trace: [
                makeReviewerStep({ iteration: 1, evidence: "first-pass plausible" }),
            ],
        });
        renderPopover(segment);
        expect(screen.queryByText(/iter\s+\d+/)).toBeNull();
    });
    it("renders the existing Dimension and Pressure-class sections", () => {
        renderPopover();
        expect(screen.getByText("Dimension")).toBeInTheDocument();
        expect(screen.getByText("Pressure class")).toBeInTheDocument();
        expect(screen.getByText('12" x 8"')).toBeInTheDocument();
    });
    it("renders a critique row per reviewer step", () => {
        const segment = makeSegment({
            review_verdict: "uncertain",
            reasoning_trace: [
                makeReviewerStep({ stage: "reviewer_critique", iteration: 1, evidence: "first critique" }),
                makeReviewerStep({ stage: "reviewer_refine", iteration: 2, evidence: "refined geometry" }),
            ],
        });
        const { container } = renderPopover(segment);
        expect(container.querySelectorAll(".critique-row").length).toBe(2);
    });
});
