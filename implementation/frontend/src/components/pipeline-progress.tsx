"use client";

import { Check, Loader2, AlertTriangle, ArrowLeft, RotateCcw } from "lucide-react";
import AppHeader from "@/components/app-header";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type StepStatus = "idle" | "running" | "done";

export type PipelineState = {
  extract: StepStatus;
  scale: StepStatus;
  cleanup: StepStatus;
};

type Props = {
  filename: string;
  state: PipelineState;
  error: string | null;
  onRecrop: () => void;
  onReset: () => void;
};

const STAGES: { key: keyof PipelineState; title: string; description: string }[] = [
  {
    key: "extract",
    title: "Find elements",
    description: "Reading vector geometry inside the cropped region",
  },
  {
    key: "scale",
    title: "Detect scale",
    description: "Locating Ø callouts and inferring the drawing scale",
  },
  {
    key: "cleanup",
    title: "Cleanup",
    description: "Filtering out non-duct rectangles by size and area",
  },
];

export default function PipelineProgress({
  filename,
  state,
  error,
  onRecrop,
  onReset,
}: Props) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <AppHeader
        filename={filename}
        onReset={onReset}
        meta={
          <span className="rounded-full border border-border/60 bg-card/60 px-2 py-0.5 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Processing
          </span>
        }
      />
      <section className="flex min-h-0 flex-1 items-center justify-center bg-[radial-gradient(circle_at_center,oklch(0.97_0_0)_0%,oklch(0.93_0.003_260)_100%)] p-8">
        <div className="w-full max-w-md rounded-xl border border-border/60 bg-card/80 p-6 shadow-xl backdrop-blur">
          <h2 className="text-[15px] font-medium text-foreground">
            {error ? "Pipeline failed" : "Running pipeline…"}
          </h2>
          <p className="mt-1 text-[12.5px] text-muted-foreground">
            {error
              ? "One of the stages couldn't complete — adjust the crop and try again."
              : "Find → detect scale → cleanup. This usually takes a few seconds."}
          </p>

          <ul className="mt-5 space-y-3">
            {STAGES.map((s) => (
              <StageRow
                key={s.key}
                title={s.title}
                description={s.description}
                status={state[s.key]}
                hasError={error !== null && state[s.key] === "running"}
              />
            ))}
          </ul>

          {error && (
            <div className="mt-5 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-[12.5px] text-destructive">
              <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                <p className="leading-relaxed">{error}</p>
              </div>
            </div>
          )}

          {error && (
            <div className="mt-5 flex items-center justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={onReset}>
                <RotateCcw />
                Start over
              </Button>
              <Button size="sm" onClick={onRecrop}>
                <ArrowLeft />
                Back to crop
              </Button>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function StageRow({
  title,
  description,
  status,
  hasError,
}: {
  title: string;
  description: string;
  status: StepStatus;
  hasError: boolean;
}) {
  return (
    <li
      className={cn(
        "flex items-start gap-3 rounded-md border p-2.5",
        status === "running" && !hasError && "border-primary/30 bg-primary/5",
        status === "done" && "border-emerald-400/30 bg-emerald-50/40 dark:bg-emerald-950/20",
        status === "idle" && "border-border/50 bg-background/40",
        hasError && "border-destructive/40 bg-destructive/5",
      )}
    >
      <span
        className={cn(
          "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border",
          status === "done" && "border-emerald-500/60 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
          status === "running" && !hasError && "border-primary/50 bg-primary/15 text-primary",
          status === "idle" && "border-border bg-muted/30 text-muted-foreground",
          hasError && "border-destructive/50 bg-destructive/15 text-destructive",
        )}
      >
        {hasError ? (
          <AlertTriangle className="size-3" />
        ) : status === "done" ? (
          <Check className="size-3" />
        ) : status === "running" ? (
          <Loader2 className="size-3 animate-spin" />
        ) : (
          <span className="size-1.5 rounded-full bg-current opacity-40" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium text-foreground">{title}</p>
        <p className="mt-0.5 text-[12px] text-muted-foreground">{description}</p>
      </div>
    </li>
  );
}
