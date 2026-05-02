# Competitor Research

## TaksoAI

AI-first takeoff platform for mechanical, plumbing, and HVAC contractors. Positions itself as a fast replacement for manual on-screen takeoff — most plans process in under 15 minutes.

### Core technology
- Patented HVAC AI with segmentation-based detection ("Next-Gen Pipe & Duct Algorithm V2.0")
- Segments duct runs, branches, sizes, and components
- Identifies 38+ pipe/plumbing fittings
- Auto-measures lengths, sizes, and systems for each duct run
- Supports commercial HVAC: rectangular and round ductwork, flex duct, fittings, multi-zone

### Workflow
1. User uploads a PDF plan
2. User provides key info and equipment names to count
3. Algorithm scans the drawing, produces an annotated takeoff
4. Estimator reviews on-screen — confirms AI-detected elements, adjusts as needed
5. Structured quantities export into estimation/pricing tools

Tag search: type any equipment, fixture, or device tag → every instance is located across the plan set.

### Pricing
Not published. Sales-led, contact required.

## How it maps to the Techjay brief

| Requirement | TaksoAI |
|---|---|
| Upload PDF/image drawing | Yes |
| Detect ducts | Yes (segmentation AI) |
| Annotate detected ducts | Yes (on-screen overlay) |
| Extract dimensions (e.g. 14"⌀, 10"x8") | Yes (length, size, system) |
| Identify pressure class (LP/MP/HP) | **Not surfaced** |
| Click-to-inspect interactive UI | Partial (on-screen review/edit) |

## Gap and opportunity

Pressure class is not a publicly claimed output of TaksoAI — or of the broader AI takeoff market. It's the part of the Techjay brief with the weakest commodity solution. Treating pressure class as a first-class extracted attribute — inferred from explicit "LP/MP/HP" annotations, derived from velocity/CFM, or read from the schedule/legend — is the most differentiated capability the assessment is asking for.

## Sources
- https://www.taksoai.com/estimating-software/hvac
- https://www.taksoai.com/estimating-software/mechanical/
- https://www.taksoai.com
- https://www.taksoai.com/estimating-software/subcontractor/
- https://www.taksoai.com/why-traditional-hvac-takeoffs-are-broken/
