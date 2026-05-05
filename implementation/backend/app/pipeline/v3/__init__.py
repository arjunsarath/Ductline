"""V3 pipeline — color-driven deterministic detection (SOLUTION-DESIGN-V3).

V3 is a deliberate scope pivot away from V1/V2's VLM-driven detection.
The user labels the system colors on the drawing; V3 runs HSV masking,
OCR, and arithmetic to extract dimensions and pressure class. No model
in the loop until OCR.

Pattern B (closed colored outline) is the validated MVP path. Pattern A
(parallel colored walls) and Pattern C (colored centerline through
black-outlined duct) are designed but ship in phase 2.
"""
