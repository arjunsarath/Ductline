/**
 * Viewport types and constants. Lives in its own module so React Fast
 * Refresh can hot-reload Viewer.tsx (Fast Refresh requires component files
 * to export only components).
 */

export interface Viewport {
  scale: number;
  tx: number;
  ty: number;
  rotationDeg: number;
}

export const INITIAL_VIEWPORT: Viewport = { scale: 1, tx: 0, ty: 0, rotationDeg: 0 };
export const SCALE_MIN = 0.2;
export const SCALE_MAX = 8;
