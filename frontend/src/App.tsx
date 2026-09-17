import { useEffect, useRef, useState } from "react";
import { fetchFixture, pollAnalysis, startAnalysis } from "./api";
import type { AnalysisProgress as Progress, Graph } from "./types";
import { AnalysisProgress } from "./AnalysisProgress";
import { ViewerSkeleton } from "./Skeleton";
import { Spinner } from "./Spinner";
import { ThemeToggle, type Theme } from "./ThemeToggle";
import { Viewer } from "./Viewer";
import "./Viewer.css";

type Status = "idle" | "loading" | "error";

// Fast enough that the stage tracker and streaming summaries feel live. Cheap,
// because a poll only carries the graph when it changed (see pollAnalysis).
const POLL_INTERVAL_MS = 1200;

export function App() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [stage, setStage] = useState("");
  // Non-null while an analysis is running: drives the progress card before
  // the first partial graph, and the HUD over the viewer after it.
  const [progress, setProgress] = useState<Progress | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const haveVersion = useRef(0);
  const [error, setError] = useState<string | null>(null);
  // Starts empty: the input shows a greyed placeholder as an example of the
  // expected shape, not a real value that gets analyzed if you just hit the
  // button.
  const [repoUrl, setRepoUrl] = useState("");
  const pollTimer = useRef<number | null>(null);
  const [theme, setTheme] = useState<Theme>(() =>
    window.localStorage.getItem("repo-explorer-theme") === "dark" ? "dark" : "light",
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("repo-explorer-theme", theme);
  }, [theme]);

  function stopPolling() {
    if (pollTimer.current !== null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }

  function endAnalysis() {
    setAnalyzing(false);
    setProgress(null);
    haveVersion.current = 0;
  }

  function poll(jobId: string) {
    pollAnalysis(jobId, haveVersion.current)
      .then((result) => {
        if (result.status === "done" && result.graph) {
          setGraph(result.graph);
          setStatus("idle");
          endAnalysis();
        } else if (result.status === "error") {
          // A partial graph may already be on screen; drop back to the
          // landing page so the error isn't hidden behind it.
          setGraph(null);
          setError(result.error ?? "Analysis failed.");
          setStatus("error");
          endAnalysis();
        } else {
          setStage(result.stage);
          if (result.progress) {
            setProgress(result.progress);
            haveVersion.current = result.progress.partial_version;
          }
          // The partial graph: structure lands seconds in, so the viewer
          // opens now and fills in, instead of after the last LLM call.
          if (result.graph) {
            setGraph(result.graph);
            setStatus("idle");
          }
          pollTimer.current = window.setTimeout(() => poll(jobId), POLL_INTERVAL_MS);
        }
      })
      .catch((e) => {
        setGraph(null);
        setError(String(e.message ?? e));
        setStatus("error");
        endAnalysis();
      });
  }

  function loadFixture() {
    stopPolling();
    setStatus("loading");
    setStage("");
    setError(null);
    fetchFixture()
      .then((g) => {
        setGraph(g);
        setStatus("idle");
      })
      .catch((e) => {
        setError(String(e.message ?? e));
        setStatus("error");
      });
  }

  function analyze() {
    if (!repoUrl.trim()) return;
    stopPolling();
    setStatus("loading");
    setStage("");
    setError(null);
    startAnalysis(repoUrl)
      .then((result) => {
        if (result.status === "done" && result.graph) {
          setGraph(result.graph);
          setStatus("idle");
        } else if (result.job_id) {
          setAnalyzing(true);
          poll(result.job_id);
        }
      })
      .catch((e) => {
        setError(String(e.message ?? e));
        setStatus("error");
      });
  }

  if (graph) {
    return (
      <Viewer
        graph={graph}
        onBack={() => {
          stopPolling();
          endAnalysis();
          setGraph(null);
        }}
        live={analyzing ? progress : null}
        theme={theme}
        onToggleTheme={() => setTheme((current) => current === "light" ? "dark" : "light")}
      />
    );
  }

  // Loading: draw the viewer's shape in placeholders rather than dimming the
  // landing page behind a spinner — the reader sees what is about to appear
  // and where, and the real viewer replaces it without a layout jump.
  if (status === "loading") {
    return (
      <div className="viewer viewer--loading">
        <ViewerSkeleton />
        <div className="loading-card__box loading-card__box--floating" aria-live="polite">
          {analyzing ? (
            <>
              <AnalysisProgress progress={progress} variant="card" fallbackLabel={stage || "Starting the analysis…"} />
              <p className="loading-card__subtext">
                The graph opens as soon as the structure is known — a few seconds — and fills in while the rest runs.
              </p>
            </>
          ) : (
            <>
              <Spinner size="lg" label={stage || "Opening the repository…"} />
              <p className="loading-card__subtext">Cached repositories open instantly.</p>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="landing">
      <div className="landing__glow landing__glow--one" />
      <div className="landing__glow landing__glow--two" />

      <header className="landing__header">
        <div className="brand-mark" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <span className="brand-name">repo-explorer</span>
        <span className="brand-pill">ARCHITECTURE INTELLIGENCE</span>
        <ThemeToggle
          theme={theme}
          onToggle={() => setTheme((current) => current === "light" ? "dark" : "light")}
        />
      </header>

      <main className="landing__content">
        <p className="landing__eyebrow">Understand the shape of a codebase</p>
        <h1>See how a repo <em>really</em> works.</h1>
        <p className="landing__subtitle">
          Turn a GitHub repository into an explorable map of its architecture, dependencies, and the
          decisions that shaped it.
        </p>

        <div className="landing__form-card">
          <label className="landing__label" htmlFor="repo-url">GitHub repository URL</label>
          <div className="landing__row">
            <div className="landing__input-wrap">
              <span className="landing__input-icon" aria-hidden="true">↗</span>
              <input
                id="repo-url"
                className="landing__input"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") analyze();
                }}
                placeholder="https://github.com/owner/repo"
                autoComplete="off"
                spellCheck={false}
              />
            </div>
            <button className="landing__button" onClick={analyze} disabled={!repoUrl.trim()}>
              Analyze repo <span aria-hidden="true">→</span>
            </button>
          </div>
          <p className="landing__hint">
            A new repo is explorable within seconds and finishes filling in over a few minutes. Cached ones open instantly.
          </p>
        </div>

        <button className="landing__fixture" onClick={loadFixture}>
          <span aria-hidden="true">✦</span> Try the interactive demo instead
        </button>

        <div className="landing__features">
          <div><span className="feature-icon">◎</span><span><strong>Map the structure</strong><small>Folders, files & dependencies</small></span></div>
          <div><span className="feature-icon">✦</span><span><strong>Read the reasoning</strong><small>PRs turned into plain language</small></span></div>
          <div><span className="feature-icon">⌁</span><span><strong>Explore at your pace</strong><small>Zoom from repo to detail</small></span></div>
        </div>
      </main>

      {status === "error" && (
        <div className="landing__status landing__status--error" role="alert">
          <span aria-hidden="true">!</span>{error}
        </div>
      )}

      <footer className="landing__footer">Built for curious engineers <span>·</span> Dependency edges for Python, JS/TS, Go, Rust, JVM, C/C++ and more</footer>
    </div>
  );
}
