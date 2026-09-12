import type { GraphNode } from "./types";

export interface Point {
  x: number;
  y: number;
}

const FOLDER_SPACING = 420;
const CHILD_SPACING = 220;

/**
 * Places top-level folders on a coarse grid, then clusters each folder's
 * children tightly around it. Zooming just multiplies these coordinates by
 * the camera scale, so a folder's children spread out from an invisible
 * cluster into a readable grid without a separate per-level layout pass.
 */
export function computeLayout(nodes: GraphNode[]): Record<string, Point> {
  const positions: Record<string, Point> = {};

  const topFolders = nodes.filter((n) => n.parent === null);
  const cols = Math.max(1, Math.ceil(Math.sqrt(topFolders.length)));
  topFolders.forEach((folder, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    positions[folder.id] = { x: col * FOLDER_SPACING, y: row * FOLDER_SPACING };
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

export function centroid(points: Point[]): Point {
  if (points.length === 0) return { x: 0, y: 0 };
  const sum = points.reduce((acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y }), { x: 0, y: 0 });
  return { x: sum.x / points.length, y: sum.y / points.length };
}

const NODE_W = 160;
const NODE_H = 130;

/** Scale that fits a set of world points (plus node size) inside the canvas, so a folder's
 * children stay on screen regardless of how many there are or how the layout spaced them. */
export function fitScale(points: Point[], canvasW: number, canvasH: number, min: number, max: number): number {
  if (points.length === 0) return min;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const width = Math.max(...xs) - Math.min(...xs) + NODE_W;
  const height = Math.max(...ys) - Math.min(...ys) + NODE_H;
  const margin = 100;
  const scaleX = (canvasW - margin * 2) / Math.max(width, 1);
  const scaleY = (canvasH - margin * 2) / Math.max(height, 1);
  return Math.min(max, Math.max(min, Math.min(scaleX, scaleY)));
}
