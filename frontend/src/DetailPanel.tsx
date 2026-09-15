import { useState } from "react";
import type { Annotation, GraphNode } from "./types";
import { FileRationalePanel } from "./FileRationalePanel";

interface Props {
  node: GraphNode;
  annotations: Annotation[];
  onClose: () => void;
  onViewSource?: () => void;
  fileRationale?: { owner: string; name: string; path: string };
}

export function DetailPanel({ node, annotations, onClose, onViewSource, fileRationale }: Props) {
  const sorted = [...annotations].sort((a, b) => a.date.localeCompare(b.date));
  const [showFileRationale, setShowFileRationale] = useState(false);

  return (
    <aside className="detail-panel">
      <button className="detail-panel__close" onClick={onClose} aria-label="Close">
        ×
      </button>
      <div className="detail-panel__kind">{node.type}</div>
      <h2 className="detail-panel__title">{node.id}</h2>
      <p className="detail-panel__summary">{node.summary}</p>
      <div className="detail-panel__actions">
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
    </aside>
  );
}
