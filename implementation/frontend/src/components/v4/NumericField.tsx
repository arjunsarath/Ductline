/**
 * Single-line numeric field used by the V4 settings drawer. Coerces
 * non-finite input to 0 so the form state stays a valid OperationalVars
 * payload at all times.
 */

interface Props {
  label: string;
  value: number;
  step: number;
  onChange: (next: number) => void;
}

export function NumericField({ label, value, step, onChange }: Props) {
  return (
    <label className="v4-drawer-row">
      <span>{label}</span>
      <input
        type="number"
        step={step}
        value={Number.isFinite(value) ? value : 0}
        onChange={(e) => {
          const n = parseFloat(e.target.value);
          onChange(Number.isFinite(n) ? n : 0);
        }}
      />
    </label>
  );
}
