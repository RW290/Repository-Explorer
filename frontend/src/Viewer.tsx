import { lazy, Suspense, useEffect, useMemo, useState, type CSSProperties } from "react";
import type { Architecture, Graph, GraphNode } from "./types";
import { computeLayout, fitScale, repoViewCenter } from "./layout";
import { buildArchitecture, parseRepoUrl } from "./api";
import { DetailPanel, type ComponentSelection } from "./DetailPanel";
import { labelOf, languageOf, legendFor, toneOf } from "./languages";
import { ProjectOverview } from "./ProjectOverview";
import { Spinner } from "./Spinner";
import { ThemeToggle, type Theme } from "./ThemeToggle";
import "./Viewer.css";

// Lazy: this chunk carries highlight.js (see SourceViewer's default export),
// which would otherwise more than double the initial bundle for a panel most
// visitors never open.
const SourceViewer = lazy(() => import("./SourceViewer"));
// Likewise Mermaid (~1MB) rides with the map view, not the landing page.
const ArchitectureView = lazy(() => import("./ArchitectureView").then((m) => ({ default: m.ArchitectureView })));

type Level = "repo" | "folder" | "file";
// What the repo level shows: the semantic architecture map (Mermaid, groups
// like Frontend / API / LLM) or the literal folder grid. Both drill into the
// same folder → file levels below.
type Mode = "map" | "folders";

const PANEL_WIDTH = 360;
// Share of the viewport height left to the folder grid once the compact project
// overview claims the top of the screen. Mirrors the overview's max-height
// in Viewer.css — keep the two in step.
const OVERVIEW_BAND_REMAINDER = 0.5;
// Where the folder grid's center lands vertically at repo level when the
// overview is present: below the compact overview, with room for the graph.
const FOLDER_BAND_CENTER = 0.78;

function truncate(text: string, max: number): string {
  const firstSentence = text.split(/(?<=[.!?])\s/)[0] ?? text;
  const base = firstSentence.length <= max ? firstSentence : text;
  return base.length > max ? `${base.slice(0, max - 1).trimEnd()}…` : base;
}

interface Props {
  graph: Graph;
  onBack: () => void;
  theme: Theme;
  onToggleTheme: () => void;
}

export function Viewer({ graph, onBack, theme, onToggleTheme }: Props) {
  const positions = useMemo(() => computeLayout(graph.nodes), [graph.nodes]);
  const byId = useMemo(() => {
    const map = new Map<string, GraphNode>();
    graph.nodes.forEach((n) => map.set(n.id, n));
    return map;
  }, [graph.nodes]);

  const repo = useMemo(() => parseRepoUrl(graph.repo_url), [graph.repo_url]);
  const hasOverviewCard = Boolean(repo && graph.overview);

  // The map can exist (shipped with the graph), be buildable (a real repo
  // analyzed before the map stage existed — ask the backend once), or be
  // impossible (fixture without one). Only the last forces the folder grid.
  const [architecture, setArchitecture] = useState<Architecture | null>(graph.architecture);
  const [mapStatus, setMapStatus] = useState<"idle" | "loading" | "error" | "unavailable">(
    graph.architecture ? "idle" : repo ? "loading" : "unavailable",
  );
  const [mapError, setMapError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>(graph.architecture || repo ? "map" : "folders");

  useEffect(() => {
    if (architecture || !repo || mapStatus !== "loading") return;
    let cancelled = false;
    buildArchitecture(repo.owner, repo.name)
      .then((result) => {
        if (cancelled) return;
        if (result) {
          setArchitecture(result);
          setMapStatus("idle");
        } else {
          setMapError("The model couldn't produce a usable map for this repository.");
          setMapStatus("error");
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setMapError(String((e as { message?: string })?.message ?? e));
        setMapStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [architecture, repo, mapStatus]);

  const [level, setLevel] = useState<Level>("repo");
  const [activeFolderId, setActiveFolderId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedComponentId, setSelectedComponentId] = useState<string | null>(null);
  const [viewingSource, setViewingSource] = useState(false);
  const [showOverview, setShowOverview] = useState(false);

  const viewportW = typeof window !== "undefined" ? window.innerWidth : 1200;
  const viewportH = typeof window !== "undefined" ? window.innerHeight : 800;
  // The detail panel opens as soon as we leave repo level (folder/file clicks both select),
  // so frame against the panel-open canvas width for both zoomed levels.
  const canvasW = level === "repo" ? viewportW : viewportW - PANEL_WIDTH;

  const topFolderPositions = useMemo(
    () => graph.nodes.filter((n) => n.parent === null).map((n) => positions[n.id]),
    [graph.nodes, positions],
  );
  const repoCenter = useMemo(() => repoViewCenter(topFolderPositions), [topFolderPositions]);
  // When there's an overview to show, it owns the upper part of the screen as
  // a fixed overlay and the folder grid gets the band beneath it. Fitting the
  // folders to that band (rather than the whole viewport) is what keeps the
  // two from colliding without the overview having to shrink as folders are
  // added.
  const folderBandH = hasOverviewCard ? viewportH * OVERVIEW_BAND_REMAINDER : viewportH;
  // The floor is only there to stop absurd shrinkage, not to hold cards at
  // full size: a 1.05 floor meant six folders couldn't fit the band on a
  // 1280x720 screen and the bottom row ran off-canvas. Roomy screens still
  // land near the 1.35 ceiling on their own.
  const repoScale = useMemo(
    () => fitScale(topFolderPositions, canvasW, folderBandH, 0.5, 1.35),
    [topFolderPositions, canvasW, folderBandH],
  );

  const childrenByParent = useMemo(() => {
    const map = new Map<string, GraphNode[]>();
    graph.nodes.forEach((n) => {
      if (!n.parent) return;
      const siblings = map.get(n.parent) ?? [];
      siblings.push(n);
      map.set(n.parent, siblings);
    });
    return map;
  }, [graph.nodes]);

  const siblingPositions = useMemo(
    () =>
      activeFolderId
        ? graph.nodes.filter((n) => n.parent === activeFolderId).map((n) => positions[n.id])
        : [],
    [graph.nodes, activeFolderId, positions],
  );
  const folderScale = useMemo(
    // No floor above ~repo scale: a folder with many children legitimately needs more screen
    // room than one with few, and forcing a higher minimum just clips content off-screen
    // (card size and spacing both scale together, so shrinking here never causes overlap).
    () => fitScale(siblingPositions, canvasW, viewportH, 0.5, 6),
    [siblingPositions, canvasW, viewportH],
  );
  // Never zoom out below the folder view, but always guarantee the single selected card
  // fits the canvas — a flat multiplier on folderScale could compound past on-screen size
  // when the folder was already sparse enough to hit folderScale's own max.
  const fileScale = useMemo(
    () =>
      selectedNodeId ? fitScale([positions[selectedNodeId]], canvasW, viewportH, folderScale, 8) : folderScale,
    [selectedNodeId, positions, canvasW, viewportH, folderScale],
  );

  // Falls back rather than indexing blindly: any level/selection combination
  // that leaves no position to aim at used to dereference undefined here and
  // take the whole app down with it, which is far too harsh a failure for a
  // camera that could simply stay where it is.
  const target =
    (level === "repo"
      ? repoCenter
      : level === "folder"
        ? positions[activeFolderId ?? ""]
        : positions[selectedNodeId ?? ""]) ?? repoCenter;
  const scale = level === "repo" ? repoScale : level === "folder" ? folderScale : fileScale;

  const tx = canvasW / 2 - target.x * scale;
  const focusY = level === "repo" && hasOverviewCard ? viewportH * FOLDER_BAND_CENTER : viewportH / 2;
  const ty = focusY - target.y * scale;

  const showMap = mode === "map" && level === "repo";

  function isVisible(node: GraphNode): boolean {
    if (showMap) return false;
    if (level === "repo") return node.parent === null;
    return node.parent === activeFolderId;
  }

  function handleNodeClick(node: GraphNode) {
    if (level === "repo" && node.type === "folder") {
      setActiveFolderId(node.id);
      setLevel("folder");
      setSelectedNodeId(node.id);
    } else if (node.parent === activeFolderId) {
      setSelectedNodeId(node.id);
      setLevel("file");
    }
  }

  function goToRepo() {
    setLevel("repo");
    setActiveFolderId(null);
    setSelectedNodeId(null);
    setSelectedComponentId(null);
    setViewingSource(false);
  }

  function goToFolder() {
    setLevel("folder");
    setSelectedNodeId(activeFolderId);
    setViewingSource(false);
  }

  /** One step out: code view → file → folder → repo. */
  function goBackOneStep() {
    if (viewingSource) {
      setViewingSource(false);
    } else if (level === "file" && activeFolderId) {
      goToFolder();
    } else {
      goToRepo();
    }
  }

  /** A click on a map node: select that file/folder without leaving the
   * map. Map node ids are graph node ids. */
  function selectComponent(id: string) {
    const isExternal = id.startsWith("ext:");
    if (!isExternal && !byId.has(id)) return;
    setSelectedComponentId(id);
    setSelectedNodeId(isExternal ? null : id);
    setViewingSource(false);
  }

  /** Jump from a map component into the zoomable explorer at its file or
   * folder. The mode stays "map", so the repo crumb leads back here. */
  function openInExplorer(node: GraphNode) {
    setSelectedComponentId(null);
    setViewingSource(false);
    if (node.type === "folder") {
      setActiveFolderId(node.id);
      setSelectedNodeId(node.id);
      setLevel("folder");
    } else {
      setActiveFolderId(node.parent);
      setSelectedNodeId(node.id);
      setLevel("file");
    }
  }

  const selectedNode = selectedNodeId ? byId.get(selectedNodeId) ?? null : null;
  const selectedAnnotations = selectedNode
    ? graph.annotations.filter((a) => selectedNode.annotations.includes(a.id))
    : [];

  const selection = useMemo<ComponentSelection | undefined>(() => {
    if (!architecture || !selectedComponentId) return undefined;
    const member = architecture.nodes.find((n) => n.id === selectedComponentId);
    if (!member) return undefined;
    // Neighbours are graph nodes, or externals stood in for by a minimal
    // node-shaped record so the flow list can name them.
    const otherFor = (id: string): GraphNode | undefined => {
      const real = byId.get(id);
      if (real) return real;
      const ext = architecture.nodes.find((n) => n.id === id && n.external);
      return ext
        ? { id: ext.label ?? id.replace(/^ext:/, ""), type: "file", parent: null, summary: ext.description ?? "", dependencies: [], annotations: [] }
        : undefined;
    };
    const resolve = (edges: Architecture["edges"], pick: (e: Architecture["edges"][number]) => string) =>
      edges.flatMap((edge) => {
        const other = otherFor(pick(edge));
        return other ? [{ edge, other, otherId: pick(edge) }] : [];
      });
    return {
      external: member.external ? { label: member.label ?? member.id.replace(/^ext:/, ""), description: member.description ?? null } : undefined,
      group: architecture.groups.find((g) => g.id === member.group) ?? null,
      inbound: resolve(architecture.edges.filter((e) => e.target === member.id), (e) => e.source),
      outbound: resolve(architecture.edges.filter((e) => e.source === member.id), (e) => e.target),
    };
  }, [architecture, selectedComponentId, byId]);

  const panelOpen = Boolean(selectedNode || selection?.external);

  function nodeCategory(node: GraphNode): "folder" | "code" | "other" {
    if (node.type === "folder") return "folder";
    return languageOf(node.id) ? "code" : "other";
  }

  /** A file card's colours come from its kind (language, docs, config) as
   * CSS variables, so one rule in Viewer.css styles every kind. */
  function toneVars(node: GraphNode): CSSProperties {
    const tone = node.type === "file" ? toneOf(node.id, theme) : null;
    if (!tone) return {};
    return {
      "--tone-fill": tone.fill,
      "--tone-stroke": tone.stroke,
      "--tone-text": tone.text,
      "--tone-accent": tone.accent,
    } as CSSProperties;
  }

  // The legend names the kinds actually on screen, not a fixed list: at repo
  // level that's the whole repo, inside a folder just that folder's files.
  const legendPaths = graph.nodes
    .filter((n) => n.type === "file" && (level === "repo" || n.parent === activeFolderId))
    .map((n) => n.id);
  const legend = legendFor(legendPaths, theme);

  const canShowMap = mapStatus !== "unavailable";

  return (
    <div className="viewer">
      <div className="breadcrumbs">
        <button onClick={onBack} title="Back to repo picker">
          ←
        </button>
        <span className="breadcrumbs__sep">/</span>
        <button onClick={goToRepo} className={level === "repo" ? "active" : ""}>
          repo
        </button>
        {activeFolderId && (
          <>
            <span className="breadcrumbs__sep">/</span>
            <button onClick={goToFolder} className={level === "folder" ? "active" : ""}>
              {activeFolderId}
            </button>
          </>
        )}
        {level === "file" && selectedNode && (
          <>
            <span className="breadcrumbs__sep">/</span>
            <button className="active">{selectedNode.id.split("/").pop()}</button>
          </>
        )}
        {(level !== "repo" || viewingSource) && (
          <>
            <span className="breadcrumbs__sep">/</span>
            <button className="breadcrumbs__back" onClick={goBackOneStep} title="Back one step">
              ↰ back
            </button>
          </>
        )}
        {canShowMap && level === "repo" && (
          <div className="mode-switch" role="tablist" aria-label="Repo view">
            <button
              role="tab"
              aria-selected={mode === "map"}
              className={mode === "map" ? "active" : ""}
              onClick={() => {
                setMode("map");
                setSelectedNodeId(null);
              }}
              title="Semantic architecture map"
            >
              map
            </button>
            <button
              role="tab"
              aria-selected={mode === "folders"}
              className={mode === "folders" ? "active" : ""}
              onClick={() => {
                setMode("folders");
                setSelectedComponentId(null);
                setSelectedNodeId(null);
                setShowOverview(false);
              }}
              title="Folder-by-folder file grid"
            >
              folders
            </button>
          </div>
        )}
        {showMap && hasOverviewCard && (
          <button
            className={`breadcrumbs__overview ${showOverview ? "active" : ""}`}
            onClick={() => setShowOverview((v) => !v)}
            title="Project overview"
          >
            ◎ overview
          </button>
        )}
      </div>
      <div className={`viewer__tools ${panelOpen ? "viewer__tools--panel-open" : ""}`}>
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
      </div>
      {!showMap && (
        <div className="viewer__legend" aria-label="Node color legend">
          <span><i className="viewer__legend-swatch viewer__legend-swatch--folder" />Folders</span>
          {legend.map((entry) => (
            <span key={entry.label}><i className="viewer__legend-swatch" style={{ background: entry.color }} />{entry.label}</span>
          ))}
          <span><i className="viewer__legend-swatch viewer__legend-swatch--other" />Other</span>
        </div>
      )}

      {showMap && architecture && (
        <Suspense fallback={null}>
          <div className={`arch-frame ${panelOpen ? "arch-frame--panel-open" : ""}`}>
            <ArchitectureView
              architecture={architecture}
              nodes={graph.nodes}
              theme={theme}
              selectedId={selectedComponentId}
              onSelect={selectComponent}
            />
          </div>
        </Suspense>
      )}
      {showMap && !architecture && (
        <div className="arch-placeholder">
          {mapStatus === "loading" ? (
            <>
              <Spinner size="lg" label="Mapping the architecture…" />
              <p>
                One-time step for this repository: the model is grouping its files into components and
                layers. This can take a minute or two.
              </p>
              <button className="arch-placeholder__link" onClick={() => setMode("folders")}>
                Browse folders in the meantime
              </button>
            </>
          ) : (
            <>
              <p className="arch-placeholder__error">{mapError ?? "No architecture map is available."}</p>
              <div className="arch-placeholder__actions">
                {repo && (
                  <button className="detail-panel__view-source" onClick={() => setMapStatus("loading")}>
                    Try again
                  </button>
                )}
                <button className="arch-placeholder__link" onClick={() => setMode("folders")}>
                  Browse folders instead
                </button>
              </div>
            </>
          )}
        </div>
      )}

      <div
        className="world"
        style={{
          transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
          visibility: showMap ? "hidden" : "visible",
        }}
      >
        {graph.nodes.map((node) => {
          const pos = positions[node.id];
          if (!pos) return null;
          const visible = isVisible(node);
          const isSelected = node.id === selectedNodeId;
          const preview = level === "repo" && node.type === "folder" ? childrenByParent.get(node.id) ?? [] : null;
          return (
            <div
              key={node.id}
              className={`node node--${nodeCategory(node)} ${isSelected ? "node--selected" : ""}`}
              data-lang={node.type === "file" ? labelOf(node.id) ?? undefined : undefined}
              style={{
                ...toneVars(node),
                left: pos.x,
                top: pos.y,
                opacity: visible ? 1 : 0,
                pointerEvents: visible ? "auto" : "none",
              }}
              onClick={() => handleNodeClick(node)}
            >
              <div className="node__name">{node.id.split("/").pop()}</div>
              {level !== "repo" && node.type === "file" && (
                <div className="node__summary">{truncate(node.summary, 70)}</div>
              )}
              {preview && preview.length > 0 && (
                <div className="node__preview" aria-hidden="true">
                  {preview.slice(0, 4).map((child) => (
                    <span key={child.id} className="node__preview-chip">
                      {child.id.split("/").pop()}
                    </span>
                  ))}
                  {preview.length > 4 && (
                    <span className="node__preview-chip node__preview-chip--more">+{preview.length - 4} more</span>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {panelOpen && (
        <DetailPanel
          node={selectedNode}
          annotations={selectedAnnotations}
          selection={selection}
          onSelectComponent={selectComponent}
          onOpenInExplorer={selectedNode && selection ? () => openInExplorer(selectedNode) : undefined}
          // Dismissing the panel has to step the camera out too: at file
          // level it's aimed at the very node being deselected, so leaving
          // the level alone would point it at nothing.
          onClose={() => {
            setViewingSource(false);
            if (level === "file" && activeFolderId) goToFolder();
            else goToRepo();
          }}
          onViewSource={
            repo && selectedNode?.type === "file" ? () => setViewingSource(true) : undefined
          }
          fileRationale={
            repo && selectedNode?.type === "file"
              ? { owner: repo.owner, name: repo.name, path: selectedNode.id }
              : undefined
          }
        />
      )}

      {viewingSource && repo && selectedNode && (
        <Suspense fallback={null}>
          <SourceViewer
            owner={repo.owner}
            name={repo.name}
            path={selectedNode.id}
            onClose={() => setViewingSource(false)}
          />
        </Suspense>
      )}

      {hasOverviewCard && level === "repo" && !showMap && (
        <ProjectOverview owner={repo!.owner} name={repo!.name} overview={graph.overview} />
      )}
      {hasOverviewCard && showMap && showOverview && (
        <div className="overview-drawer">
          <button className="overview-drawer__close" onClick={() => setShowOverview(false)} aria-label="Close overview">
            ×
          </button>
          <ProjectOverview
            owner={repo!.owner}
            name={repo!.name}
            overview={graph.overview}
            style={{ position: "static", width: "auto", maxHeight: "none", transform: "none", maskImage: "none", WebkitMaskImage: "none", padding: 0 }}
          />
        </div>
      )}
    </div>
  );
}
