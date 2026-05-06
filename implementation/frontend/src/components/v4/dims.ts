/**
 * Layout maths for the V4 viewer:
 *  • drawing dims come from the backend's rasterized page (page_dims) so the
 *    overlay viewBox matches the exact pixel space CV ran in;
 *  • fit the page+overlay into the available viewport.
 *
 * page_dims is the source of truth — earlier iterations inferred dims from
 * the geometry bbox + PDF aspect ratio, which mis-aligned coordinates
 * whenever data didn't extend to the page edges.
 */

import { useMemo } from "react";
import type { V4Result } from "../../types/v4";

export interface Dims {
  width: number;
  height: number;
}

export function useDrawingDims(result: V4Result | null): Dims | null {
  return useMemo(() => {
    if (!result) return null;
    return {
      width: result.page_dims.width_px,
      height: result.page_dims.height_px,
    };
  }, [result]);
}

export function useFitDims(
  dims: Dims | null,
  winSize: { w: number; h: number },
): { w: number; h: number } {
  return useMemo(() => {
    if (!dims) return { w: 0, h: 0 };
    const maxW = winSize.w * 0.7;
    const maxH = Math.max(200, winSize.h - 200);
    const aspect = dims.width / dims.height;
    let w = maxW;
    let h = w / aspect;
    if (h > maxH) {
      h = maxH;
      w = h * aspect;
    }
    return { w, h };
  }, [dims, winSize]);
}
