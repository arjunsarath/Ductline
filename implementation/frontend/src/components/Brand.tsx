/**
 * Ductline brand mark — a stylized duct cross-section. Designed in Paper file
 * "Nice quartz" and reused across the top bar of all three views.
 */

export function Brand() {
  return (
    <div className="brand">
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
        <rect x="2" y="6" width="18" height="10" stroke="#0F1115" strokeWidth="1.5" fill="none" />
        <line x1="2" y1="11" x2="20" y2="11" stroke="#0F1115" strokeWidth="1.5" strokeDasharray="2 2" />
        <circle cx="6" cy="11" r="1.5" fill="#0F1115" />
        <circle cx="16" cy="11" r="1.5" fill="#0F1115" />
      </svg>
      <span className="brand-name">Ductline</span>
      <span className="brand-version">v1.0</span>
    </div>
  );
}
