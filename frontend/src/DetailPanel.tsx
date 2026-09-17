import { useState } from "react";
import type { Annotation, ArchitectureEdge, ArchitectureGroup, GraphNode } from "./types";
import { FileRationalePanel } from "./FileRationalePanel";

/** A node selected on the architecture map: the semantic group it was
 * placed in, and its map neighbours, so the panel can list what flows in
 * and out. */
export interface ComponentSelection {
  /** Set when the selected map node is an external system (no file). */
  external?: { label: string; description: string | null };
  group: ArchitectureGroup | null;
  inbound: { edge: ArchitectureEdge; other: GraphNode; otherId: string }[];
  outbound: { edge: ArchitectureEdge; other: GraphNode; otherId: string }[];
}

interface Props {
  // Null only when an external system is selected on the map: there is no
  // file, so the panel shows the external's label, description and flows.
  node: GraphNode | null;
  annotations: Annotation[];
  onClose: () => void;
  onViewSource?: () => void;
  fileRationale?: { owner: string; name: string; path: string };
  selection?: ComponentSelection;
  /** The analysis is still running, so an empty summary means "not yet". */
  analyzing?: boolean;
  onOpenInExplorer?: () => void;
  onSelectComponent?: (id: string) => void;
}

function FlowList({
  title,
  items,
  direction,
  onSelectComponent,
}: {
  title: string;
  items: ComponentSelection["inbound"];
  direction: "in" | "out";
  onSelectComponent?: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <>
      <h3>{title}</h3>
      <ul className="detail-panel__flows">
        {items.map(({ edge, other, otherId }) => (
          <li key={`${edge.source}-${edge.target}`} className={edge.backed ? "" : "detail-panel__flow--inferred"}>
            <button onClick={() => onSelectComponent?.(otherId)} title={otherId.startsWith("ext:") ? "External system" : otherId}>
              {direction === "in" ? "←" : "→"} {other.id.split("/").pop()}
            </button>
            {edge.label && <span className="detail-panel__flow-label">{edge.label}</span>}
            <span
              className="detail-panel__flow-badge"
              title={edge.backed ? "An import between these files backs this edge" : "Asserted by the model; no import shows it"}
            >
              {edge.backed ? "import" : "inferred"}
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

export function DetailPanel({
  node,
  annotations,
  onClose,
  onViewSource,
  fileRationale,
  selection,
  analyzing = false,
  onOpenInExplorer,
  onSelectComponent,
}: Props) {
  const sorted = [...annotations].sort((a, b) => a.date.localeCompare(b.date));
  const [showFileRationale, setShowFileRationale] = useState(false);

  return (
    <aside className="detail-panel">
      <button className="detail-panel__close" onClick={onClose} aria-label="Close">
        ×
      </button>

      <div className="detail-panel__kind">
        {node ? node.type : "external system"}
        {selection?.group ? ` · in map group “${selection.group.label}”` : ""}
      </div>
      <h2 className="detail-panel__title">{node ? node.id : selection?.external?.label}</h2>
      {node ? (
        node.summary || !analyzing ? (
          <p className="detail-panel__summary">{node.summary}</p>
        ) : (
          <p className="detail-panel__summary detail-panel__summary--pending">
            This file's summary is still being written — it will appear here on its own. Its dependencies below are
            already final, and the source is available now.
          </p>
        )
      ) : (
        <>
          {selection?.external?.description && <p className="detail-panel__summary">{selection.external.description}</p>}
          <p className="detail-panel__empty">Lives outside this repository — a library or service the code talks to — so there's no file to open.</p>
        </>
      )}
      <div className="detail-panel__actions">
        {onOpenInExplorer && (
          <button className="detail-panel__view-source" onClick={onOpenInExplorer}>
            Open in explorer <span aria-hidden="true">→</span>
          </button>
        )}
        {onViewSource && (
          <button className="detail-panel__view-source" onClick={onViewSource}>
            View source &amp; ask why <span aria-hidden="true">→</span>
          </button>
        )}
        {fileRationale && (
          <button className="detail-panel__view-source" onClick={() => setShowFileRationale((v) => !v)}>
            {showFileRationale ? "Hide" : "Why does this file exist?"} <span aria-hidden="true">→</span>
          </button>
        )}
      </div>
      {showFileRationale && fileRationale && <FileRationalePanel {...fileRationale} />}

      {selection && (
        <div className="detail-panel__component">
          {selection.group?.description && <p className="detail-panel__group-desc">{selection.group.description}</p>}
          <FlowList title="Flows in from" items={selection.inbound} direction="in" onSelectComponent={onSelectComponent} />
          <FlowList title="Flows out to" items={selection.outbound} direction="out" onSelectComponent={onSelectComponent} />
          {selection.inbound.length === 0 && selection.outbound.length === 0 && (
            <p className="detail-panel__empty">No flows drawn to or from this node on the map.</p>
          )}
        </div>
      )}

      {node && (
        <>
      <h3>Dependencies</h3>
      {node.dependencies.length === 0 ? (
        <p className="detail-panel__empty">No dependencies recorded.</p>
      ) : (
        <ul className="detail-panel__deps">
          {node.dependencies.map((dep) => (
            <li key={dep}>{dep}</li>
          ))}
        </ul>
      )}

      <h3>History</h3>
      {sorted.length === 0 ? (
        <p className="detail-panel__empty">No historical annotations recorded for this node yet.</p>
      ) : (
        <ul className="detail-panel__annotations">
          {sorted.map((ann) => (
            <li key={ann.id} className={`annotation annotation--${ann.confidence}`}>
              <div className="annotation__meta">
                <span className="annotation__ref">{ann.source_ref}</span>
                <span className="annotation__date">{ann.date}</span>
                <span className={`annotation__confidence annotation__confidence--${ann.confidence}`}>
                  {ann.confidence} confidence
                </span>
              </div>
              <p className="annotation__diff">{ann.diff_summary}</p>
              {ann.rationale_stated && (
                <p className="annotation__rationale annotation__rationale--stated">
                  <strong>Stated:</strong> {ann.rationale_stated}
                </p>
              )}
              {ann.rationale_inferred && (
                <p className="annotation__rationale annotation__rationale--inferred">
                  <strong>Inferred:</strong> {ann.rationale_inferred}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
        </>
      )}
    </aside>
  );
}
