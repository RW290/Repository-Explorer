import { useEffect, useMemo, useState } from "react";
import { askWhy, fetchFileContent, fetchRationales } from "./api";
import type { FileContent, LineRationale } from "./types";
import { RichText } from "./markdown";
import { highlightLines } from "./highlight";
import { Spinner } from "./Spinner";
import "./SourceViewer.css";

interface Props {
  owner: string;
  name: string;
  path: string;
  onClose: () => void;
}

interface Selection {
  startLine: number;
  endLine: number;
  text: string;
}

/** Line number for a DOM node inside the code area, via its nearest
 * ancestor carrying data-line. */
function lineOf(node: Node | null): number | null {
  const element = node instanceof Element ? node : node?.parentElement ?? null;
  const line = element?.closest<HTMLElement>("[data-line]");
  return line ? Number(line.dataset.line) : null;
}

export function SourceViewer({ owner, name, path, onClose }: Props) {
  const [file, setFile] = useState<FileContent | null>(null);
  const [rationales, setRationales] = useState<LineRationale[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [selection, setSelection] = useState<Selection | null>(null);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [activeRationale, setActiveRationale] = useState<LineRationale | null>(null);

  useEffect(() => {
    setLoading(true);
    setLoadError(null);
    setSelection(null);
    setActiveRationale(null);
    Promise.all([fetchFileContent(owner, name, path), fetchRationales(owner, name, path)])
      .then(([f, r]) => {
        setFile(f);
        setRationales(r);
      })
      .catch((e) => setLoadError(String(e.message ?? e)))
      .finally(() => setLoading(false));
  }, [owner, name, path]);

  const lines = useMemo(() => (file ? highlightLines(file.content, path) : []), [file, path]);

  function rationaleForLine(lineNo: number): LineRationale | undefined {
    return rationales.find((r) => lineNo >= r.start_line && lineNo <= r.end_line);
  }

  /** Reads whatever the user just highlighted in the code area and turns it
   * into a line range plus the exact selected text. */
  function captureSelection() {
    const domSelection = window.getSelection();
    if (!domSelection || domSelection.isCollapsed) return;
    const text = domSelection.toString();
    if (!text.trim()) return;

    const anchor = lineOf(domSelection.anchorNode);
    const focus = lineOf(domSelection.focusNode);
    if (anchor === null || focus === null) return;

    setActiveRationale(null);
    setAskError(null);
    setSelection({
      startLine: Math.min(anchor, focus),
      endLine: Math.max(anchor, focus),
      text,
    });
  }

  function clearSelection() {
    setSelection(null);
    setQuestion("");
    setAskError(null);
    window.getSelection()?.removeAllRanges();
  }

  function isSelected(lineNo: number): boolean {
    return selection !== null && lineNo >= selection.startLine && lineNo <= selection.endLine;
  }

  async function submitQuestion() {
    if (!selection) return;
    setAsking(true);
    setAskError(null);
    try {
      const entry = await askWhy(
        owner,
        name,
        path,
        selection.startLine,
        selection.endLine,
        question.trim() || undefined,
        selection.text,
      );
      setRationales((prev) => [...prev, entry]);
      setActiveRationale(entry);
      clearSelection();
    } catch (e: unknown) {
      setAskError(String((e as { message?: string })?.message ?? e));
    } finally {
      setAsking(false);
    }
  }

  const selectionLabel = selection
    ? selection.startLine === selection.endLine
      ? `Line ${selection.startLine}`
      : `Lines ${selection.startLine}–${selection.endLine}`
    : null;

  return (
    <div className="source-viewer__backdrop" onClick={onClose}>
      <div className="source-viewer" onClick={(e) => e.stopPropagation()}>
        <div className="source-viewer__header">
          <button className="source-viewer__back" onClick={onClose}>
            ← Back
          </button>
          <span className="source-viewer__path">{path}</span>
          <button className="source-viewer__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        {loading && (
          <div className="source-viewer__loading">
            <Spinner size="lg" label="Loading file…" />
          </div>
        )}
        {loadError && <div className="source-viewer__error">{loadError}</div>}

        {file && (
          <div className="source-viewer__body">
            <div className="source-viewer__code" onMouseUp={captureSelection}>
              {lines.map((html, i) => {
                const lineNo = i + 1;
                const rationale = rationaleForLine(lineNo);
                return (
                  <div
                    key={lineNo}
                    data-line={lineNo}
                    className={`source-line ${isSelected(lineNo) ? "source-line--selected" : ""}`}
                  >
                    <span className="source-line__no" aria-hidden="true">
                      {lineNo}
                    </span>
                    <code className="source-line__text" dangerouslySetInnerHTML={{ __html: html || " " }} />
                    {rationale && (
                      <button
                        className="source-line__marker"
                        title="A rationale is recorded for this line"
                        onClick={() => setActiveRationale(rationale)}
                      >
                        ●
                      </button>
                    )}
                  </div>
                );
              })}
            </div>

            <aside className="source-viewer__panel">
              {selection && (
                <div className="ask-why">
                  <div className="ask-why__label">{selectionLabel} selected</div>
                  <pre className="ask-why__excerpt">{selection.text}</pre>
                  <textarea
                    className="ask-why__input"
                    placeholder="Why is this used? (optional — defaults to that question)"
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                  />
                  <div className="ask-why__actions">
                    <button onClick={submitQuestion} disabled={asking}>
                      {asking ? "Asking…" : "Ask why"}
                    </button>
                    <button className="ask-why__cancel" onClick={clearSelection} disabled={asking}>
                      Cancel
                    </button>
                  </div>
                  {askError && <p className="ask-why__error">{askError}</p>}
                </div>
              )}

              {activeRationale && (
                <div className="rationale-thread">
                  <div className="rationale-thread__meta">
                    Lines {activeRationale.start_line}
                    {activeRationale.end_line !== activeRationale.start_line ? `–${activeRationale.end_line}` : ""}
                  </div>
                  <p className="rationale-thread__q">{activeRationale.question}</p>
                  <RichText className="rationale-thread__a" text={activeRationale.answer} />
                </div>
              )}

              {!selection && !activeRationale && (
                <div className="source-viewer__hint">
                  <p>Highlight any code on the left — a word, an expression, a whole block — then ask why it's there.</p>
                  {rationales.length > 0 && (
                    <>
                      <h4>Recorded so far</h4>
                      <ul className="rationale-list">
                        {rationales.map((r) => (
                          <li key={r.id}>
                            <button onClick={() => setActiveRationale(r)}>
                              Lines {r.start_line}
                              {r.end_line !== r.start_line ? `–${r.end_line}` : ""}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              )}
            </aside>
          </div>
        )}
      </div>
    </div>
  );
}

// Default export as well, so Viewer can lazy-load this chunk: it pulls in
// highlight.js, which has no business sitting in the initial bundle for the
// many visitors who never open a file's source.
export default SourceViewer;
