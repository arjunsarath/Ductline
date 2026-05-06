# Archive — V1/V2 legacy artifacts

This directory holds artifacts from the V1 (hybrid VLM) and V2 (reviewer loop)
phases that are no longer referenced by the V3 (color-driven) or V4
(outline-based) live paths. Items are kept for retrospective and design history;
nothing here is loaded by the running backend or frontend.

V3 source code remains in place under `implementation/backend/app/` and
`implementation/frontend/src/components/` because V3 is still the colour-driven
fallback path per `SOLUTION-DESIGN-V4.md` §1, and several V1 frontend
components are still exercised by Vitest tests under
`frontend/src/components/__tests__/`. Those components stay in tree so the
test suite remains green.

## Moved files

| Path | Origin | Rationale |
|------|--------|-----------|
| `screenshots/01 · Upload@2x.png` | Pre-V2 baseline UI mock | Not referenced anywhere; superseded by `screenshots/v3_ui_*.png` |
| `screenshots/02 · Processing@2x.png` | Pre-V2 baseline UI mock | Same |
| `screenshots/03 · Result@2x.png` | Pre-V2 baseline UI mock | Same |

## Items considered and kept in tree

- `implementation/backend/app/pipeline/{detect,detect_tiled,review,categorize,
  classify,extract,assemble,layout,legend,quality,regions,orientation}.py` —
  V1/V2 modules wired through the parked `/agent/*` router in `app/main.py`.
  Still importable; not on the V3/V4 live path. Removing them would require
  unmounting the agent router, which is an explicit architectural decision
  rather than cleanup.
- `implementation/backend/app/vlm/` — VLMClient seam preserved per
  ADR-0011 for a future hybrid path. No current importers outside the
  parked agent pipeline.
- `implementation/frontend/src/components/{UploadView,ProcessingView,
  ResultView,ApprovalPanel,TilePreview,Stepper,Sidebar,Brand,Popover,
  SegmentMark,RasterCanvas,PdfCanvas,Viewer,QualityBanner,CanvasControls}.tsx`
  — V1 components, no longer rendered by `App.tsx`, but still imported by
  tests under `components/__tests__/`. Moving them would require deleting
  the matching tests.
- `implementation/frontend/src/api/client.ts` — V1 client; same situation.
- `SOLUTION-DESIGN.md`, `SOLUTION-DESIGN-V2.md`, `SOLUTION-DESIGN-V3.md` —
  retained at the project root as design history, linked from `README.md`.
- `implementation/docker-compose.yml` and per-service Dockerfiles — written
  for V1, marked parked in `implementation/README.md` §9, not validated
  since the V3 pivot. Kept because the README explicitly documents how to
  revive them.
