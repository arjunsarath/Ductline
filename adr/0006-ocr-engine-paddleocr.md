# ADR-0006 — OCR engine: PaddleOCR for engineering drawings

**Status:** Accepted, 2026-05-02
**Deciders:** Arjun Sarath
**References:** SOLUTION-DESIGN §4 (stage 5), ADR-0001 (hybrid stack)

## Context

Stage 5 of the pipeline reads text from a rasterized engineering drawing. Two distinct text surfaces exist on these drawings:

1. **Dimension callouts and pressure-class annotations** scattered near duct segments — small (often 8 pt at native scale), sometimes rotated along the duct, frequently in CAD-specific fonts (Romans, ISOCPEUR, SimplexRomanS).
2. **Title-block and schedule regions** — tabular content with column headers (`SYSTEM`, `SIZE`, `PRESS. CLASS`) and per-row values keyed by system tag.

The OCR engine has to handle both. Failure on either path silently breaks the pressure-class state machine: tier 1 (explicit annotation) needs accurate small-text reads; tier 2 (schedule lookup) needs accurate table extraction.

The OCR choice is also a load-bearing decision behind the algorithmic-first posture: the better the OCR, the less work the VLM has to do at stage 4 and the more determinism there is in the output.

## Decision

PaddleOCR (`paddleocr` Python package, default English model with `use_angle_cls=True`).

Used in two modes:

- **Plain OCR mode** for per-segment neighborhoods and standalone callouts. Returns `(bbox, text, confidence)` per detection.
- **PP-Structure table mode** for the schedule region only. Returns row-and-column-aware structured output.

Wrapped behind an `OCRExtractor` interface so the implementation can be swapped without touching the pipeline.

```python
class OCRExtractor(Protocol):
    def extract_text(self, image: PILImage, region: Bbox | None = None) -> list[OCRMatch]: ...
    def extract_table(self, image: PILImage, region: Bbox) -> Table: ...
```

## Why PaddleOCR

- **Built-in angle classifier** — handles text rotated along ducts without additional preprocessing. Tesseract requires manual deskew per region.
- **PP-Structure table mode** — purpose-built for the kind of tabular layout schedules use. Tesseract has no equivalent; you build it manually with column-detection heuristics.
- **Confidence scores per detection** — feeds directly into stage 2 (quality check) as the "OCR confidence average" input. No engineering work to surface.
- **Modern transformer-based recognition** — better than Tesseract on engineering typography out of the box.
- **Multilingual** — drawing #1 (AfDB) is from an African Development Bank project; drawing #5 (ASC 2018) is North American but the model handles both without reconfiguration.

## Consequences

**Positive**
- Strong recall on engineering callouts and tables without per-drawing tuning.
- Schedule extraction is a one-call operation, not a custom CV+OCR pipeline.
- The confidence stream feeds quality detection (stage 2) for free.
- Wrapped behind `OCRExtractor` so a swap stays surgical.

**Negative**
- ~500 MB model download on first run; cached in a Docker volume to avoid re-downloads. Adds ~3 minutes to first-time `docker compose up`.
- Heavier than Tesseract on cold start (~2 s init vs. ~0.5 s).
- Active project but smaller community than Tesseract — fewer Stack Overflow hits when something goes weird.

## Alternatives considered

1. **Tesseract** — industry standard, lightweight, free, no setup. Rejected: weak on rotated text, no native table mode, weaker on engineering typography. Tesseract remains a viable v1.1 fallback if PaddleOCR is too heavy for a target deployment.

2. **RapidOCR** — ONNX-converted PaddleOCR for speed (~3-5× faster). Newer project, fewer features. Rejected for v1: lacks PP-Structure equivalent. Documented as a v1.1 substitute if pipeline latency exceeds the 30 s P50 target.

3. **TrOCR (Microsoft)** — transformer-based, very high accuracy on dense text. Rejected: 1+ GB model, ~10× slower per call, exceeds the latency budget. Better suited to offline batch processing than an interactive demo.

4. **Cloud OCR (AWS Textract / Google Document AI)** — best-in-class table extraction, especially for schedules. Rejected: external network dependency, per-page cost, and adds an account-and-key story to the README. Wrong shape for a take-home.

5. **EasyOCR** — comparable to PaddleOCR on plain text, weaker on tables. Rejected for the same table-mode reason as RapidOCR.

## Open question for build

Whether PP-Structure's column inference works on the schedule formats in our 5 benchmark drawings. If column headers are non-standard (e.g., `P.C.` instead of `PRESSURE CLASS`), we'll need a small alias dictionary in `OCRExtractor.extract_table`. Time-boxed to 30 minutes during day 1.
