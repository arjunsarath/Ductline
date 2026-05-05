/**
 * V3 upload view. Minimal drop-zone variant of the agent UploadView —
 * V3's flow is Upload → Picker → Result, so this view exits as soon as
 * a file is chosen.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchSample,
  listSamples,
  type V3SampleEntry,
} from "../../api/v3Client";

interface Props {
  onFile: (file: File) => void;
  errorMessage?: string;
}

export function V3Upload({ onFile, errorMessage }: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [samples, setSamples] = useState<V3SampleEntry[] | null>(null);

  // Show samples on the landing page so first-time users have something
  // to click. Loads on mount; if the backend has no /drawings volume the
  // list is empty and the section just doesn't render.
  useEffect(() => {
    listSamples().then(setSamples).catch(() => setSamples([]));
  }, []);

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
        <div className="brand">Ductline · V3</div>
        <span className="topbar-pill">● color-driven · deterministic</span>
      </header>
      <section className="upload-body">
        <div className="upload-header">
          <h1 className="upload-title">Drop a color-coded duct plan.</h1>
          <p className="upload-subtitle">
            V3 detects ducts by the system colors you label on the page —
            no LLM, no review loop. Best on Pattern B drawings (closed
            colored outline around each duct run).
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
          <div className="dropzone-text">
            <div className="dropzone-heading">Drop a file here, or browse</div>
            <div className="dropzone-hint">PDF, PNG, or JPG · single page</div>
          </div>
          <div className="dropzone-actions" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="button button-primary"
              onClick={() => inputRef.current?.click()}
            >
              Choose drawing
            </button>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
            onChange={onChange}
            hidden
          />
          {errorMessage && (
            <p className="upload-error" role="alert">
              {errorMessage}
            </p>
          )}
        </div>
        {samples !== null && samples.length > 0 && (
          <div className="samples-panel">
            <h3 className="samples-title">Or try a sample</h3>
            <ul className="samples-list">
              {samples.map((s) => (
                <li key={s.name}>
                  <button
                    type="button"
                    className="samples-row"
                    onClick={async () => {
                      try {
                        const file = await fetchSample(s.name);
                        onFile(file);
                      } catch {
                        /* swallow — keep dialog open */
                      }
                    }}
                  >
                    <span className="mono samples-row-name">{s.name}</span>
                    <span className="samples-row-size">
                      {(s.size_bytes / 1024).toFixed(0)} KB
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </main>
  );
}
