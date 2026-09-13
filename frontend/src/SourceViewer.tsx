import { useEffect, useMemo, useState } from "react";
import { askWhy, fetchFileContent, fetchRationales } from "./api";
import type { FileContent, LineRationale } from "./types";
import { Spinner } from "./Spinner";
import "./SourceViewer.css";

interface Props {
  owner: string;
  name: string;
  path: string;
  onClose: () => void;
}

export function SourceViewer({ owner, name, path, onClose }: Props) {
  const [file, setFile] = useState<FileContent | null>(null);
  const [rationales, setRationales] = useState<LineRationale[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [selStart, setSelStart] = useState<number | null>(null);
  const [selEnd, setSelEnd] = useState<number | null>(null);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [activeRationale, setActiveRationale] = useState<LineRationale | null>(null);

  useEffect(() => {
    setLoading(true);
    setLoadError(null);
    setSelStart(null);
    setSelEnd(null);
    setActiveRationale(null);
    Promise.all([fetchFileContent(owner, name, path), fetchRationales(owner, name, path)])
      .then(([f, r]) => {
        setFile(f);
        setRationales(r);
      })
      .catch((e) => setLoadError(String(e.message ?? e)))
      .finally(() => setLoading(false));
  }, [owner, name, path]);

  // Mirrors Python's str.splitlines(), which the backend uses for all line
  // numbering: a single trailing newline doesn't produce a phantom extra
  // line, unlike a plain split("\n").
  const lines = useMemo(() => (file ? file.content.replace(/\n$/, "").split("\n") : []), [file]);

  function rationaleForLine(lineNo: number): LineRationale | undefined {
    return rationales.find((r) => lineNo >= r.start_line && lineNo <= r.end_line);
  }

  function handleLineClick(lineNo: number, shiftKey: boolean) {
    setActiveRationale(null);
    setAskError(null);
    if (shiftKey && selStart !== null) {
      setSelEnd(lineNo);
    } else {
      setSelStart(lineNo);
      setSelEnd(lineNo);
    }
  }

  function clearSelection() {
    setSelStart(null);
    setSelEnd(null);
    setQuestion("");
    setAskError(null);
  }

  function isSelected(lineNo: number): boolean {
    if (selStart === null || selEnd === null) return false;
    return lineNo >= Math.min(selStart, selEnd) && lineNo <= Math.max(selStart, selEnd);
  }

  async function submitQuestion() {
    if (selStart === null || selEnd === null) return;
    const lo = Math.min(selStart, selEnd);
    const hi = Math.max(selStart, selEnd);
    setAsking(true);
    setAskError(null);
    try {
      const entry = await askWhy(owner, name, path, lo, hi, question.trim() || undefined);
      setRationales((prev) => [...prev, entry]);
      setActiveRationale(entry);
      clearSelection();
    } catch (e: unknown) {
      setAskError(String((e as { message?: string })?.message ?? e));
    } finally {
      setAsking(false);
    }
  }

  const selectionLabel =
    selStart !== null && selEnd !== null
      ? Math.min(selStart, selEnd) === Math.max(selStart, selEnd)
        ? `Line ${selStart}`
        : `Lines ${Math.min(selStart, selEnd)}–${Math.max(selStart, selEnd)}`
      : null;

  return (
    <div className="source-viewer__backdrop" onClick={onClose}>
      <div className="source-viewer" onClick={(e) => e.stopPropagation()}>
        <div className="source-viewer__header">
          <span className="source-viewer__path">{path}</span>
          <button className="source-viewer__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        {loading && (
          <div className="source-viewer__loading">
            <Spinner label="Loading file…" />
          </div>
        )}
        {loadError && <div className="source-viewer__error">{loadError}</div>}

        {file && (
          <div className="source-viewer__body">
            <div className="source-viewer__code">
              {lines.map((text, i) => {
                const lineNo = i + 1;
                const rationale = rationaleForLine(lineNo);
                return (
                  <div key={lineNo} className={`source-line ${isSelected(lineNo) ? "source-line--selected" : ""}`}>
                    <button
                      className="source-line__no"
                      onClick={(e) => handleLineClick(lineNo, e.shiftKey)}
                      title="Click to select a line, shift-click to extend the range"
                    >
                      {lineNo}
                    </button>
                    <code className="source-line__text">{text || " "}</code>
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
              {selectionLabel && (
                <div className="ask-why">
                  <div className="ask-why__label">{selectionLabel} selected</div>
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
                  <p className="rationale-thread__a">{activeRationale.answer}</p>
                </div>
              )}

              {!selectionLabel && !activeRationale && (
                <div className="source-viewer__hint">
                  <p>Click a line number to select it (shift-click to extend a range), then ask why it's there.</p>
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
