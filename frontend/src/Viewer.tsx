import { useMemo, useState } from "react";
import type { Graph, GraphNode } from "./types";
import { computeLayout, fitScale, repoViewCenter } from "./layout";
import { parseRepoUrl } from "./api";
import { DetailPanel } from "./DetailPanel";
import { SourceViewer } from "./SourceViewer";
import { ProjectOverview } from "./ProjectOverview";
import { ThemeToggle, type Theme } from "./ThemeToggle";
import "./Viewer.css";

type Level = "repo" | "folder" | "file";

const PANEL_WIDTH = 360;
// Share of the viewport height left to the folder grid once the compact project
// overview claims the top of the screen. Mirrors the overview's max-height
// in Viewer.css — keep the two in step.
const OVERVIEW_BAND_REMAINDER = 0.55;
// Where the folder grid's center lands vertically at repo level when the
// overview is present: below the compact overview, with room for the graph.
const FOLDER_BAND_CENTER = 0.76;

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

  const [level, setLevel] = useState<Level>("repo");
  const [activeFolderId, setActiveFolderId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [viewingSource, setViewingSource] = useState(false);

  const repo = useMemo(() => parseRepoUrl(graph.repo_url), [graph.repo_url]);
  const hasOverviewCard = Boolean(repo && graph.overview);

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
  const repoScale = useMemo(
    () => fitScale(topFolderPositions, canvasW, folderBandH, 0.6, 1.35),
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

  const target =
    level === "repo"
      ? repoCenter
      : level === "folder"
        ? positions[activeFolderId!]
        : positions[selectedNodeId!];
  const scale = level === "repo" ? repoScale : level === "folder" ? folderScale : fileScale;

  const tx = canvasW / 2 - target.x * scale;
  const focusY = level === "repo" && hasOverviewCard ? viewportH * FOLDER_BAND_CENTER : viewportH / 2;
  const ty = focusY - target.y * scale;

  function isVisible(node: GraphNode): boolean {
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
    setViewingSource(false);
  }

  function goToFolder() {
    setLevel("folder");
    setSelectedNodeId(activeFolderId);
    setViewingSource(false);
  }

  const selectedNode = selectedNodeId ? byId.get(selectedNodeId) ?? null : null;
  const selectedAnnotations = selectedNode
    ? graph.annotations.filter((a) => selectedNode.annotations.includes(a.id))
    : [];

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
      </div>
      <div className={`viewer__tools ${selectedNode ? "viewer__tools--panel-open" : ""}`}>
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
      </div>

      <div
        className="world"
        style={{ transform: `translate(${tx}px, ${ty}px) scale(${scale})` }}
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
              className={`node node--${node.type} ${isSelected ? "node--selected" : ""}`}
              style={{
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

      {selectedNode && (
        <DetailPanel
          node={selectedNode}
          annotations={selectedAnnotations}
          onClose={() => {
            setSelectedNodeId(null);
            setViewingSource(false);
          }}
          onViewSource={
            repo && selectedNode.type === "file" ? () => setViewingSource(true) : undefined
          }
          fileRationale={
            repo && selectedNode.type === "file"
              ? { owner: repo.owner, name: repo.name, path: selectedNode.id }
              : undefined
          }
        />
      )}

      {viewingSource && repo && selectedNode && (
        <SourceViewer
          owner={repo.owner}
          name={repo.name}
          path={selectedNode.id}
          onClose={() => setViewingSource(false)}
        />
      )}

      {hasOverviewCard && level === "repo" && (
        <ProjectOverview owner={repo!.owner} name={repo!.name} overview={graph.overview} />
      )}
    </div>
  );
}
