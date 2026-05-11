"use client";

import { FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Props = {
  filename?: string;
  // Free-form right-side content (e.g. page indicator, zoom controls).
  meta?: React.ReactNode;
  onReset?: () => void;
  className?: string;
};

export default function AppHeader({ filename, meta, onReset, className }: Props) {
  return (
    <header
      className={cn(
        "flex h-12 shrink-0 items-center gap-3 border-b border-border/60 bg-card/40 px-4 backdrop-blur",
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <div className="flex size-6 items-center justify-center rounded-md bg-primary/15 text-primary">
          <FileText className="size-3.5" strokeWidth={2.25} />
        </div>
        <span className="text-[13px] font-semibold tracking-tight">Techjay</span>
        <span className="text-xs text-muted-foreground/70">·</span>
        <span className="text-[12px] uppercase tracking-[0.14em] text-muted-foreground">
          PDF Inspector
        </span>
      </div>

      {filename && (
        <>
          <div className="h-4 w-px bg-border" />
          <span className="truncate text-[13px] text-foreground/90" title={filename}>
            {filename}
          </span>
        </>
      )}

      <div className="ml-auto flex items-center gap-2">
        {meta}
        {onReset && (
          <Button variant="ghost" size="sm" onClick={onReset}>
            New file
          </Button>
        )}
      </div>
    </header>
  );
}
