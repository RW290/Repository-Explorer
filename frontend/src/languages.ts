// What kind of file is this, and what colour is it?
//
// The code extensions mirror what the backend's import resolver understands
// (backend/app/imports.py) plus a few it doesn't draw edges for — these are
// the files worth marking as source in the viewer. Docs and config get a
// kind too, so "everything that isn't Python" isn't one grey mass.
//
// Colours are generated from a hue per kind rather than hand-picked per
// theme: one lightness/saturation recipe for dark and one for light keeps
// every kind legible in both without 40 hand-tuned hex triples. Hues are
// spread around the wheel, steering clear of the folder blue (~210).

export type Theme = "dark" | "light";

interface Kind {
  label: string;
  hue: number;
  /** Saturation multiplier; config and docs are deliberately quieter. */
  chroma?: number;
  code: boolean;
}

const k = (label: string, hue: number, code = true, chroma = 1): Kind => ({ label, hue, code, chroma });

const PYTHON = k("Python", 150);
const TYPESCRIPT = k("TypeScript", 236);
const JAVASCRIPT = k("JavaScript", 50);
const CPP = k("C++", 322);
const SHELL = k("Shell", 84);
const ELIXIR = k("Elixir", 278);
const DOCS = k("Docs", 38, false, 0.55);
const CONFIG = k("Config", 222, false, 0.22);

const KIND_BY_EXTENSION: Record<string, Kind> = {
  py: PYTHON, pyi: PYTHON,
  ts: TYPESCRIPT, tsx: TYPESCRIPT, mts: TYPESCRIPT, cts: TYPESCRIPT,
  js: JAVASCRIPT, jsx: JAVASCRIPT, mjs: JAVASCRIPT, cjs: JAVASCRIPT,
  vue: k("Vue", 160), svelte: k("Svelte", 14), astro: k("Astro", 262),
  go: k("Go", 190), rs: k("Rust", 22),
  java: k("Java", 4), kt: k("Kotlin", 268), scala: k("Scala", 352), groovy: k("Groovy", 176),
  c: k("C", 104), h: k("C", 104),
  cc: CPP, cpp: CPP, cxx: CPP, hpp: CPP, hh: CPP, hxx: CPP,
  m: k("Objective-C", 196), mm: k("Objective-C", 196), ino: k("Arduino", 182),
  rb: k("Ruby", 342), php: k("PHP", 250), dart: k("Dart", 170),
  cs: k("C#", 288), swift: k("Swift", 16), sql: k("SQL", 62), lua: k("Lua", 244),
  sh: SHELL, bash: SHELL, zsh: SHELL, ex: ELIXIR, exs: ELIXIR,
  css: k("CSS", 304), scss: k("SCSS", 330), sass: k("Sass", 330), less: k("Less", 310),
  html: k("HTML", 28), htm: k("HTML", 28),
  md: DOCS, mdx: DOCS, rst: DOCS, txt: DOCS, adoc: DOCS,
  json: CONFIG, yaml: CONFIG, yml: CONFIG, toml: CONFIG, ini: CONFIG, cfg: CONFIG, xml: CONFIG, env: CONFIG, lock: CONFIG,
};

function kindOf(path: string): Kind | null {
  const name = path.split("/").pop()?.toLowerCase() ?? "";
  if (name === "dockerfile" || name === "makefile") return CONFIG;
  // `.txt` is docs by default, but pip requirement files are configuration.
  if (/^(requirements|constraints)[\w.-]*\.txt$/.test(name)) return CONFIG;
  const ext = name.includes(".") ? name.split(".").pop()! : "";
  return KIND_BY_EXTENSION[ext] ?? null;
}

/** Display name of the file's kind ("TypeScript", "Docs", …), or null. */
export function labelOf(path: string): string | null {
  return kindOf(path)?.label ?? null;
}

/** Language name for a *source* file; null for docs, config and the rest. */
export function languageOf(path: string): string | null {
  const kind = kindOf(path);
  return kind?.code ? kind.label : null;
}

export interface Tone {
  fill: string;
  stroke: string;
  text: string;
  /** The small uppercase label on a card; also the legend swatch. */
  accent: string;
}

function hslToHex(h: number, s: number, l: number): string {
  // Hex, not hsl(): these also go into Mermaid `classDef` lines, whose
  // parser can't take parentheses.
  const a = (s / 100) * Math.min(l / 100, 1 - l / 100);
  const channel = (n: number) => {
    const t = (n + h / 30) % 12;
    const value = l / 100 - a * Math.max(-1, Math.min(t - 3, 9 - t, 1));
    return Math.round(255 * value).toString(16).padStart(2, "0");
  };
  return `#${channel(0)}${channel(8)}${channel(4)}`;
}

const toneCache = new Map<string, Tone>();

/** Colours for a file's kind in the given theme, or null if it has none. */
export function toneOf(path: string, theme: Theme): Tone | null {
  const kind = kindOf(path);
  if (!kind) return null;
  const key = `${kind.label}:${theme}`;
  let tone = toneCache.get(key);
  if (!tone) {
    const c = kind.chroma ?? 1;
    // Yellows read lighter than blues at equal lightness; nudge them down so
    // dark-theme text keeps its contrast.
    const warm = kind.hue > 40 && kind.hue < 100 ? -4 : 0;
    tone =
      theme === "dark"
        ? {
            fill: hslToHex(kind.hue, 42 * c, 17 + warm / 2),
            stroke: hslToHex(kind.hue, 52 * c, 44 + warm),
            text: hslToHex(kind.hue, 45 * c, 93),
            accent: hslToHex(kind.hue, 72 * c, 70),
          }
        : {
            fill: hslToHex(kind.hue, 78 * c, 95),
            stroke: hslToHex(kind.hue, 52 * c, 66 + warm),
            text: hslToHex(kind.hue, 48 * c, 18),
            accent: hslToHex(kind.hue, 62 * c, 34 + warm),
          };
    toneCache.set(key, tone);
  }
  return tone;
}

/** The kinds present among `paths`, most common first, for a legend. */
export function legendFor(paths: string[], theme: Theme, max = 5): { label: string; color: string }[] {
  const counts = new Map<string, { count: number; path: string }>();
  paths.forEach((path) => {
    const label = labelOf(path);
    if (!label) return;
    const entry = counts.get(label);
    if (entry) entry.count += 1;
    else counts.set(label, { count: 1, path });
  });
  return [...counts.entries()]
    .sort((a, b) => b[1].count - a[1].count)
    .slice(0, max)
    .map(([label, { path }]) => ({ label, color: toneOf(path, theme)!.accent }));
}
