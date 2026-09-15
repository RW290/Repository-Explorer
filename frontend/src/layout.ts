import type { GraphNode } from "./types";

export interface Point {
  x: number;
  y: number;
}

const FOLDER_SPACING_X = 300;
const FOLDER_SPACING_Y = 300;
const CHILD_SPACING = 220;
// Top folders lay out as a row beneath the overview hub card, wrapping only
// once a row gets wide enough to fight for horizontal space. A square-ish
// grid (ceil(sqrt(n))) would push even three folders onto two rows, and that
// extra vertical extent forces fitScale to zoom the whole repo view out far
// enough that the cards become unreadably small.
const MAX_FOLDER_COLS = 5;

/**
 * Places top-level folders in a row (wrapping past MAX_FOLDER_COLS) beneath
 * the overview hub, then clusters each folder's children tightly around it.
 * Zooming just multiplies these coordinates by the camera scale, so a
 * folder's children spread out from an invisible cluster into a readable
 * grid without a separate per-level layout pass.
 */
export function computeLayout(nodes: GraphNode[]): Record<string, Point> {
  const positions: Record<string, Point> = {};

  const topFolders = nodes.filter((n) => n.parent === null);
  const cols = Math.min(MAX_FOLDER_COLS, Math.max(1, topFolders.length));
  topFolders.forEach((folder, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    // Center each row horizontally so a partly-filled last row doesn't hang
    // off to one side under the hub card.
    const rowCount = Math.min(cols, topFolders.length - row * cols);
    const offset = (cols - rowCount) / 2;
    positions[folder.id] = { x: (col + offset) * FOLDER_SPACING_X, y: row * FOLDER_SPACING_Y };
  });

  const byParent: Record<string, GraphNode[]> = {};
  nodes.forEach((n) => {
    if (n.parent) {
      (byParent[n.parent] ??= []).push(n);
    }
  });

  Object.entries(byParent).forEach(([parentId, children]) => {
    const base = positions[parentId];
    if (!base) return;
    const childCols = Math.max(1, Math.ceil(Math.sqrt(children.length)));
    const rows = Math.ceil(children.length / childCols);
    children.forEach((child, i) => {
      const col = i % childCols;
      const row = Math.floor(i / childCols);
      positions[child.id] = {
        x: base.x + (col - (childCols - 1) / 2) * CHILD_SPACING,
        y: base.y + (row - (rows - 1) / 2) * CHILD_SPACING,
      };
    });
  });

  return positions;
}

const NODE_W = 160;
const NODE_H = 130;

/** Center of the folder grid's bounding box, which the repo-level camera
 * frames. The project overview is no longer part of this composition — it's
 * a screen-space overlay, so its size doesn't depend on how many folders
 * there are (and the folder count doesn't shrink the prose). */
export function repoViewCenter(folders: Point[]): Point {
  if (folders.length === 0) return { x: 0, y: 0 };
  const xs = folders.map((p) => p.x);
  const ys = folders.map((p) => p.y);
  return {
    x: (Math.min(...xs) + Math.max(...xs)) / 2,
    y: (Math.min(...ys) + Math.max(...ys)) / 2,
  };
}

/** Scale that fits a set of world points (plus node size) inside the canvas, so a folder's
 * children stay on screen regardless of how many there are or how the layout spaced them.
 * `extraHeight` accommodates one outsized point (the project overview hub card) that's much
 * taller than a standard node — passing it avoids clipping that card without needing per-point
 * sizes threaded through the whole fit calculation. */
export function fitScale(
  points: Point[],
  canvasW: number,
  canvasH: number,
  min: number,
  max: number,
  extraHeight = 0,
): number {
  if (points.length === 0) return min;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const width = Math.max(...xs) - Math.min(...xs) + NODE_W;
  const height = Math.max(...ys) - Math.min(...ys) + NODE_H + extraHeight;
  const margin = 100;
  const scaleX = (canvasW - margin * 2) / Math.max(width, 1);
  const scaleY = (canvasH - margin * 2) / Math.max(height, 1);
  return Math.min(max, Math.max(min, Math.min(scaleX, scaleY)));
}

