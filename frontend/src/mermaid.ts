import type { ArchitectureGroup } from "./types";
import type { Theme } from "./ThemeToggle";
import { languageOf, labelOf, toneOf } from "./languages";

/**
 * Deterministic map → Mermaid compiler.
 *
 * The model never writes Mermaid. It produces a validated JSON graph (see
 * backend/app/architecture.py) whose nodes are the explorer's own file and
 * folder nodes; the view turns that into a MapModel (labels, categories,
 * extra import edges) and this turns the model into flowchart source. Every
 * label goes through one escape function, so nothing the model wrote can
 * smuggle in syntax.
 */

export type Direction = "LR" | "TD";
export type Category = "folder" | "code" | "other" | "external";

export interface MapNode {
  /** Graph node id (the file or folder path). */
  id: string;
  /** First label line: the file or folder name, as on the explorer cards. */
  label: string;
  /** Quieter second line: the parent folder, or the folder's file count. */
  sub: string | null;
  /** Short description (the file summary's first sentence), wrapped by the
   * compiler onto up to two further lines. */
  desc: string | null;
  category: Category;
  group: string | null;
}

export interface MapEdge {
  source: string;
  target: string;
  label: string | null;
  /** Import graph vouches for it (solid) or the model asserted it (dashed). */
  backed: boolean;
  /** An import the model didn't draw, shown thin when "all imports" is on. */
  faint?: boolean;
}

export interface MapModel {
  groups: ArchitectureGroup[];
  nodes: MapNode[];
  edges: MapEdge[];
}

export interface Compiled {
  source: string;
  /** Mermaid node id → graph node id, for wiring clicks after render. */
  pathFor: Map<string, string>;
}

interface Tone {
  fill: string;
  stroke: string;
  text: string;
}

// Fallback node colours by category. A file whose kind is known (a language,
// docs, config) takes that kind's tone from languages.ts instead, so a box on
// the map is the same colour as its card in the grid.
const CATEGORY_TONES: Record<Theme, Record<Category, Tone>> = {
  dark: {
    folder: { fill: "#1c3550", stroke: "#327dca", text: "#e6f1ff" },
    code: { fill: "#1a3a30", stroke: "#278c63", text: "#e3fbef" },
    other: { fill: "#1f2030", stroke: "#3c3f55", text: "#e8e7f2" },
    external: { fill: "#181920", stroke: "#4a4d5e", text: "#9a9db0" },
  },
  light: {
    folder: { fill: "#e4f2ff", stroke: "#8bbcf0", text: "#16385a" },
    code: { fill: "#e5f8ee", stroke: "#8bcfae", text: "#113f2c" },
    other: { fill: "#ffffff", stroke: "#c9cfdb", text: "#273142" },
    external: { fill: "#f1f2f6", stroke: "#c3c8d4", text: "#6b7386" },
  },
};

const DESC_MAX_CHARS = 92;

export function trimDescription(text: string): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= DESC_MAX_CHARS) return clean;
  const cut = clean.slice(0, DESC_MAX_CHARS);
  return `${cut.slice(0, Math.max(cut.lastIndexOf(" "), 40)).trimEnd()}…`;
}

// One tone per semantic group, cycling: the subgraph's border and label.
// 8-digit hex fills: Mermaid's `style` parser can't take rgba(...).
const GROUP_TONES: Record<Theme, { stroke: string; fill: string }[]> = {
  dark: [
    { stroke: "#8b7bff", fill: "#8b7bff12" },
    { stroke: "#e5a03e", fill: "#e5a03e0f" },
    { stroke: "#3ec7c2", fill: "#3ec7c20f" },
    { stroke: "#ef6b8a", fill: "#ef6b8a0f" },
    { stroke: "#4e9cff", fill: "#4e9cff0f" },
    { stroke: "#43c98b", fill: "#43c98b0f" },
  ],
  light: [
    { stroke: "#6653d5", fill: "#6653d50d" },
    { stroke: "#c47f1c", fill: "#c47f1c0f" },
    { stroke: "#0f8b86", fill: "#0f8b860d" },
    { stroke: "#c8434a", fill: "#c8434a0d" },
    { stroke: "#2b75bb", fill: "#2b75bb0d" },
    { stroke: "#198453", fill: "#1984530d" },
  ],
};

const FAINT_EDGE: Record<Theme, string> = { dark: "#4c4f66", light: "#b9bfcc" };

export function categoryOf(id: string, type: "file" | "folder"): Category {
  if (type === "folder") return "folder";
  return languageOf(id) ? "code" : "other";
}

function esc(text: string): string {
  return text
    .replace(/#/g, "#35;")
    .replace(/"/g, "#34;")
    .replace(/</g, "#60;")
    .replace(/>/g, "#62;")
    .replace(/\|/g, "#124;")
    .replace(/[\r\n\t]+/g, " ");
}

export function compileMap(model: MapModel, theme: Theme, direction: Direction = "LR"): Compiled {
  const idFor = new Map<string, string>();
  const pathFor = new Map<string, string>();
  model.nodes.forEach((n, i) => {
    idFor.set(n.id, `n${i}`);
    pathFor.set(`n${i}`, n.id);
  });

  const nodeLine = (n: MapNode) => {
    // Up to four lines: the name, a quieter context line, then the
    // description wrapped onto two (styled after render — SVG labels can't
    // carry per-line markup). Folders get the double-walled "subroutine"
    // shape so they read as containers; externals a hexagon.
    const parts = [esc(n.label)];
    if (n.sub) parts.push(esc(n.sub));
    if (n.desc) parts.push(esc(trimDescription(n.desc)));
    const label = parts.join("<br/>");
    const id = idFor.get(n.id);
    if (n.category === "folder") return `${id}[["${label}"]]`;
    if (n.category === "external") return `${id}{{"${label}"}}`;
    return `${id}["${label}"]`;
  };

  const lines: string[] = [`flowchart ${direction}`];
  const grouped = new Set<string>();
  const groupTones = GROUP_TONES[theme];
  model.groups.forEach((group, gi) => {
    const members = model.nodes.filter((n) => n.group === group.id);
    if (members.length === 0) return;
    lines.push(`subgraph g_${group.id}["${esc(group.label)}"]`);
    members.forEach((n) => {
      grouped.add(n.id);
      lines.push(`  ${nodeLine(n)}`);
    });
    lines.push("end");
    const tone = groupTones[gi % groupTones.length];
    lines.push(`style g_${group.id} fill:${tone.fill},stroke:${tone.stroke},stroke-width:1.5px,color:${tone.stroke}`);
  });
  model.nodes.filter((n) => !grouped.has(n.id)).forEach((n) => lines.push(nodeLine(n)));

  // Solid = the import graph vouches for it; dashed = asserted by the model
  // (an HTTP call, a subprocess, an external service). Faint edges are the
  // remaining real imports, drawn thin and unlabeled.
  const faintIndexes: number[] = [];
  let edgeIndex = 0;
  model.edges.forEach((e) => {
    const a = idFor.get(e.source);
    const b = idFor.get(e.target);
    if (!a || !b) return;
    const arrow = e.backed ? "-->" : "-.->";
    const label = e.label && !e.faint ? `|"${esc(e.label)}"|` : "";
    lines.push(`${a} ${arrow}${label} ${b}`);
    if (e.faint) faintIndexes.push(edgeIndex);
    edgeIndex += 1;
  });
  if (faintIndexes.length > 0) {
    lines.push(`linkStyle ${faintIndexes.join(",")} stroke:${FAINT_EDGE[theme]},stroke-width:1px`);
  }

  // One classDef per distinct tone: the file's kind when it has one, else
  // its category's fallback.
  const categoryTones = CATEGORY_TONES[theme];
  const classes = new Map<string, { tone: Tone; ids: string[] }>();
  model.nodes.forEach((n) => {
    const isFile = n.category === "code" || n.category === "other";
    const kindTone = isFile ? toneOf(n.id, theme) : null;
    const key = kindTone ? `kind_${(labelOf(n.id) ?? "x").replace(/[^A-Za-z0-9]/g, "_")}` : `cat_${n.category}`;
    const entry = classes.get(key) ?? { tone: kindTone ?? categoryTones[n.category], ids: [] };
    entry.ids.push(idFor.get(n.id)!);
    classes.set(key, entry);
  });
  classes.forEach(({ tone, ids }, key) => {
    lines.push(`classDef ${key} fill:${tone.fill},stroke:${tone.stroke},stroke-width:1.5px,color:${tone.text}`);
    lines.push(`class ${ids.join(",")} ${key}`);
  });

  return { source: lines.join("\n"), pathFor };
}
