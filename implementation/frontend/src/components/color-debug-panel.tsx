"use client";

// TEMPORARY debug panel for tuning the "what is black?" colour threshold used
// by scale detection. Remove the whole file once the threshold is locked in.

import { useMemo } from "react";
import { Bug, Check, Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  colorSummary,
  hexLuma,
  type Element,
} from "@/lib/extract";

type Props = {
  elements: Element[];
  threshold: number;
  setThreshold: (v: number) => void;
  highlightedColor: string | null;
  setHighlightedColor: (c: string | null) => void;
  onProceed: () => void;
};

export default function ColorDebugPanel({
  elements,
  threshold,
  setThreshold,
  highlightedColor,
  setHighlightedColor,
  onProceed,
}: Props) {
  const summary = useMemo(() => colorSummary(elements), [elements]);

  const totalWithColor = useMemo(
    () => summary.reduce((acc, s) => acc + s.count, 0),
    [summary],
  );

  return (
    <aside
      data-role="no-pan"
      className="flex w-[320px] shrink-0 flex-col border-l border-amber-400/60 bg-amber-50/50 dark:bg-amber-950/20"
    >
      <div className="flex items-center justify-between border-b border-amber-400/60 px-3 py-2.5">
        <div className="flex items-center gap-2">
          <Bug className="size-4 text-amber-600 dark:text-amber-400" />
          <p className="text-[12px] font-semibold uppercase tracking-[0.14em] text-amber-700 dark:text-amber-300">
            Colour debug
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={onProceed} aria-label="Proceed">
          <Check />
          Proceed
        </Button>
      </div>

      <div className="space-y-3 border-b border-amber-400/40 px-3 py-3">
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-muted-foreground">Black threshold (max channel)</span>
            <span className="font-mono tabular-nums">{threshold.toFixed(2)}</span>
          </div>
          <input
            type="range"
            min={0}
            max={0.5}
            step={0.01}
            value={threshold}
            onChange={(e) => setThreshold(parseFloat(e.target.value))}
            className="w-full accent-amber-500"
          />
          <Input
            type="number"
            min={0}
            max={1}
            step={0.01}
            value={threshold}
            onChange={(e) => {
              const v = parseFloat(e.target.value);
              if (!Number.isNaN(v)) setThreshold(Math.max(0, Math.min(1, v)));
            }}
            className="h-7 text-[12px]"
          />
        </div>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          Colours with max channel ≤ threshold count as ink. While this panel
          is open the live overlay re-filters as you drag — what you see is
          exactly what the backend would feed the rectangle-containment and
          wall-pair checks on the next detect-scale call. Click a swatch to
          isolate that one colour.
        </p>
      </div>

      <div className="flex-1 overflow-auto px-2 py-2">
        <p className="px-1 pb-2 text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
          {summary.length} distinct · {totalWithColor} coloured elements
        </p>
        <ul className="space-y-1">
          {summary.map(({ color, count, byType }) => {
            const luma = hexLuma(color);
            const passesAsInk = luma <= threshold;
            const isHighlighted = highlightedColor === color;
            return (
              <li key={color}>
                <button
                  type="button"
                  onClick={() =>
                    setHighlightedColor(isHighlighted ? null : color)
                  }
                  className={cn(
                    "group flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left transition-colors",
                    isHighlighted
                      ? "border-amber-500 bg-amber-100/70 dark:bg-amber-900/40"
                      : "border-transparent hover:bg-muted/60",
                  )}
                >
                  <span
                    className="inline-block size-4 rounded-[3px] border border-border shadow-inner"
                    style={{ background: color }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <p className="truncate font-mono text-[11px]">{color}</p>
                      <p className="font-mono text-[10px] tabular-nums text-muted-foreground">
                        {count}
                      </p>
                    </div>
                    <p className="text-[10px] text-muted-foreground">
                      {Object.entries(byType)
                        .map(([t, c]) => `${t}:${c}`)
                        .join(" · ")}
                    </p>
                  </div>
                  <span
                    className={cn(
                      "rounded-full px-1.5 py-0.5 text-[10px] tabular-nums",
                      passesAsInk
                        ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                        : "bg-rose-500/15 text-rose-700 dark:text-rose-400",
                    )}
                    title={`max channel ${luma.toFixed(2)}`}
                  >
                    {passesAsInk ? "ink" : "skip"}
                  </span>
                  {isHighlighted ? (
                    <Eye className="size-3 text-amber-600 dark:text-amber-400" />
                  ) : (
                    <EyeOff className="size-3 text-muted-foreground/40" />
                  )}
                </button>
              </li>
            );
          })}
          {summary.length === 0 && (
            <li className="px-1 py-3 text-center text-[12px] text-muted-foreground">
              No stored colours on this page.
            </li>
          )}
        </ul>
      </div>
    </aside>
  );
}
