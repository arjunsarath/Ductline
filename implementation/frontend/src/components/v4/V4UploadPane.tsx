/**
 * Landing pane for V4 — drop a single-page mechanical PDF. Mirrors the V3
 * upload affordance for visual consistency, with the assumption banner
 * embedded so first-time users see A1–A15 before submitting.
 */

import { V4AssumptionBanner } from "./V4AssumptionBanner";

interface Props {
  onFile: (f: File) => void;
}

export function V4UploadPane({ onFile }: Props) {
  return (
    <main className="upload-view">
      <header className="topbar">
        <div className="brand">Ductline · V4</div>
        <span className="topbar-pill">● length · CFM · pressure</span>
      </header>
      <section className="upload-body">
        <div className="upload-header">
          <h1 className="upload-title">Drop a single-page mechanical PDF.</h1>
          <p className="upload-subtitle">
            V4 detects ducts, terminals, and connectors; computes length, CFM,
            and pressure per segment; and classifies each segment under SMACNA.
          </p>
        </div>
        <V4AssumptionBanner />
        <label
          className="dropzone"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const f = e.dataTransfer.files?.[0];
            if (f) onFile(f);
          }}
        >
          <div className="dropzone-text">
            <div className="dropzone-heading">Drop a PDF here, or browse</div>
            <div className="dropzone-hint">single page · A15</div>
          </div>
          <input
            type="file"
            accept=".pdf,application/pdf"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onFile(f);
            }}
            hidden
          />
        </label>
      </section>
    </main>
  );
}
