import { lazy, Suspense, useEffect, useMemo, useState, type CSSProperties } from "react";
import type { AnalysisProgress as Progress, Architecture, Graph, GraphNode } from "./types";
import { AnalysisProgress } from "./AnalysisProgress";
import { ArchitectureAsk, type AskFocus } from "./ArchitectureAsk";
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
  /** Non-null while the analysis that produced `graph` is still running:
   * `graph` is then partial (structure first, summaries and the map still to
   * come) and is replaced by a fuller one on every poll. */
  live?: Progress | null;
}

export function Viewer({ graph, onBack, theme, onToggleTheme, live = null }: Props) {
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
  // "pending": an analysis is still running and the map is its last stage,
  // so there's nothing to ask the backend for yet.
  const [mapStatus, setMapStatus] = useState<"idle" | "loading" | "pending" | "error" | "unavailable">(
    graph.architecture ? "idle" : live ? "pending" : repo ? "loading" : "unavailable",
  );
  const [mapError, setMapError] = useState<string | null>(null);
  // A live analysis opens on the folder grid: that's the part that exists.
  const [mode, setMode] = useState<Mode>(graph.architecture ? "map" : live ? "folders" : repo ? "map" : "folders");

  // The graph prop is replaced on every poll of a live analysis. When the map
  // finally arrives, adopt it — and move to it if the reader is still sitting
  // at the top level, since it's the view they'd have landed on.
  useEffect(() => {
    if (!graph.architecture || graph.architecture === architecture) return;
    setArchitecture(graph.architecture);
    setMapStatus("idle");
    if (mapStatus === "pending" && level === "repo" && !selectedNodeId) setMode("map");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph.architecture]);
  // Analysis finished without producing a map: fall back to asking for one.
  useEffect(() => {
    if (!live && mapStatus === "pending") setMapStatus(repo ? "loading" : "unavailable");
  }, [live, mapStatus, repo]);

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
  // The architecture Ask panel, and the map section (group box) it may be
  // focused on. A selected node takes precedence over a selected group.
  const [askOpen, setAskOpen] = useState(false);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);

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
  // full size: a floor near 1.0 can't fit six folders into the band on a
  // 1280x720 screen, and the bottom row runs off-canvas. Roomy screens land
  // near the 1.35 ceiling on their own.
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

  // Falls back rather than indexing blindly: a level/selection combination
  // that leaves no position to aim at would otherwise dereference undefined
  // and take the whole app down, which is far too harsh a failure for a
  // camera that can simply stay where it is.
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
    setSelectedGroupId(null);
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
    setSelectedGroupId(null);
    setSelectedComponentId(id);
    setSelectedNodeId(isExternal ? null : id);
    setViewingSource(false);
  }

  /** A click on one of the map's group boxes: select that section and open
   * the Ask panel on it — a section has no file to show, so asking about it
   * is what selecting it is for. */
  function selectGroup(groupId: string) {
    setSelectedGroupId(groupId);
    setSelectedComponentId(null);
    setSelectedNodeId(null);
    setViewingSource(false);
    setShowOverview(false);
    setAskOpen(true);
  }

  function clearMapSelection() {
    setSelectedGroupId(null);
    setSelectedComponentId(null);
    setSelectedNodeId(null);
  }

  /** Switch to the map from anywhere, keeping the reader's place: whatever
   * file or folder they were on is selected on the map if it's a member
   * (or, for a file that isn't, its folder if that is). */
  function goToMap() {
    if (mapStatus === "pending") return;
    const memberIds = new Set((architecture?.nodes ?? []).map((n) => n.id));
    const current = selectedNodeId ? byId.get(selectedNodeId) : undefined;
    const onMap = current
      ? memberIds.has(current.id)
        ? current.id
        : current.parent && memberIds.has(current.parent)
          ? current.parent
          : null
      : null;
    setMode("map");
    setLevel("repo");
    setActiveFolderId(null);
    setViewingSource(false);
    setSelectedComponentId(onMap);
    setSelectedNodeId(onMap);
  }

  /** Switch to the folder explorer from the map. With a file or folder
   * selected on the map, land on that same node; otherwise the folder grid. */
  function goToFolders() {
    setShowOverview(false);
    setMode("folders");
    if (!showMap) return;
    const current = selectedNodeId ? byId.get(selectedNodeId) : undefined;
    if (current) {
      openInExplorer(current);
    } else {
      setSelectedComponentId(null);
      setSelectedNodeId(null);
    }
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

  // What "this" means in an architecture question: the selected node, else
  // the selected section, else nothing (the whole map).
  const askFocus = useMemo<AskFocus | null>(() => {
    if (!architecture) return null;
    if (selectedComponentId) {
      const member = architecture.nodes.find((n) => n.id === selectedComponentId);
      if (member) return { kind: "node", id: member.id, label: member.external ? member.label ?? member.id : member.id };
    }
    if (selectedGroupId) {
      const group = architecture.groups.find((g) => g.id === selectedGroupId);
      if (group) return { kind: "group", id: group.id, label: group.label };
    }
    return null;
  }, [architecture, selectedComponentId, selectedGroupId]);

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

  // "M" flips between the two views from anywhere, unless the reader is
  // typing a question or reading source.
  useEffect(() => {
    if (!canShowMap) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== "m" || e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
      if (viewingSource) return;
      e.preventDefault();
      if (showMap) goToFolders();
      else goToMap();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="viewer">
      <div className={`viewer__topbar ${panelOpen ? "viewer__topbar--panel-open" : ""}`}>
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
        {showMap && repo && architecture && (
          <button
            className={`breadcrumbs__ask ${askOpen ? "active" : ""}`}
            onClick={() => {
              setAskOpen((v) => !v);
              setShowOverview(false);
            }}
            title="Ask a question about this architecture"
          >
            ✦ ask
          </button>
        )}
        {showMap && hasOverviewCard && (
          <button
            className={`breadcrumbs__overview ${showOverview ? "active" : ""}`}
            onClick={() => {
              setShowOverview((v) => !v);
              setAskOpen(false);
            }}
            title="Project overview"
          >
            ◎ overview
          </button>
        )}
      </div>
      {canShowMap && (
        <div
          className="view-switch"
          role="tablist"
          aria-label="Switch between the architecture map and the folder explorer"
        >
          <button
            role="tab"
            aria-selected={showMap}
            className={showMap ? "active" : ""}
            disabled={mapStatus === "pending"}
            onClick={goToMap}
            title={
              mapStatus === "pending"
                ? "The map is built last — it unlocks when the analysis finishes"
                : "Architecture map: files grouped into semantic layers, with the flows between them (M)"
            }
          >
            <span className="view-switch__icon" aria-hidden="true">◈</span>
            Map{mapStatus === "pending" ? " …" : ""}
          </button>
          <button
            role="tab"
            aria-selected={!showMap}
            className={!showMap ? "active" : ""}
            onClick={goToFolders}
            title="Folder explorer: every folder and file, zoom in level by level (M)"
          >
            <span className="view-switch__icon" aria-hidden="true">▦</span>
            Folders
          </button>
          <kbd className="view-switch__key" aria-hidden="true">M</kbd>
        </div>
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
          <div
            className={`arch-frame ${panelOpen ? "arch-frame--panel-open" : ""} ${
              askOpen && repo ? "arch-frame--ask-open" : ""
            }`}
          >
            <ArchitectureView
              architecture={architecture}
              nodes={graph.nodes}
              theme={theme}
              selectedId={selectedComponentId}
              onSelect={selectComponent}
              selectedGroupId={selectedGroupId}
              onSelectGroup={repo ? selectGroup : undefined}
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
              {level !== "repo" && node.type === "file" && node.summary && (
                <div className="node__summary">{truncate(node.summary, 70)}</div>
              )}
              {visible && level !== "repo" && node.type === "file" && !node.summary && live && (
                <div className="node__summary node__summary--pending" aria-label="Summary being written">
                  <span /><span />
                </div>
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
          analyzing={Boolean(live)}
          node={selectedNode}
          annotations={selectedAnnotations}
          selection={selection}
          onSelectComponent={selectComponent}
          onOpenInExplorer={selectedNode && selection ? () => openInExplorer(selectedNode) : undefined}
          onAskAbout={
            showMap && repo && selection
              ? () => {
                  setShowOverview(false);
                  setAskOpen(true);
                }
              : undefined
          }
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

      {showMap && askOpen && repo && architecture && (
        <ArchitectureAsk
          owner={repo.owner}
          name={repo.name}
          focus={askFocus}
          onClearFocus={clearMapSelection}
          onShowFocus={(kind, id) => (kind === "group" ? selectGroup(id) : selectComponent(id))}
          onClose={() => setAskOpen(false)}
        />
      )}

      {live && !viewingSource && <AnalysisProgress progress={live} variant="hud" compact={level !== "repo"} />}

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
