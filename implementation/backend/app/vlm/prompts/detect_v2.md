You are looking at an HVAC mechanical engineering drawing. The image may contain duct work, piping, structural grids, and electrical line work.

Your job: identify every HVAC **duct segment** visible in the image. Ignore piping, structural grids, and electrical.

A duct segment is a straight run of duct between two fittings (or between a fitting and a terminal device). Long runs broken by elbows, tees, or transitions count as multiple segments — break at every fitting.

**Step 1.** Before answering, count the duct segments you can see in the image. Most plan-view HVAC drawings have between 5 and 50 segments. If you only see one segment, look again — there are almost always more.

**Step 2.** For each segment, return a JSON object with these fields. Use the actual coordinates and text from the image — do not invent numbers and do not copy any example values.

Fields per segment:
- `bbox`: a 4-element array `[x_min, y_min, x_max, y_max]` in **normalized coordinates** where the top-left of the image is `0, 0` and the bottom-right is `1, 1`. The bbox should tightly enclose just that duct segment.
- `shape_hint`: `"round"` if you see a diameter symbol (`⌀`, `Ø`, or text like `DIA`); `"rectangular"` if you see a width-by-height callout (e.g. `12x8`); `"unknown"` otherwise.
- `nearby_text`: a list of any text strings within ~30 pixels of the segment, exactly as written in the image. Empty list if none.

**Output format.** Return only this JSON object — no prose, no markdown fence, no explanation:

```
{"segments": [ ... ]}
```

If you genuinely see no ducts, return `{"segments": []}`.
