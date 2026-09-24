import type { GraphNode } from "./types";

export interface Point {
  x: number;
  y: number;
}

const FOLDER_SPACING_X = 300;
const FOLDER_SPACING_Y = 220;
const CHILD_SPACING = 220;
// Top folders lay out in rows of at most this many, beneath the project
// overview that occupies the top of the screen. A square-ish grid
// (ceil(sqrt(n))) would spread them wider than the band left over for them.
const MAX_FOLDER_COLS = 3;

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

const NODE_W = 196;
const NODE_H = 150;

export function repoViewCenter(folders: Point[]): Point {
  if (folders.length === 0) return { x: 0, y: 0 };
  const xs = folders.map((p) => p.x);
  const ys = folders.map((p) => p.y);
  return {
    x: (Math.min(...xs) + Math.max(...xs)) / 2,
    y: (Math.min(...ys) + Math.max(...ys)) / 2,
  };
}

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
  // Proportional, not a flat 100: the repo level fits folders into a band
  // that's only ~half the viewport, and a fixed 100px gutter on each side
  // would eat most of a short screen's band and drive the fit to nonsense.
  const margin = Math.min(100, canvasH * 0.1, canvasW * 0.1);
  const scaleX = (canvasW - margin * 2) / Math.max(width, 1);
  const scaleY = (canvasH - margin * 2) / Math.max(height, 1);
  return Math.min(max, Math.max(min, Math.min(scaleX, scaleY)));
}

