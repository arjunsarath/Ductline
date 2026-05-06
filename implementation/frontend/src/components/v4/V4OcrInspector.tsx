/**
 * Floating debug card showing the exact crop sent to the VLM and the text it
 * returned. Anchored top-right of the viewer so the operator can compare the
 * input image to the output text without leaving the page.
 */

import type { DebugOcrMatch } from "../../types/v4";

interface Props {
  match: DebugOcrMatch;
  onClose: () => void;
}

export function V4OcrInspector({ match, onClose }: Props) {
  const [x, y, w, h] = match.bbox;
  return (
    <aside
      className="v4-ocr-inspector"
      role="region"
      aria-label="OCR match details"
    >
      <header>
        <strong>OCR match</strong>
        <button type="button" className="v4-ocr-inspector__close" onClick={onClose}>
          ×
        </button>
      </header>
      {match.crop_data_url ? (
        <div className="v4-ocr-inspector__crop">
          <img src={match.crop_data_url} alt="OCR input crop" />
        </div>
      ) : (
        <div className="v4-ocr-inspector__crop v4-ocr-inspector__crop--missing">
          (crop unavailable)
        </div>
      )}
      <dl className="v4-ocr-inspector__data">
        <dt>Text</dt>
        <dd className="v4-ocr-inspector__text">{match.text || "(empty)"}</dd>
        <dt>Bbox</dt>
        <dd>{`x=${x}, y=${y}, ${w}×${h} px`}</dd>
        <dt>Confidence</dt>
        <dd>{(match.confidence * 100).toFixed(0)}%</dd>
      </dl>
    </aside>
  );
}
