/**
 * Fixture builders for unit tests. Centralised so changes to the
 * DrawingResult schema don't require touching every test file.
 */
export function makeSegment(overrides = {}) {
    return {
        id: "S-001",
        geometry: {
            type: "polyline",
            points: [
                [10, 10],
                [100, 10],
                [100, 50],
            ],
        },
        dimension: {
            value: '12" x 8"',
            shape: "rectangular",
            confidence: "high",
            source: "ocr:near_segment(d=12px)",
        },
        pressure_class: {
            value: "LOW",
            confidence: "high",
            source: "schedule:DUCT-SCHED-2/row-A",
            alternatives: [],
        },
        reasoning_trace: [
            { stage: "vlm_detect", evidence: "rectangular duct on plan view" },
            { stage: "ocr_callout", evidence: "12 x 8" },
            { stage: "schedule_lookup", evidence: "row A → LOW" },
        ],
        ...overrides,
    };
}
export function makeReviewerStep(overrides = {}) {
    return {
        stage: "reviewer_critique",
        evidence: "geometry tracks duct centerline; callout consistent",
        iteration: 1,
        ...overrides,
    };
}
export function makeResult(overrides = {}) {
    return {
        drawing_id: "drw-test",
        width_px: 1200,
        height_px: 900,
        display_image_data_url: "data:image/png;base64,iVBORw0KGgo=",
        quality: {
            overall: "high",
            blur_score: 0.95,
            skew_degrees: 0.1,
            ocr_confidence_avg: 0.92,
            warnings: [],
        },
        segments: [makeSegment()],
        aggregate: {
            total: 1,
            by_pressure_class: { LOW: 1, MEDIUM: 0, HIGH: 0 },
            by_confidence: { high: 1, medium: 0, low: 0 },
        },
        errors: [],
        coord_space: "pixels",
        ...overrides,
    };
}
export const VERDICTS = [
    "plausible",
    "implausible",
    "uncertain",
    "not_reviewed",
];
