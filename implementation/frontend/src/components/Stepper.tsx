/**
 * Three-step progress indicator (Upload → Processing → Result) per Paper
 * artboards 01 and 02.
 */

type Step = "upload" | "processing" | "result";

const STEPS: Array<{ id: Step; label: string }> = [
  { id: "upload", label: "Step 01 / Upload" },
  { id: "processing", label: "Step 02 / Processing" },
  { id: "result", label: "Result" },
];

export function Stepper({ active }: { active: Step }) {
  return (
    <div className="stepper" role="progressbar" aria-valuetext={active}>
      {STEPS.map((step, index) => (
        <span key={step.id} style={{ display: "contents" }}>
          {index > 0 && <span className="stepper-divider" />}
          <span
            className={step.id === active ? "stepper-active" : "stepper-inactive"}
          >
            {step.label}
          </span>
        </span>
      ))}
    </div>
  );
}
