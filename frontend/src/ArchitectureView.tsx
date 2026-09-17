import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Architecture, GraphNode } from "./types";
import type { Theme } from "./ThemeToggle";
import { categoryOf, compileMap, type Direction, type MapEdge, type MapModel, type MapNode } from "./mermaid";
import { MapSkeleton } from "./Skeleton";
import { legendFor } from "./languages";
import "./ArchitectureView.css";

/**
 * The architecture map: a Mermaid flowchart of the explorer's own file and
 * folder nodes, boxed into semantic groups, on a freely pannable/zoomable
 * canvas. Drag to pan, wheel or pinch to zoom, click a node to select it.
 *
 * Mermaid renders to a static SVG, so interaction is layered on top: the SVG
 * sits inside a transformed div (translate + scale), and node clicks are
 * caught by delegation on the rendered `g.node` elements, whose ids Mermaid
 * derives from the ids the compiler assigned. That keeps Mermaid in strict
 * security mode — no click directives, no HTML labels — while still giving
 * the node-based feel of the folder/file explorer.
 */

interface Props {
  architecture: Architecture;
  /** The full graph: map nodes are looked up here for names, kinds and imports. */
  nodes: GraphNode[];
  theme: Theme;
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** A group box (one of the map's semantic sections) was clicked. */
  selectedGroupId?: string | null;
  onSelectGroup?: (groupId: string) => void;
}

interface View {
  x: number;
  y: number;
  scale: number;
}

const FIT_PADDING = 36;
const MAX_FIT_SCALE = 1.3;
const MAX_SCALE = 4;
const MIN_SCALE_OF_FIT = 0.35;
const DRAG_THRESHOLD_PX = 4;
const MOUSE_WHEEL_ZOOM_SPEED = 0.0015;
const PINCH_ZOOM_SPEED = 0.01;
const BUTTON_ZOOM_STEP = 1.25;

// Mermaid is ~1MB; it loads the first time a map is shown, not on landing.
// ELK comes with it: dagre (Mermaid's default) lays subgraphs out as a
// diagonal staircase once edges cross group boundaries, which is exactly
// what an architecture map is made of. ELK's layered algorithm handles
// compound nodes properly — the same reason gitdiagram switched to it.
let mermaidModule: Promise<typeof import("mermaid").default> | null = null;
function loadMermaid() {
  mermaidModule ??= Promise.all([import("mermaid"), import("@mermaid-js/layout-elk")]).then(([m, elk]) => {
    m.default.registerLayoutLoaders(elk.default);
    return m.default;
  });
  return mermaidModule;
}
let renderSeq = 0;

const THEME_VARS: Record<Theme, Record<string, string>> = {
  dark: {
    background: "#0b0c11",
    primaryColor: "#1c1d29",
    primaryBorderColor: "#3c3f55",
    primaryTextColor: "#e8e7f2",
    lineColor: "#8f86e6",
    secondaryColor: "#1c1d29",
    tertiaryColor: "#141520",
    clusterBkg: "rgba(255,255,255,0.03)",
    clusterBorder: "#33364a",
    titleColor: "#c7c1ff",
    edgeLabelBackground: "#14151d",
    fontFamily: "Manrope, -apple-system, BlinkMacSystemFont, sans-serif",
    fontSize: "14px",
  },
  light: {
    background: "#f6f7fb",
    primaryColor: "#ffffff",
    primaryBorderColor: "#c9cfdb",
    primaryTextColor: "#273142",
    lineColor: "#6653d5",
    secondaryColor: "#ffffff",
    tertiaryColor: "#f0f2f7",
    clusterBkg: "rgba(255,255,255,0.5)",
    clusterBorder: "#d3d8e3",
    titleColor: "#4f3eb7",
    edgeLabelBackground: "#f6f7fb",
    fontFamily: "Manrope, -apple-system, BlinkMacSystemFont, sans-serif",
    fontSize: "14px",
  },
};


function mermaidIdOf(el: Element): string | null {
  // Mermaid names each node element `[<renderId>-]flowchart-<ourId>-<n>`;
  // the render-id prefix is present under ELK and absent under dagre.
  const match = /flowchart-(n\d+)-\d+$/.exec(el.id);
  return match ? match[1] : null;
}

/** The summary's opening sentence, for the node's description line. Model
 * summaries often start with "This file …" or the file name — trimmed so the
 * line spends its few words on the point. */
function firstSentence(summary: string): string | null {
  const text = summary.replace(/\s+/g, " ").trim();
  if (!text) return null;
  const sentence = text.split(/(?<=[.!?])\s/)[0] ?? text;
  return sentence.replace(/^(?:this (?:file|module|script|component)|the file|`[^`]+`)\s+/i, "").trim() || sentence;
}

/** Group id for a rendered cluster element. Mermaid names it
 * `[<renderId>-]g_<groupId>`, from the id the compiler assigned. */
function groupIdOf(el: Element, known: Set<string>): string | null {
  const match = /g_([a-z0-9_]+)$/.exec(el.id);
  return match && known.has(match[1]) ? match[1] : null;
}

/** Files a map node stands for: itself, or a folder's direct children. */
function filesOf(node: GraphNode, childrenOf: Map<string, GraphNode[]>): GraphNode[] {
  return node.type === "folder" ? childrenOf.get(node.id) ?? [] : [node];
}

/** Turns the backend's map (real node ids + groups + flows) into what the
 * compiler draws: names and kinds from the graph, plus — optionally — every
 * other import between map nodes, so a reader can check the model's chosen
 * flows against the whole truth. */
function buildModel(architecture: Architecture, nodes: GraphNode[], allImports: boolean): MapModel {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const childrenOf = new Map<string, GraphNode[]>();
  nodes.forEach((n) => {
    if (n.parent) childrenOf.set(n.parent, [...(childrenOf.get(n.parent) ?? []), n]);
  });
  const members = architecture.nodes.flatMap((m) => {
    if (m.external) return [];
    const node = byId.get(m.id);
    return node ? [{ member: m, node }] : [];
  });
  const mapNodes: MapNode[] = members.map(({ member, node }) => ({
    id: node.id,
    label: node.id.split("/").pop() ?? node.id,
    sub:
      node.type === "folder"
        ? `folder · ${(childrenOf.get(node.id) ?? []).length} files`
        : node.id.includes("/")
          ? node.id.slice(0, node.id.lastIndexOf("/") + 1)
          : null,
    desc: firstSentence(node.summary),
    category: categoryOf(node.id, node.type),
    group: member.group,
  }));
  architecture.nodes
    .filter((m) => m.external)
    .forEach((m) =>
      mapNodes.push({
        id: m.id,
        label: m.label ?? m.id.replace(/^ext:/, ""),
        sub: "external",
        desc: m.description ?? null,
        category: "external",
        group: m.group,
      }),
    );
  const edges: MapEdge[] = architecture.edges.map((e) => ({ ...e }));
  if (allImports) {
    const drawn = new Set(edges.flatMap((e) => [`${e.source}>${e.target}`, `${e.target}>${e.source}`]));
    const fileOwner = new Map<string, string>();
    members.forEach(({ node }) => filesOf(node, childrenOf).forEach((f) => fileOwner.set(f.id, node.id)));
    members.forEach(({ node }) => {
      filesOf(node, childrenOf).forEach((f) => {
        f.dependencies.forEach((dep) => {
          const target = fileOwner.get(dep);
          if (!target || target === node.id) return;
          const key = `${node.id}>${target}`;
          if (drawn.has(key)) return;
          drawn.add(key);
          drawn.add(`${target}>${node.id}`);
          edges.push({ source: node.id, target, label: null, backed: true, faint: true });
        });
      });
    });
  }
  return { groups: architecture.groups, nodes: mapNodes, edges };
}

/** Tight bounds of the drawn content in SVG user units. Mermaid's own
 * viewBox comes out far larger than the diagram (stray measurement text and
 * off-canvas label boxes get included), which would fit the map at a
 * fraction of the size it deserves. Measuring the elements that actually
 * make up the picture — clusters, nodes, edges, edge labels — and mapping
 * each through its screen transform sidesteps whatever else is in there. */
function contentBounds(svg: SVGSVGElement): { x: number; y: number; w: number; h: number } | null {
  const toUser = svg.getScreenCTM()?.inverse();
  if (!toUser) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  svg.querySelectorAll<SVGGraphicsElement>("g.cluster, g.node, path.flowchart-link, g.edgeLabel").forEach((el) => {
    let box: DOMRect;
    try {
      box = el.getBBox();
    } catch {
      return;
    }
    if (!box.width && !box.height) return;
    const ctm = el.getScreenCTM();
    if (!ctm) return;
    const m = toUser.multiply(ctm);
    for (const [x, y] of [
      [box.x, box.y],
      [box.x + box.width, box.y],
      [box.x, box.y + box.height],
      [box.x + box.width, box.y + box.height],
    ]) {
      const p = new DOMPoint(x, y).matrixTransform(m);
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x);
      maxY = Math.max(maxY, p.y);
    }
  });
  if (!Number.isFinite(minX)) return null;
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
}

/** Style a node label's lines by role: line 1 is the name, line 2 the
 * context line, anything after is description. Handles both label modes
 * Mermaid may produce: SVG text (one tspan per line) or an HTML
 * foreignObject (text + <br>). */
function markTypeLines(node: SVGGElement) {
  const tspans = node.querySelectorAll("tspan.text-outer-tspan");
  if (tspans.length > 1) {
    tspans.forEach((line, i) => {
      if (i === 1) line.classList.add("arch-type-line");
      else if (i > 1) line.classList.add("arch-desc-line");
    });
    return;
  }
  const label = node.querySelector(".nodeLabel");
  if (!label) return;
  let breaks = 0;
  [...label.childNodes].forEach((child) => {
    if (child.nodeName === "BR") {
      breaks += 1;
      return;
    }
    if (breaks === 0) return;
    const span = document.createElement("span");
    span.className = breaks === 1 ? "arch-type-line" : "arch-desc-line";
    label.replaceChild(span, child);
    span.appendChild(child);
  });
}

export function ArchitectureView({
  architecture,
  nodes,
  theme,
  selectedId,
  onSelect,
  selectedGroupId = null,
  onSelectGroup,
}: Props) {
  const [allImports, setAllImports] = useState(false);
  const model = useMemo(() => buildModel(architecture, nodes, allImports), [architecture, nodes, allImports]);
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const view = useRef<View>({ x: 0, y: 0, scale: 1 });
  const fitScale = useRef(1);
  const content = useRef({ w: 0, h: 0 });
  const dragged = useRef(false);
  // The node under the pointer when it went down. Pointer capture makes the
  // container the target of the later pointerup/click, so the node has to
  // be resolved here, not from the click event.
  const pressedNode = useRef<string | null>(null);
  const pressedGroup = useRef<string | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinchDistance = useRef<number | null>(null);
  const lastSize = useRef<{ w: number; h: number } | null>(null);
  // True until the reader zooms or pans by hand. While it holds, the view is
  // still "the fit", so a resize should produce a new fit; once they've
  // chosen their own view, a resize must not throw it away.
  const untouched = useRef(true);
  // Layout direction, picked once per architecture (see the render effect)
  // and reused on theme / import-toggle re-renders so the view doesn't jump.
  const chosenDirection = useRef<{ arch: Architecture; direction: Direction } | null>(null);
  const pathFor = useRef(new Map<string, string>());

  const [renderState, setRenderState] = useState<"rendering" | "ready" | "error">("rendering");
  const [renderError, setRenderError] = useState<string | null>(null);
  const [renderVersion, setRenderVersion] = useState(0);
  const [zoomLabel, setZoomLabel] = useState("100%");

  const apply = useCallback((animate: boolean) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { x, y, scale } = view.current;
    canvas.classList.toggle("arch__canvas--animate", animate);
    canvas.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
    setZoomLabel(`${Math.round((scale / fitScale.current) * 100)}%`);
  }, []);

  const fit = useCallback(
    (animate: boolean) => {
      const container = containerRef.current;
      const { w, h } = content.current;
      if (!container || !w || !h) return;
      const cw = container.clientWidth;
      const ch = container.clientHeight;
      const scale = Math.min((cw - FIT_PADDING * 2) / w, (ch - FIT_PADDING * 2) / h, MAX_FIT_SCALE);
      fitScale.current = scale;
      view.current = { x: (cw - w * scale) / 2, y: (ch - h * scale) / 2, scale };
      untouched.current = true;
      apply(animate);
    },
    [apply],
  );

  const zoomAt = useCallback(
    (clientX: number, clientY: number, factor: number, animate: boolean) => {
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const px = clientX - rect.left;
      const py = clientY - rect.top;
      const { x, y, scale } = view.current;
      untouched.current = false;
      const next = Math.min(MAX_SCALE, Math.max(fitScale.current * MIN_SCALE_OF_FIT, scale * factor));
      const ratio = next / scale;
      view.current = { x: px - (px - x) * ratio, y: py - (py - y) * ratio, scale: next };
      apply(animate);
    },
    [apply],
  );

  function zoomStep(factor: number) {
    const container = containerRef.current;
    if (!container) return;
    zoomAt(container.clientWidth / 2, container.clientHeight / 2, factor, true);
  }

  // Render (and re-render on theme change: the compiled source carries
  // theme-specific colors).
  useEffect(() => {
    let cancelled = false;
    setRenderState("rendering");
    setRenderError(null);
    loadMermaid()
      .then(async (mermaid) => {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "base",
          themeVariables: THEME_VARS[theme],
          // Both spellings: the ELK renderer reads the top-level flag, the
          // dagre one reads flowchart.htmlLabels.
          htmlLabels: false,
          flowchart: {
            htmlLabels: false,
            curve: "basis",
            nodeSpacing: 40,
            rankSpacing: 56,
            padding: 14,
            useMaxWidth: false,
            defaultRenderer: "elk",
            // Mermaid wraps label lines at this width; wide enough that a
            // name or folder path stays on one line and the description
            // takes two.
            wrappingWidth: 250,
          },
          elk: { mergeEdges: false, nodePlacementStrategy: "BRANDES_KOEPF" },
        });
        const host = hostRef.current;
        const container = containerRef.current;
        if (!host || !container) return;

        // Try both directions the first time an architecture is drawn and
        // keep the one that fits the viewport at the larger scale. Rendering
        // twice costs a couple hundred milliseconds; a map fitted at 30%
        // costs every reader a zoom before they can read anything.
        const remembered = chosenDirection.current?.arch === architecture ? chosenDirection.current.direction : null;
        const candidates: Direction[] = remembered ? [remembered] : ["LR", "TD"];
        const pad = 24;
        let best: { direction: Direction; svg: string; bounds: { x: number; y: number; w: number; h: number }; scale: number } | null = null;
        for (const direction of candidates) {
          const compiled = compileMap(model, theme, direction);
          const { svg } = await mermaid.render(`arch-${++renderSeq}`, compiled.source);
          if (cancelled) return;
          pathFor.current = compiled.pathFor;
          host.innerHTML = svg;
          const probe = host.querySelector("svg");
          if (!probe) continue;
          const vb = probe.viewBox.baseVal;
          const bounds = contentBounds(probe) ?? { x: vb.x, y: vb.y, w: vb.width, h: vb.height };
          const scale = Math.min(
            (container.clientWidth - FIT_PADDING * 2) / (bounds.w + pad * 2),
            (container.clientHeight - FIT_PADDING * 2) / (bounds.h + pad * 2),
          );
          if (!best || scale > best.scale) best = { direction, svg, bounds, scale };
        }
        if (!best) throw new Error("Mermaid produced no SVG.");
        chosenDirection.current = { arch: architecture, direction: best.direction };
        if (host.innerHTML !== best.svg) host.innerHTML = best.svg;
        const svgEl = host.querySelector("svg");
        if (!svgEl) throw new Error("Mermaid produced no SVG.");
        const { bounds } = best;
        const w = bounds.w + pad * 2;
        const h = bounds.h + pad * 2;
        svgEl.setAttribute("viewBox", `${bounds.x - pad} ${bounds.y - pad} ${w} ${h}`);
        svgEl.setAttribute("width", String(w));
        svgEl.setAttribute("height", String(h));
        svgEl.style.width = `${w}px`;
        svgEl.style.height = `${h}px`;
        svgEl.style.maxWidth = "none";
        content.current = { w, h };

        host.querySelectorAll<SVGGElement>("g.node").forEach((g) => {
          const mermaidId = mermaidIdOf(g);
          const path = mermaidId ? pathFor.current.get(mermaidId) : undefined;
          if (!path) return;
          g.dataset.archId = path;
          g.classList.add("arch-node", "arch-node--linked");
          if (path.startsWith("ext:")) g.classList.add("arch-node--external");
          markTypeLines(g);
        });
        // Group boxes are selectable too: a section of the map is a thing a
        // reader can point at and ask about.
        const groupIds = new Set(architecture.groups.map((group) => group.id));
        host.querySelectorAll<SVGGElement>("g.cluster").forEach((g) => {
          const groupId = groupIdOf(g, groupIds);
          if (!groupId) return;
          g.dataset.groupId = groupId;
          g.classList.add("arch-group");
        });
        setRenderState("ready");
        setRenderVersion((v) => v + 1);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setRenderError(String((e as { message?: string })?.message ?? e));
        setRenderState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [architecture, model, theme]);

  // Fit after every fresh render; a theme re-render keeps the current view.
  const fittedFor = useRef<Architecture | null>(null);
  useEffect(() => {
    if (renderState !== "ready") return;
    if (fittedFor.current !== architecture) {
      fittedFor.current = architecture;
      fit(false);
    } else {
      apply(false);
    }
  }, [renderState, renderVersion, architecture, fit, apply]);

  // Selected highlight lives on the SVG element, re-applied after re-render.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    host.querySelectorAll(".arch-node--selected").forEach((el) => el.classList.remove("arch-node--selected"));
    if (selectedId) {
      host.querySelector(`[data-arch-id="${CSS.escape(selectedId)}"]`)?.classList.add("arch-node--selected");
    }
  }, [selectedId, renderVersion]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    host.querySelectorAll(".arch-group--selected").forEach((el) => el.classList.remove("arch-group--selected"));
    if (selectedGroupId) {
      host.querySelector(`[data-group-id="${CSS.escape(selectedGroupId)}"]`)?.classList.add("arch-group--selected");
    }
  }, [selectedGroupId, renderVersion]);

  // Wheel needs a non-passive listener to stop the page scrolling.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const onWheel = (e: WheelEvent) => {
      // Every wheel gesture zooms, at the cursor; moving around is done by
      // dragging. Telling a mouse wheel from a two-finger trackpad scroll
      // can't be done reliably — smooth-scrolling mice report the same small
      // pixel deltas a trackpad does — and guessing wrong made the wheel pan
      // on some hardware. A trackpad pinch arrives as a wheel event with
      // ctrlKey set, at a finer scale, which is what the faster speed is for.
      e.preventDefault();
      const speed = e.ctrlKey || e.metaKey ? PINCH_ZOOM_SPEED : MOUSE_WHEEL_ZOOM_SPEED;
      const delta = e.deltaMode === WheelEvent.DOM_DELTA_LINE ? e.deltaY * 16 : e.deltaY;
      zoomAt(e.clientX, e.clientY, Math.exp(-delta * speed), false);
    };
    container.addEventListener("wheel", onWheel, { passive: false });
    return () => container.removeEventListener("wheel", onWheel);
  }, [apply, zoomAt]);

  // When the container resizes (the detail panel opening, a window resize),
  // keep whatever is at the visible center in place instead of refitting and
  // throwing away the reader's zoom.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(() => {
      const w = container.clientWidth;
      const h = container.clientHeight;
      // With both side panels open the map can get narrower than its own
      // legend plus toolbar; stack them instead of letting them collide.
      container.classList.toggle("arch--narrow", w < 880);
      const last = lastSize.current;
      lastSize.current = { w, h };
      if (!last || !content.current.w) return;
      if (untouched.current) {
        // Still showing the fit: fit the new space (a panel opening can take
        // a third of the width, and re-centring alone would clip the map).
        fit(true);
        return;
      }
      view.current = { ...view.current, x: view.current.x + (w - last.w) / 2, y: view.current.y + (h - last.h) / 2 };
      apply(true);
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [apply, fit]);

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    containerRef.current?.setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    dragged.current = false;
    pressedNode.current = (e.target as Element).closest<SVGGElement>("g.arch-node")?.dataset.archId ?? null;
    // Nodes are drawn in their own layer, not inside the cluster element, so
    // a press on a node never reaches this.
    pressedGroup.current = pressedNode.current
      ? null
      : (e.target as Element).closest<SVGGElement>("g.arch-group")?.dataset.groupId ?? null;
    containerRef.current?.classList.add("arch--dragging");
  }

  function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const prev = pointers.current.get(e.pointerId);
    if (!prev) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinchDistance.current !== null && pinchDistance.current > 0) {
        zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, distance / pinchDistance.current, false);
      }
      pinchDistance.current = distance;
      dragged.current = true;
      return;
    }

    const dx = e.clientX - prev.x;
    const dy = e.clientY - prev.y;
    if (!dragged.current && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
    dragged.current = true;
    untouched.current = false;
    view.current = { ...view.current, x: view.current.x + dx, y: view.current.y + dy };
    apply(false);
  }

  function onPointerUp(e: React.PointerEvent<HTMLDivElement>) {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinchDistance.current = null;
    if (pointers.current.size === 0) containerRef.current?.classList.remove("arch--dragging");
    // A press-and-release on a node, without dragging, is a selection. A
    // drag that happens to end over a node is not.
    if (e.type === "pointerup" && !dragged.current) {
      if (pressedNode.current) onSelect(pressedNode.current);
      else if (pressedGroup.current) onSelectGroup?.(pressedGroup.current);
    }
    pressedNode.current = null;
    pressedGroup.current = null;
  }

  return (
    <div
      ref={containerRef}
      className="arch"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={(e) => {
        if (!(e.target as Element).closest("g.arch-node, g.arch-group")) fit(true);
      }}
    >
      <div ref={canvasRef} className="arch__canvas">
        <div ref={hostRef} className="arch__host" />
      </div>

      {renderState === "rendering" && <MapSkeleton label="Drawing the map…" />}
      {renderState === "error" && (
        <div className="arch__status arch__status--error">Couldn't draw the map: {renderError}</div>
      )}

      <div className="arch__toolbar" onPointerDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()}>
        <button onClick={() => zoomStep(BUTTON_ZOOM_STEP)} title="Zoom in" aria-label="Zoom in">+</button>
        <span className="arch__zoom" aria-live="polite">{zoomLabel}</span>
        <button onClick={() => zoomStep(1 / BUTTON_ZOOM_STEP)} title="Zoom out" aria-label="Zoom out">−</button>
        <button onClick={() => fit(true)} title="Fit to screen (or double-click the canvas)" className="arch__fit">
          fit
        </button>
        <span className="arch__toolbar-sep" />
        <button
          className={`arch__fit ${allImports ? "arch__toggle--on" : ""}`}
          onClick={() => setAllImports((v) => !v)}
          title="Also draw every other import between these nodes, thin and unlabeled"
          aria-pressed={allImports}
        >
          all imports
        </button>
      </div>

      <div className="arch__legend" aria-label="Map legend">
        {model.nodes.some((n) => n.category === "folder") && (
          <span><i className="viewer__legend-swatch viewer__legend-swatch--folder" />Folders</span>
        )}
        {legendFor(model.nodes.filter((n) => n.category === "code" || n.category === "other").map((n) => n.id), theme, 4).map((entry) => (
          <span key={entry.label}><i className="viewer__legend-swatch" style={{ background: entry.color }} />{entry.label}</span>
        ))}
        {model.nodes.some((n) => n.category === "external") && (
          <span><i className="viewer__legend-swatch viewer__legend-swatch--external" />External</span>
        )}
        <span className="arch__legend-gap"><i className="arch__legend-line" />import-verified flow</span>
        <span><i className="arch__legend-line arch__legend-line--dashed" />inferred flow</span>
        {allImports && <span><i className="arch__legend-line arch__legend-line--faint" />other import</span>}
        <span className="arch__legend-hint">drag to move · scroll or pinch to zoom</span>
      </div>
    </div>
  );
}
