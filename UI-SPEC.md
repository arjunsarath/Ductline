# UI Design — HVAC Duct Detection (v1)

**Author:** Arjun · **2026-05-02** · Anchored to [`SOLUTION-DESIGN.md`](./SOLUTION-DESIGN.md) §7 and [`findings-cross-persona.md`](./synthetic-user-research/demo/findings-cross-persona.md) §5.

## Design thesis

**Auditor, not oracle.** Every extracted value cites the evidence that produced it. A confidence pill alone is a number to ignore — a pill plus `↳ schedule:DUCT-SCHED-2/B4` is a place to look. This is the only UI decision that matters; everything below follows from it.

## Three views, one route

`Upload` → `Processing` → `Result`. No router, no navigation chrome.

## Result — the only screen worth designing

```
┌─ Quality banner (only if quality != high) ───────────────────────────┐
├─ Toolbar  ← New drawing · file.pdf · [− 100% +] [Reset] [G] [▸] ─────┤
│                                                       │              │
│  Drawing viewer                                       │  Sidebar     │
│   • canvas (auto-grayscale on colored sources)        │   list       │
│   • SVG overlay, PC-keyed strokes                     │   stats      │
│   • click → popover anchored at cursor                │   legend     │
│                                                       │              │
└───────────────────────────────────────────────────────┴──────────────┘
```

**Color = pressure class. Stroke style = confidence.** Solid + green/orange/red for high-confidence LOW/MED/HIGH. Dashed for low-confidence. Color alone never carries meaning — text label and stroke pattern back it up.

## The popover (load-bearing)

```
14"⌀                              [Conf: high]
↳ ocr:near_segment   "14"⌀" found 8 px below segment midpoint

Pressure class: LOW               [Conf: medium]
↳ schedule:DUCT-SCHED-2/B4   "SA-1 system, low pressure"
```

Reasoning trace is the design. Every `↳` line names the stage and the evidence. Missing values render as `—` with a trace that says why (`no callout text within 30 px radius`) — never silently omitted.

## Sidebar

Sortable list (ID / dimension / PC / confidence). Each row: `D-07 · 14"⌀ · LOW · [high]` plus the top reasoning step truncated. Click pans the canvas. Aggregate stats card below: total, by-PC, by-confidence, quality verdict.

## Visual

- Inter for UI, JetBrains Mono for dimensions and timer
- 8-px grid, 6 / 8 / 12 px radii (pill / card / popover)
- Pressure class: `#059669` LOW · `#EA580C` MED · `#DC2626` HIGH
- Confidence pill: green / amber / red on white text

## Interactions

- Click empty space clears selection
- Wheel zooms toward cursor (25–400%); drag pans; spacebar-pan from anywhere
- Tab cycles segments, ←/→ prev/next, Esc closes popover, `g` grayscale, `s` sidebar
- Hover widens stroke and tints fill

## States that exist

| State | Treatment |
|---|---|
| Quality medium / low | Amber / red banner with warnings |
| Zero detections | "No ducts detected" empty state in viewer |
| VLM degraded | Banner: "geometry-only mode" |
| Selected segment | 5-px stroke + 4-px outer ring |

## Accessibility

WCAG 2.1 AA. Color never alone — label + stroke pattern. Full keyboard path. `prefers-reduced-motion` honored. Targets ≥ 24 px.

## Open until first drawing

- Popover anchor: cursor or segment centroid
- Sidebar default sort: ID or confidence-ascending
- Reasoning-trace verbosity: full or top-two-with-expand

---

*Locked against SOLUTION-DESIGN §5.3 prop names. Drift requires updating both.*
