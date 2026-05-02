You are reading an HVAC mechanical drawing — a 2D plan view that may include duct work, piping, structural grids, and electrical line work.

Identify **only HVAC duct segments**. Ignore piping, structural grids, electrical, and annotations.

A duct segment is a straight run of duct between two fittings (or between a fitting and a terminal device). Long runs broken by elbows, tees, or transitions count as multiple segments — break at every fitting.

For each duct segment you identify, output a JSON object with these fields:

- `bbox`: `[x_min, y_min, x_max, y_max]` in **normalized [0, 1]** coordinates. `0,0` is the top-left of the image, `1,1` is the bottom-right.
- `shape_hint`: one of `"round"`, `"rectangular"`, or `"unknown"`. Use `"round"` when you see a circle/oval cross-section indicator (`⌀`, `Ø`, `DIA`); `"rectangular"` when you see a rectangle indicator or `W x H` callout; `"unknown"` if you cannot tell.
- `nearby_text`: a list of any text strings you can read within roughly 30 pixels of the segment (dimensions like `14"⌀`, system tags like `SA-1`, or pressure-class labels like `LOW PRESS`). Empty list if there is nothing.

Return a single JSON object with this exact shape — no explanation, no surrounding text:

```
{
  "segments": [
    {
      "bbox": [0.12, 0.34, 0.45, 0.38],
      "shape_hint": "rectangular",
      "nearby_text": ["14\" x 8\"", "SA-1"]
    }
  ]
}
```

If you see no duct segments, return `{"segments": []}`.
