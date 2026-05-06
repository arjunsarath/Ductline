/**
 * Calculation Settings drawer (SOLUTION-DESIGN-V4 §7).
 *
 * Hosts every editable input on `OperationalVars` plus an optional
 * source_node_id override. On Save the parent re-runs the V4 session with
 * the updated settings — the brief explicitly accepts a full re-upload as
 * the "live recompute" mechanism.
 */

import { useState } from "react";
import type { OperationalVars } from "../../types/v4";
import { NumericField } from "./NumericField";

interface Props {
  initial: OperationalVars;
  initialSourceNodeId: string;
  busy: boolean;
  onSave: (next: OperationalVars, sourceNodeId: string) => void;
  onClose: () => void;
}

const FITTING_KEYS = [
  "elbow",
  "transition",
  "tee",
  "y_branch",
  "equipment",
  "terminal",
] as const;

export function V4SettingsDrawer({
  initial,
  initialSourceNodeId,
  busy,
  onSave,
  onClose,
}: Props) {
  const [draft, setDraft] = useState<OperationalVars>(initial);
  const [sourceId, setSourceId] = useState<string>(initialSourceNodeId);

  function patch(partial: Partial<OperationalVars>) {
    setDraft((d) => ({ ...d, ...partial }));
  }

  function patchSmacna(partial: Partial<OperationalVars["smacna_thresholds_in_wc"]>) {
    setDraft((d) => ({
      ...d,
      smacna_thresholds_in_wc: { ...d.smacna_thresholds_in_wc, ...partial },
    }));
  }

  function patchVelocity(partial: Partial<OperationalVars["velocity_thresholds_fpm"]>) {
    setDraft((d) => ({
      ...d,
      velocity_thresholds_fpm: { ...d.velocity_thresholds_fpm, ...partial },
    }));
  }

  function patchK(key: string, value: number) {
    setDraft((d) => ({
      ...d,
      fitting_k_table: { ...d.fitting_k_table, [key]: value },
    }));
  }

  return (
    <aside className="v4-drawer" role="dialog" aria-label="Calculation settings">
      <header className="v4-drawer-head">
        <h3>Calculation settings</h3>
        <button
          type="button"
          className="v4-detail-close"
          aria-label="Close settings"
          onClick={onClose}
        >
          ×
        </button>
      </header>

      <div className="v4-drawer-body">
        <fieldset className="v4-drawer-section">
          <legend>Air & friction</legend>
          <NumericField
            label='Air density (lb/ft³)'
            value={draft.air_density_lb_ft3}
            step={0.001}
            onChange={(v) => patch({ air_density_lb_ft3: v })}
          />
          <NumericField
            label="Friction factor"
            value={draft.friction_factor}
            step={0.001}
            onChange={(v) => patch({ friction_factor: v })}
          />
          <NumericField
            label="Source pressure (in. w.c.)"
            value={draft.source_pressure_in_wc}
            step={0.01}
            onChange={(v) => patch({ source_pressure_in_wc: v })}
          />
          <NumericField
            label="Flex equivalent length (ft)"
            value={draft.flex_equiv_length_ft}
            step={0.5}
            onChange={(v) => patch({ flex_equiv_length_ft: v })}
          />
        </fieldset>

        <fieldset className="v4-drawer-section">
          <legend>Fitting K-values</legend>
          {FITTING_KEYS.map((k) => (
            <NumericField
              key={k}
              label={k}
              value={draft.fitting_k_table[k] ?? 0}
              step={0.05}
              onChange={(v) => patchK(k, v)}
            />
          ))}
        </fieldset>

        <fieldset className="v4-drawer-section">
          <legend>SMACNA thresholds (in. w.c.)</legend>
          <NumericField
            label="Low ≤"
            value={draft.smacna_thresholds_in_wc.low_max_in_wc}
            step={0.1}
            onChange={(v) => patchSmacna({ low_max_in_wc: v })}
          />
          <NumericField
            label="Medium upper"
            value={draft.smacna_thresholds_in_wc.medium_max_in_wc}
            step={0.1}
            onChange={(v) => patchSmacna({ medium_max_in_wc: v })}
          />
        </fieldset>

        <fieldset className="v4-drawer-section">
          <legend>Velocity thresholds (FPM)</legend>
          <NumericField
            label="Low ≤"
            value={draft.velocity_thresholds_fpm.low_max_fpm}
            step={50}
            onChange={(v) => patchVelocity({ low_max_fpm: v })}
          />
          <NumericField
            label="Medium upper"
            value={draft.velocity_thresholds_fpm.medium_max_fpm}
            step={50}
            onChange={(v) => patchVelocity({ medium_max_fpm: v })}
          />
        </fieldset>

        <fieldset className="v4-drawer-section">
          <legend>Source node (optional)</legend>
          <label className="v4-drawer-row">
            <span>Source node ID</span>
            <input
              type="text"
              value={sourceId}
              placeholder="auto"
              onChange={(e) => setSourceId(e.target.value)}
            />
          </label>
        </fieldset>
      </div>

      <footer className="v4-drawer-foot">
        <button type="button" className="button-ghost" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className="button button-primary"
          onClick={() => onSave(draft, sourceId.trim())}
          disabled={busy}
        >
          {busy ? "Recomputing…" : "Save & recompute"}
        </button>
      </footer>
    </aside>
  );
}

