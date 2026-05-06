/**
 * Resolve the V4 selection state into the concrete segment/terminal it
 * points at, plus any pipeline warnings that mention the segment.
 */

import { useMemo } from "react";
import type { V4Result, V4Segment, V4Terminal } from "../../types/v4";
import type { V4Selection } from "./V4Overlay";

export interface ResolvedSelection {
  segment: V4Segment | null;
  terminal: V4Terminal | null;
  segmentWarnings: string[];
}

export function useResolvedSelection(
  result: V4Result | null,
  selection: V4Selection,
): ResolvedSelection {
  return useMemo(() => {
    if (!result || !selection) {
      return { segment: null, terminal: null, segmentWarnings: [] };
    }
    if (selection.kind === "segment") {
      const segment =
        result.segments.find((s) => s.id === selection.id) ?? null;
      const segmentWarnings = segment
        ? filterWarnings(result.warnings, segment.id)
        : [];
      return { segment, terminal: null, segmentWarnings };
    }
    const terminal = result.terminals.find((t) => t.id === selection.id) ?? null;
    return { segment: null, terminal, segmentWarnings: [] };
  }, [result, selection]);
}

// Segment ids look like `edge::duct_39`; backend warnings cite the bare
// `duct_39`. Match either form so users see relevant notes.
function filterWarnings(warnings: string[], id: string): string[] {
  const bare = id.includes("::") ? id.split("::").pop() ?? id : id;
  return warnings.filter(
    (w) => w.includes(id) || (bare !== id && w.includes(bare)),
  );
}
