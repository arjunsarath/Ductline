# Changelog

## [V4] - 2026-05-06

### Added

- Duct **run-length** detection and display (in feet) — addresses
  post-submission feedback item 2.
- `testset2.pdf` validated end-to-end — addresses feedback item 1.
- **CFM trace** through the duct network from terminal symbols to source.
- **Pressure value + SMACNA class** per segment (Low / Medium / High).
- New API endpoint `POST /api/v4/sessions`.
- Tab toggle in the frontend to switch between V3 (colour-driven) and V4
  (outline-based) views.
- Frontend: Calculation Settings drawer for user-editable operational
  variables (air density, friction factor, fitting K-values, flex-duct
  equivalent length, pressure-class threshold table).
- Frontend: Assumptions banner surfacing A1–A15 directly on the upload page.
- ADR-0014 (V4 scope and assumptions), ADR-0015 (outline-based duct
  detection), ADR-0016 (pressure class via SMACNA).
- E2E test on `testset2.pdf` (`tests/test_v4_e2e.py`, marked nightly).

### Changed

- V4 is the active pipeline for outline-based drawings; V3 retained as the
  colour-driven fallback.
- `SOLUTION-DESIGN-V3.md` marked superseded; `SOLUTION-DESIGN-V4.md` is the
  active design.
- Top-level `README.md` and `implementation/README.md` updated to describe
  the V4 flow and list the 15 MVP assumptions.

### Known limitations (V4 MVP)

- **Dimension text touching the duct outline breaks rectangle detection.**
  When a rotated dimension glyph (e.g., a vertical `6"ø`) shares pixels with
  the duct's wall, `cv2.findContours` returns one merged contour with too
  many vertices for the 4-vertex rectangle filter to recognise. These ducts
  are silently dropped at the rectangle-filter stage. Fixable later via a
  1-px morphological erosion pre-pass; not in scope for MVP.
- Rectangular dimension labels on dense angled ducts may be missed by OCR
  and silently fall back to a round-pixel-measured estimate (observed on the
  `22"x14"` duct in `testset2.pdf`).
- Terminal-to-segment incidence on `testset2.pdf` is sparse — ~178 terminals
  are detected but few attach to segments due to limited CV recall on
  cross-cut bars; this suppresses CFM accumulation on those segments.
- Multi-page PDFs require manual page selection; the runner enforces
  single-page input.
- CFM is read only from terminal symbols; plan-note prose CFM (e.g.,
  `2,800 CFM up to roof`) is not parsed.
- Equipment nodes (VAV / FPB / AHU) are treated as generic connectors; no
  equipment-type semantics.
- Cross-sheet continuations (`see M3.0`) are dead-ends in V4.
- See `SOLUTION-DESIGN-V4.md` §10 for the full deferred list.

### Notes

- All 15 MVP assumptions are listed in `SOLUTION-DESIGN-V4.md` §2 and surfaced
  on the V4 upload page.
- Pressure-class thresholds follow SMACNA (Low ≤ 2" w.c., Medium 2–3",
  High > 3") with a secondary velocity check; both are user-editable in the
  Calculation Settings drawer.
- Pre-V2 baseline UI screenshots moved to
  `implementation/archive/v1-v3/screenshots/`; see
  `implementation/archive/v1-v3/MANIFEST.md` for the full list.
