"use client";

import { useCallback, useRef, useState } from "react";
import { ArrowRight, FileText, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

type Props = {
  apiUrl: string;
  initialFile: File | null;
  onContinue: (file: File) => void;
};

export default function UploadScreen({ apiUrl, initialFile, onContinue }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(initialFile);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSelect = useCallback((f: File | null) => {
    setError(null);
    if (!f) {
      setFile(null);
      return;
    }
    if (!f.name.toLowerCase().endsWith(".pdf") && f.type !== "application/pdf") {
      setError("Only PDF files are accepted.");
      setFile(null);
      return;
    }
    setFile(f);
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragOver(false);
      onSelect(e.dataTransfer.files?.[0] ?? null);
    },
    [onSelect],
  );

  return (
    <main className="flex flex-1 items-center justify-center px-6 py-10">
      <div className="w-full max-w-xl space-y-8">
        <header className="space-y-3 text-center">
          <div className="mx-auto flex size-10 items-center justify-center rounded-xl bg-primary/15 text-primary">
            <FileText className="size-5" strokeWidth={2} />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">
            PDF Element Inspector
          </h1>
          <p className="mx-auto max-w-sm text-[13px] leading-relaxed text-muted-foreground">
            Upload an engineering drawing to inspect every geometric and text
            element extracted by pdfplumber.
          </p>
        </header>

        <div
          role="button"
          tabIndex={0}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          className={cn(
            "group relative flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border border-dashed bg-card/40 px-6 py-14 text-center transition-all",
            "hover:border-primary/60 hover:bg-card/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
            dragOver
              ? "border-primary/80 bg-primary/5 ring-2 ring-primary/30"
              : "border-border",
          )}
        >
          <div
            className={cn(
              "flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground transition-all",
              "group-hover:bg-primary/10 group-hover:text-primary",
              dragOver && "scale-110 bg-primary/15 text-primary",
            )}
          >
            <Upload className="size-5" strokeWidth={2} />
          </div>
          <div className="space-y-1">
            <p className="text-sm font-medium">
              {dragOver ? "Drop to load" : "Drop a PDF here, or click to browse"}
            </p>
            <p className="text-xs text-muted-foreground">
              PDF only · up to 25 MB
            </p>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            className="hidden"
            onChange={(e) => onSelect(e.target.files?.[0] ?? null)}
          />
        </div>

        {file && (
          <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-3 py-2.5">
            <div className="flex size-8 items-center justify-center rounded-md bg-primary/10 text-primary">
              <FileText className="size-4" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-medium" title={file.name}>
                {file.name}
              </p>
              <p className="text-[11px] tabular-nums text-muted-foreground">
                {formatSize(file.size)} · application/pdf
              </p>
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={(e) => {
                e.stopPropagation();
                onSelect(null);
              }}
              aria-label="Remove file"
            >
              <X />
            </Button>
            <Button onClick={() => onContinue(file)} size="sm">
              Continue
              <ArrowRight />
            </Button>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-[13px] text-destructive">
            {error}
          </div>
        )}

        <p className="text-center font-mono text-[11px] text-muted-foreground/70">
          POST {apiUrl}
        </p>
      </div>
    </main>
  );
}
