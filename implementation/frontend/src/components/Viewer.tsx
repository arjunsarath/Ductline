/**
 * Viewer — thin renderer router (V2 §5.7). Picks PdfCanvas for vector PDFs
 * (`coord_space === "pdf_points"`) and RasterCanvas for everything else.
 *
 * Falls through to RasterCanvas if the original File isn't available — the
 * vector path requires the bytes; today's pipeline always passes them, so
 * this is purely defensive.
 */

import type { DrawingResult } from "../types/api";
import { PdfCanvas } from "./PdfCanvas";
import { RasterCanvas } from "./RasterCanvas";
import type { Viewport } from "./viewport";

interface Props {
  result: DrawingResult;
  file: File | null;
  selectedId: string | null;
  grayscale: boolean;
  viewport: Viewport;
  onViewportChange: (next: Viewport) => void;
  onSelect: (id: string | null) => void;
  onRotate: () => void;
  onZoomBy: (factor: number) => void;
}

export function Viewer(props: Props) {
  const { result, file } = props;

  if (
    result.coord_space === "pdf_points" &&
    file &&
    result.page_size_pt &&
    result.page_size_pt[0] > 0 &&
    result.page_size_pt[1] > 0
  ) {
    return (
      <PdfCanvas
        {...props}
        file={file}
        pageSizePt={result.page_size_pt}
        rotation={result.rotation_applied ?? 0}
      />
    );
  }

  return <RasterCanvas {...props} />;
}
