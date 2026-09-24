import { useEffect, useMemo, useState } from "react";
import { askWhy, ensureSymbolExplainers, fetchFileContent, fetchRationales } from "./api";
import type { Annotation, FileContent, GraphNode, LineRationale, SymbolExplainer } from "./types";
import { AnnotationList, FlowList, type ComponentSelection } from "./DetailPanel";
import { FileRationalePanel } from "./FileRationalePanel";
import { RichText } from "./markdown";
import { highlightLines } from "./highlight";
import { CodeSkeleton, SkeletonLines } from "./Skeleton";
import "./SourceViewer.css";

/**
 * Everything about one file, in one place: its source on the left, and on
 * the right a side bar with what the analysis knows about it.
 *
 * Clicking a file anywhere in the viewer lands here directly. There is no
 * intermediate "details" step to click through: the summary, why the file
 * exists, its place on the architecture map, what it imports and what
 * imports it, and its PR history are the side bar's Overview tab; Functions
 * lists the file's functions with their one-line explainers; Ask is the
 * highlight-to-ask thread. Dependencies are links, so reading can follow the
 * import graph from file to file without leaving.
 */

interface Props {
  owner: string;
  name: string;
  node: GraphNode;
  annotations: Annotation[];
  /** Files that import this one. */
  dependents: string[];
  /** Set when the file was opened from the architecture map. */
  selection?: ComponentSelection;
  /** Open another file (or select another map node) from a link in the bar. */
  onNavigate: (id: string) => void;
  /** From the map: close this and open the architecture Ask panel on the file. */
  onAskAbout?: () => void;
  onClose: () => void;
}

type Tab = "overview" | "functions" | "ask";

interface Selection {
  startLine: number;
  endLine: number;
  text: string;
}

function lineOf(node: Node | null): number | null {
  const element = node instanceof Element ? node : node?.parentElement ?? null;
  const line = element?.closest<HTMLElement>("[data-line]");
  return line ? Number(line.dataset.line) : null;
}

function PathList({ paths, empty, onNavigate }: { paths: string[]; empty: string; onNavigate: (id: string) => void }) {
  if (paths.length === 0) return <p className="detail-panel__empty">{empty}</p>;
  return (
    <ul className="source-bar__paths">
      {paths.map((path) => (
        <li key={path}>
          <button onClick={() => onNavigate(path)} title={`Open ${path}`}>
            {path}
          </button>
        </li>
      ))}
    </ul>
  );
}

export function SourceViewer({ owner, name, node, annotations, dependents, selection, onNavigate, onAskAbout, onClose }: Props) {
  const path = node.id;
  const [file, setFile] = useState<FileContent | null>(null);
  const [rationales, setRationales] = useState<LineRationale[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("overview");
  const [showWhy, setShowWhy] = useState(false);

  const [codeSelection, setCodeSelection] = useState<Selection | null>(null);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [activeRationale, setActiveRationale] = useState<LineRationale | null>(null);

  // Function explainers are generated the first time anyone opens this file
  // and stored after. Loaded separately from the file so the code shows
  // immediately and the explainers land when they land.
  const [explainers, setExplainers] = useState<SymbolExplainer[]>([]);
  const [explainersStatus, setExplainersStatus] = useState<"loading" | "done" | "error">("loading");
  const [explainersError, setExplainersError] = useState<string | null>(null);
  const [showExplainers, setShowExplainers] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setFile(null);
    setCodeSelection(null);
    setActiveRationale(null);
    setShowWhy(false);
    setTab("overview");
    Promise.all([fetchFileContent(owner, name, path), fetchRationales(owner, name, path)])
      .then(([f, r]) => {
        if (cancelled) return;
        setFile(f);
        setRationales(r);
      })
      .catch((e) => !cancelled && setLoadError(String(e.message ?? e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [owner, name, path]);

  useEffect(() => {
    let cancelled = false;
    setExplainers([]);
    setExplainersError(null);
    setExplainersStatus("loading");
    ensureSymbolExplainers(owner, name, path)
      .then((entries) => {
        if (cancelled) return;
        setExplainers(entries);
        setExplainersStatus("done");
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setExplainersError(String((e as { message?: string })?.message ?? e));
        setExplainersStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [owner, name, path]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (e.key === "Escape" && !(target && /^(INPUT|TEXTAREA)$/.test(target.tagName))) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const lines = useMemo(() => (file ? highlightLines(file.content, path) : []), [file, path]);
  const explainerByLine = useMemo(() => {
    const map = new Map<number, SymbolExplainer>();
    explainers.forEach((x) => {
      if (!map.has(x.line)) map.set(x.line, x);
    });
    return map;
  }, [explainers]);

  function rationaleForLine(lineNo: number): LineRationale | undefined {
    return rationales.find((r) => lineNo >= r.start_line && lineNo <= r.end_line);
  }

  function jumpToLine(line: number) {
    document.querySelector<HTMLElement>(`[data-line="${line}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

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
    setCodeSelection({ startLine: Math.min(anchor, focus), endLine: Math.max(anchor, focus), text });
    setTab("ask");
  }

  function clearSelection() {
    setCodeSelection(null);
    setQuestion("");
    setAskError(null);
    window.getSelection()?.removeAllRanges();
  }

  function isSelected(lineNo: number): boolean {
    return codeSelection !== null && lineNo >= codeSelection.startLine && lineNo <= codeSelection.endLine;
  }

  async function submitQuestion() {
    if (!codeSelection) return;
    setAsking(true);
    setAskError(null);
    try {
      const entry = await askWhy(
        owner,
        name,
        path,
        codeSelection.startLine,
        codeSelection.endLine,
        question.trim() || undefined,
        codeSelection.text,
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

  const selectionLabel = codeSelection
    ? codeSelection.startLine === codeSelection.endLine
      ? `Line ${codeSelection.startLine}`
      : `Lines ${codeSelection.startLine}–${codeSelection.endLine}`
    : null;
  const rangeLabel = (r: LineRationale) => `Lines ${r.start_line}${r.end_line !== r.start_line ? `–${r.end_line}` : ""}`;

  return (
    <div className="source-viewer__backdrop" onClick={onClose}>
      <div className="source-viewer" onClick={(e) => e.stopPropagation()}>
        <div className="source-viewer__header">
          <button className="source-viewer__back" onClick={onClose} title="Close (Esc)">
            ← Back
          </button>
          <span className="source-viewer__path">{path}</span>
          {explainers.length > 0 && (
            <button
              className={`source-viewer__toggle ${showExplainers ? "source-viewer__toggle--on" : ""}`}
              onClick={() => setShowExplainers((v) => !v)}
              aria-pressed={showExplainers}
              title="Show or hide the one-line explainers above each function"
            >
              ✦ explainers
            </button>
          )}
          <button className="source-viewer__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="source-viewer__body">
          <div className="source-viewer__code" onMouseUp={captureSelection}>
            {loading && <CodeSkeleton />}
            {loadError && <div className="source-viewer__error">{loadError}</div>}
            {file &&
              lines.map((html, i) => {
                const lineNo = i + 1;
                const rationale = rationaleForLine(lineNo);
                const explainer = showExplainers ? explainerByLine.get(lineNo) : undefined;
                return (
                  <div key={lineNo}>
                    {explainer && (
                      <div className={`source-explainer source-explainer--${explainer.kind}`} aria-label={`${explainer.kind} ${explainer.name}`}>
                        <span className="source-explainer__gutter" aria-hidden="true">✦</span>
                        <span className="source-explainer__text">
                          <span className="source-explainer__name">{explainer.name}</span> {explainer.explanation}
                        </span>
                      </div>
                    )}
                    <div data-line={lineNo} className={`source-line ${isSelected(lineNo) ? "source-line--selected" : ""}`}>
                      <span className="source-line__no" aria-hidden="true">
                        {lineNo}
                      </span>
                      <code className="source-line__text" dangerouslySetInnerHTML={{ __html: html || " " }} />
                      {rationale && (
                        <button
                          className="source-line__marker"
                          title="A rationale is recorded for this line"
                          onClick={() => {
                            setActiveRationale(rationale);
                            setTab("ask");
                          }}
                        >
                          ●
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
          </div>

          <aside className="source-viewer__panel source-bar">
            <div className="source-bar__tabs" role="tablist">
              {(
                [
                  ["overview", "Overview", null],
                  ["functions", "Functions", explainers.length || null],
                  ["ask", "Ask why", rationales.length || null],
                ] as const
              ).map(([key, label, count]) => (
                <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>
                  {label}
                  {count ? <span className="source-bar__count">{count}</span> : null}
                </button>
              ))}
            </div>

            {tab === "overview" && (
              <div className="source-bar__section">
                <div className="detail-panel__kind">
                  file{selection?.group ? ` · in map group “${selection.group.label}”` : ""}
                </div>
                {node.summary ? (
                  <RichText className="source-bar__summary" text={node.summary} />
                ) : (
                  <SkeletonLines lines={5} className="source-bar__pending" />
                )}
                <div className="detail-panel__actions">
                  <button className="detail-panel__view-source" onClick={() => setShowWhy((v) => !v)}>
                    {showWhy ? "Hide" : "Why does this file exist?"} <span aria-hidden="true">→</span>
                  </button>
                  {onAskAbout && (
                    <button className="detail-panel__view-source" onClick={onAskAbout}>
                      ✦ Ask about this on the map
                    </button>
                  )}
                </div>
                {showWhy && <FileRationalePanel owner={owner} name={name} path={path} />}

                {selection && (selection.inbound.length > 0 || selection.outbound.length > 0) && (
                  <div className="source-bar__flows">
                    <FlowList title="On the map: flows in from" items={selection.inbound} direction="in" onSelectComponent={onNavigate} />
                    <FlowList title="On the map: flows out to" items={selection.outbound} direction="out" onSelectComponent={onNavigate} />
                  </div>
                )}

                <h3>Imports</h3>
                <PathList paths={node.dependencies} empty="Imports nothing else in this repository." onNavigate={onNavigate} />
                <h3>Imported by</h3>
                <PathList paths={dependents} empty="Nothing in this repository imports it." onNavigate={onNavigate} />

                <h3>History</h3>
                <AnnotationList annotations={annotations} />
              </div>
            )}

            {tab === "functions" && (
              <div className="source-bar__section">
                {explainersStatus === "loading" && (
                  <>
                    <p className="source-viewer__explaining">Explaining each function in this file… (first open only)</p>
                    {Array.from({ length: 5 }, (_, i) => (
                      <SkeletonLines key={i} lines={2} className="source-bar__pending source-bar__pending--row" />
                    ))}
                  </>
                )}
                {explainersStatus === "error" && (
                  <p className="source-viewer__explaining source-viewer__explaining--error">
                    Couldn't generate function explainers: {explainersError}
                  </p>
                )}
                {explainersStatus === "done" && explainers.length === 0 && (
                  <p className="detail-panel__empty">No functions or classes found to explain in this file.</p>
                )}
                {explainers.length > 0 && (
                  <ul className="symbol-list">
                    {explainers.map((x) => (
                      <li key={x.id}>
                        <button onClick={() => jumpToLine(x.line)} title={`Line ${x.line}`}>
                          <span className={`symbol-list__kind symbol-list__kind--${x.kind}`}>
                            {x.kind === "method" ? "def" : x.kind === "class" ? "class" : "fn"}
                          </span>
                          <span className="symbol-list__name">{x.name}</span>
                        </button>
                        <p className="symbol-list__text">{x.explanation}</p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {tab === "ask" && (
              <div className="source-bar__section">
                {codeSelection && (
                  <div className="ask-why">
                    <div className="ask-why__label">{selectionLabel} selected</div>
                    <pre className="ask-why__excerpt">{codeSelection.text}</pre>
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
                    {asking && <SkeletonLines lines={3} className="source-bar__pending" />}
                    {askError && <p className="ask-why__error">{askError}</p>}
                  </div>
                )}

                {activeRationale && !codeSelection && (
                  <div className="rationale-thread">
                    <div className="rationale-thread__meta">{rangeLabel(activeRationale)}</div>
                    <p className="rationale-thread__q">{activeRationale.question}</p>
                    <RichText className="rationale-thread__a" text={activeRationale.answer} />
                  </div>
                )}

                {!codeSelection && (
                  <div className="source-viewer__hint">
                    {!activeRationale && (
                      <p>Highlight any code on the left — a word, an expression, a whole block — then ask why it's there.</p>
                    )}
                    {rationales.length > 0 && (
                      <>
                        <h4>Asked so far</h4>
                        <ul className="rationale-list">
                          {rationales.map((r) => (
                            <li key={r.id}>
                              <button
                                className={activeRationale?.id === r.id ? "active" : ""}
                                onClick={() => {
                                  setActiveRationale(r);
                                  jumpToLine(r.start_line);
                                }}
                              >
                                {rangeLabel(r)} — {r.question}
                              </button>
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                  </div>
                )}
              </div>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}

// Default export as well, so Viewer can lazy-load this chunk: it pulls in
// highlight.js, which has no business sitting in the initial bundle for the
// many visitors who never open a file's source.
export default SourceViewer;
