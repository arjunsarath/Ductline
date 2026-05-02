/**
 * Upload view per Paper artboard 01. Brand bar → centered hero (stepper +
 * title + subtitle) → dropzone → 3-column footer (in-scope / won't fit / latency).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSample, listSamples } from "../api/client";
import type { SampleDrawing } from "../types/api";
import { Brand } from "./Brand";
import { Stepper } from "./Stepper";

const ACCEPTED = ".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg";

interface Props {
  onFile: (file: File) => void;
  errorMessage?: string;
}

export function UploadView({ onFile, errorMessage }: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [showSamples, setShowSamples] = useState(false);
  const [samples, setSamples] = useState<SampleDrawing[] | null>(null);
  const [samplesError, setSamplesError] = useState<string | null>(null);

  useEffect(() => {
    if (!showSamples || samples !== null) return;
    listSamples()
      .then(setSamples)
      .catch((err: unknown) => {
        setSamplesError(err instanceof Error ? err.message : "samples unavailable");
      });
  }, [showSamples, samples]);

  const handleSampleClick = useCallback(
    async (name: string) => {
      try {
        const file = await fetchSample(name);
        onFile(file);
      } catch (err) {
        setSamplesError(err instanceof Error ? err.message : "fetch failed");
      }
    },
    [onFile],
  );

  const onChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (file) onFile(file);
    },
    [onFile],
  );

  const onDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsDragging(false);
      const file = event.dataTransfer.files?.[0];
      if (file) onFile(file);
    },
    [onFile],
  );

  return (
    <main className="upload-view">
      <header className="topbar">
        <Brand />
        <nav className="topbar-actions">
          <a
            className="topbar-link"
            href="https://github.com/arjunsarath/Ductline"
            target="_blank"
            rel="noreferrer"
          >
            Docs
          </a>
          <a className="topbar-link" href="#benchmark">
            Benchmark drawings
          </a>
          <span className="topbar-pill">● llama3.2-vision</span>
        </nav>
      </header>

      <section className="upload-body">
        <div className="upload-header">
          <Stepper active="upload" />
          <h1 className="upload-title">Drop a duct plan to begin.</h1>
          <p className="upload-subtitle">
            Single-page mechanical drawing. We extract duct geometry,
            dimensions, and pressure class — every value cites the evidence
            that produced it.
          </p>
        </div>

        <div
          className={`dropzone${isDragging ? " is-dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragging(true);
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
          }}
          role="button"
          tabIndex={0}
        >
          <DropzoneIcon />
          <div className="dropzone-text">
            <div className="dropzone-heading">Drop a file here, or browse</div>
            <div className="dropzone-hint">
              PDF, PNG, or JPG · single page · up to 50 MB
            </div>
          </div>
          <div
            className="dropzone-actions"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="button button-primary"
              onClick={() => inputRef.current?.click()}
            >
              <DownloadIcon />
              Choose drawing
            </button>
            <button
              type="button"
              className="button button-secondary"
              aria-pressed={showSamples}
              onClick={(e) => {
                e.stopPropagation();
                setShowSamples((s) => !s);
              }}
            >
              Try a sample
            </button>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED}
            onChange={onChange}
            hidden
          />
          {errorMessage && (
            <p className="upload-error" role="alert">
              {errorMessage}
            </p>
          )}
        </div>

        {showSamples && (
          <div className="samples-panel" role="dialog" aria-label="Sample drawings">
            <div className="samples-panel-head">
              <span className="eyebrow">Benchmark drawings</span>
              <span className="samples-panel-hint">
                Sourced from public mechanical engineering sets — see README.
              </span>
            </div>
            {samples === null && !samplesError && (
              <div className="samples-empty">Loading…</div>
            )}
            {samplesError && (
              <div className="samples-empty" role="alert">
                {samplesError}
              </div>
            )}
            {samples && samples.length > 0 && (
              <ul className="samples-list">
                {samples.map((sample) => (
                  <li key={sample.name}>
                    <button
                      type="button"
                      className="samples-row"
                      onClick={() => handleSampleClick(sample.name)}
                    >
                      <span className="mono samples-row-name">{sample.name}</span>
                      <span className="samples-row-size">
                        {formatBytes(sample.size_bytes)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <footer className="upload-footer">
          <div className="upload-footer-cell">
            <div className="upload-footer-label">In scope</div>
            <div className="upload-footer-body">
              Round &amp; rectangular ducts. Plan-view drawings. Schedule
              lookups.
            </div>
          </div>
          <div className="upload-footer-cell">
            <div className="upload-footer-label">Won't fit v1</div>
            <div className="upload-footer-body">
              Multi-page sets · fittings · CAD parsing · 3D · hand-drawn.
            </div>
          </div>
          <div className="upload-footer-cell">
            <div className="upload-footer-label">Latency</div>
            <div className="upload-footer-body">
              ≤ 30 s P50 · one VLM call per drawing.
            </div>
          </div>
        </footer>
      </section>
    </main>
  );
}

function DropzoneIcon() {
  return (
    <svg width="56" height="56" viewBox="0 0 56 56" fill="none" aria-hidden="true">
      <rect x="10" y="6" width="36" height="44" rx="2" stroke="#0F1115" strokeWidth="1.5" fill="#FAFAF7" />
      <line x1="16" y1="18" x2="40" y2="18" stroke="#5C6166" strokeDasharray="2 2" />
      <rect x="16" y="24" width="14" height="8" stroke="#0F1115" strokeWidth="1.2" fill="none" />
      <circle cx="36" cy="36" r="3" stroke="#0F1115" strokeWidth="1.2" fill="none" />
      <path d="M28 40 L28 48 M24 44 L28 48 L32 44" stroke="#059669" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function DownloadIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path d="M7 2 L7 10 M3 6 L7 10 L11 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <line x1="2" y1="13" x2="12" y2="13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}
