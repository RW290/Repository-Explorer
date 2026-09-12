import { useMemo, useState } from "react";
import type { Graph, GraphNode } from "./types";
import { centroid, computeLayout, fitScale } from "./layout";
import { DetailPanel } from "./DetailPanel";
import "./Viewer.css";

type Level = "repo" | "folder" | "file";

const PANEL_WIDTH = 360;

function truncate(text: string, max: number): string {
  const firstSentence = text.split(/(?<=[.!?])\s/)[0] ?? text;
  const base = firstSentence.length <= max ? firstSentence : text;
  return base.length > max ? `${base.slice(0, max - 1).trimEnd()}…` : base;
}

interface Props {
  graph: Graph;
  onBack: () => void;
}

export function Viewer({ graph, onBack }: Props) {
  const positions = useMemo(() => computeLayout(graph.nodes), [graph.nodes]);
  const byId = useMemo(() => {
    const map = new Map<string, GraphNode>();
    graph.nodes.forEach((n) => map.set(n.id, n));
    return map;
  }, [graph.nodes]);

  const repoCenter = useMemo(
    () => centroid(graph.nodes.filter((n) => n.parent === null).map((n) => positions[n.id])),
    [graph.nodes, positions],
  );

  const [level, setLevel] = useState<Level>("repo");
  const [activeFolderId, setActiveFolderId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const viewportW = typeof window !== "undefined" ? window.innerWidth : 1200;
  const viewportH = typeof window !== "undefined" ? window.innerHeight : 800;
  // The detail panel opens as soon as we leave repo level (folder/file clicks both select),
  // so frame against the panel-open canvas width for both zoomed levels.
  const canvasW = level === "repo" ? viewportW : viewportW - PANEL_WIDTH;

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
  const scale = level === "repo" ? 1 : level === "folder" ? folderScale : fileScale;

  const tx = canvasW / 2 - target.x * scale;
  const ty = viewportH / 2 - target.y * scale;

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
  }

  function goToFolder() {
    setLevel("folder");
    setSelectedNodeId(activeFolderId);
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

      <div
        className="world"
        style={{ transform: `translate(${tx}px, ${ty}px) scale(${scale})` }}
      >
        {graph.nodes.map((node) => {
          const pos = positions[node.id];
          if (!pos) return null;
          const visible = isVisible(node);
          const isSelected = node.id === selectedNodeId;
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
            </div>
          );
        })}
      </div>

      {selectedNode && (
        <DetailPanel
          node={selectedNode}
          annotations={selectedAnnotations}
          onClose={() => setSelectedNodeId(null)}
        />
      )}
    </div>
  );
}
