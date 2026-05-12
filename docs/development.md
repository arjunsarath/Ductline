# Development

## Prerequisites

| Tool | Version | Why |
| --- | --- | --- |
| Python | 3.11+ | `from __future__ import annotations` + PEP 604 union syntax |
| Node.js | 20+ | Next.js 16 / React 19 |
| Tesseract OCR | 5.x | Per-callout OCR in `/api/detect-scale` |

### Installing Tesseract

```bash
# macOS
brew install tesseract

# Debian/Ubuntu
sudo apt-get install tesseract-ocr

# Fedora
sudo dnf install tesseract
```

Verify: `tesseract --version` should print 5.x. `pytesseract` finds it by default on `$PATH`; if your install lives somewhere unusual, set `pytesseract.pytesseract.tesseract_cmd` near the top of `scale_detector.py`.

## First-time setup

### Backend

```bash
cd implementation/backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# requirements.txt does not currently pin PyMuPDF, but extractor.py and
# preprocess.py both `import fitz`. Install it manually:
pip install pymupdf
```

You should now be able to run:

```bash
uvicorn main:app --reload --port 8000
```

Health check from another terminal: `curl http://localhost:8000/api/health` → `{"ok":true}`.

### Frontend

```bash
cd implementation/frontend
npm install
npm run dev
```

This starts Next.js at http://localhost:3000. The frontend hot-reloads on save; the backend hot-reloads via `uvicorn --reload`.

## Daily workflow

Two terminals:

```bash
# Terminal 1
cd implementation/backend && source .venv/bin/activate && uvicorn main:app --reload --port 8000

# Terminal 2
cd implementation/frontend && npm run dev
```

Open http://localhost:3000, drag a PDF, draw a crop, click "Run extraction".

### Environment variables

Read by `src/app/page.tsx`:

| Name | Default | Used for |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000/api/extract` | Extract endpoint |
| `NEXT_PUBLIC_SCALE_API_URL` | `http://localhost:8000/api/detect-scale` | Scale-detection endpoint |

Set in `.env.local` if you point the frontend at a remote backend. Note that the backend CORS allowlist is `["http://localhost:3000"]` — change `allow_origins` in `main.py` to match your frontend origin.

## Troubleshooting

### `pytesseract.pytesseract.TesseractNotFoundError`

Tesseract isn't on `$PATH`. Install it (see above), or set the path explicitly:

```python
# scale_detector.py, near the top
import pytesseract
pytesseract.pytesseract.tesseract_cmd = "/opt/homebrew/bin/tesseract"
```

### `ModuleNotFoundError: No module named 'fitz'`

PyMuPDF is imported by `extractor.py` and `preprocess.py` but not pinned in `requirements.txt`. `pip install pymupdf` inside the venv.

### `Address already in use` (port 8000)

```bash
lsof -i :8000          # find the PID
kill -9 <pid>
# or run on a different port:
uvicorn main:app --reload --port 8001
```

Then update `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_SCALE_API_URL` to match.

### CORS error in the browser console

`main.py` allows only `http://localhost:3000`. If you serve the frontend on a different port or origin, add it to `allow_origins`.

### Chrome tab OOMs on a large PDF

The viewer caps the pdfjs raster width at `MAX_RASTER_WIDTH = 3000` CSS px and forces `devicePixelRatio = 1` (see `pdf-page.tsx`). If you're still crashing on extremely dense engineering plans:

- Check that the user isn't zooming to >300% on a >2.5K-wide page (the canvas becomes 8 MP+).
- Reduce `MAX_RASTER_WIDTH` in `viewer.tsx` to `2400`.
- Make sure you only have one page mounted at a time (the viewer uses `key={page_number-rasterWidth}` to force re-render — duplicated `PdfPage` mounts will stack memory).

### "Couldn't infer a drawing scale" error

The pipeline failed at the scale stage. Either `_filter_callout_candidate_boxes` returned zero callout-sized rectangles in the crop, OCR rejected all of them, or `_infer_geometry` couldn't find a duct rectangle for any successfully-OCR'd callout.

Diagnostics:
- Re-crop tightly around a known duct with a visible `NN"Ø` callout.
- Check the backend stdout — `[detect-scale]` log lines report each candidate box, OCR result, confidence, and reject reason.
- Common cause: the callout label sits in a region without a black-stroked enclosing box. Without a box `_filter_callout_candidate_boxes` has nothing to find.

### "No elements found in the selected regions"

`/api/extract` returned `pages: []`. The PDF has no vector content inside the crop (it's a scanned raster, or the crop hit empty white space).

### Pipeline progress jumps straight from "scale" to viewer without showing "cleanup"

It shouldn't — `runPipeline` has a deliberate `await new Promise(r => setTimeout(r, 350))` between scale-done and viewer transition specifically to make the "cleanup" row tick over visibly. If you don't see it, you're probably advancing past the pipeline screen by clicking too fast on a re-crop button.

### `dpi: 1200` in the scale response but the OCR is still wrong

Tesseract is sensitive to image preparation. `_strip_non_black` uses `black_threshold` (default `0.05`, frontend overrides to `0.02`) to threshold the rendered crop to pure black/white. If callouts come through faint (grey ink, screened fills), raise the threshold by tweaking the form value in `runPipeline` or the default in `main.py`.

The `_O_SLASH_SUBSTITUTES` set in `scale_detector.py` is `"@°ØøOoDd6¢"`. If Tesseract is reading a glyph as something not in that set (rare but possible — e.g. `*`, `o̸`), add it to the constant and the regex character class will pick it up.

## Adding a new filter rule

The filter chain runs in `viewer.tsx` inside `visibleElements` and `counts`. Each step short-circuits.

1. **Add the constant** to `lib/extract.ts`:

   ```ts
   export const MAX_DUCT_PERIMETER_IN = 320; // 8 ft × 8 ft
   ```

2. **Add the helper** alongside the existing `passes*` functions:

   ```ts
   export function passesMaxPerimeterInches(
     el: Element,
     ptsPerInch: number,
     maxIn: number = MAX_DUCT_PERIMETER_IN,
   ): boolean {
     if (el.type !== "rect" && el.type !== "rect_curve") return true;
     const sides = rectSideLengthsPts(el);
     if (!sides) return false;
     const perimIn = (2 * (sides.w + sides.h)) / ptsPerInch;
     return perimIn <= maxIn;
   }
   ```

   Conventions:
   - Pass through non-rectangle types unchanged (`return true`) — the type filter handles them earlier.
   - Use `rectSideLengthsPts` so rotated `rect_curve` is measured correctly.
   - Accept the threshold as a defaulted parameter so callers can override per-rule.

3. **Wire it into the viewer**. In `viewer.tsx`, both `counts` and `visibleElements` need the rule. Place it after the existing `passesMaxAreaInches` call inside the `if (sp != null && ...)` block — that's the conditional that runs only when a scale is known:

   ```ts
   if (sp != null && (
     !passesMinSideInches(el, sp)
     || !passesMaxAreaInches(el, sp)
     || !passesMaxPerimeterInches(el, sp)
   )) {
     continue;
   }
   ```

4. **Decide whether the user can see/disable it**. The current filters are not user-toggleable — they're applied unconditionally. If the new rule needs a checkbox, add it to `FiltersPane` and thread the boolean through to the memos. If not, no UI work needed.

5. **Test against a known-good PDF**. The element count in the filter pane is what the user sees; verify the count drops by roughly the number of items you expect, not zero (over-strict) or unchanged (no effect).

## Adding a new element type

Lower-frequency change but the steps are mechanical.

1. **Backend** — add a new branch in `_extract_page` in `extractor.py` that emits an element with a unique `id` prefix and a new `type` string. Add the type to `Literal[...]` on the `Element` Pydantic model in `main.py`.

2. **Frontend types** — add a new variant to the `Element` discriminated union in `lib/extract.ts`. Add an entry to `TYPE_COLORS` and `TYPE_LABELS`. Add the type to `ELEMENT_TYPES` if it should appear in the filter pane.

3. **Frontend rendering** — add a branch in `ElementOverlay`'s `for (const el of elements)` loop. Add a branch in `MeasurementsOverlay.MeasurementItem` (or update `rectSideLengthsPts`) if the new type is rectangle-like and should be measured.

4. **Frontend filters** — if the new type should pass through `passesRectAspect` / `passesMinSideInches` / `passesMaxAreaInches`, extend the `el.type !== "rect" && el.type !== "rect_curve"` checks. If not, the default `return true` handles it.

## Layout of the project

```
implementation/
  backend/        Python FastAPI service
  frontend/       Next.js client

docs/
  architecture.md
  backend.md
  frontend.md
  development.md  (this file)
```

There is no monorepo manager — backend and frontend are independent. Each has its own dependency manifest (`requirements.txt`, `package.json`) and its own dev server.

## Tests

There are no tests in the repo currently. If you add some:

- **Backend** — `pytest` is conventional. `pdfplumber` exposes pages from in-memory bytes (`pdfplumber.open(io.BytesIO(data))`), so unit tests can ship small reference PDFs in `implementation/backend/tests/fixtures/`.
- **Frontend** — `vitest` or `playwright`. The pipeline-orchestration logic in `page.tsx` and the filter helpers in `lib/extract.ts` are the highest-value targets.
