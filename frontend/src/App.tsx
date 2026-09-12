import { useRef, useState } from "react";
import { fetchFixture, pollAnalysis, startAnalysis } from "./api";
import type { Graph } from "./types";
import { Spinner } from "./Spinner";
import { Viewer } from "./Viewer";

type Status = "idle" | "loading" | "error";

const POLL_INTERVAL_MS = 3000;

export function App() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [stage, setStage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [repoUrl, setRepoUrl] = useState("https://github.com/psf/requests");
  const pollTimer = useRef<number | null>(null);

  function stopPolling() {
    if (pollTimer.current !== null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }

  function poll(jobId: string) {
    pollAnalysis(jobId)
      .then((result) => {
        if (result.status === "done" && result.graph) {
          setGraph(result.graph);
          setStatus("idle");
        } else if (result.status === "error") {
          setError(result.error ?? "Analysis failed.");
          setStatus("error");
        } else {
          setStage(result.stage);
          pollTimer.current = window.setTimeout(() => poll(jobId), POLL_INTERVAL_MS);
        }
      })
      .catch((e) => {
        setError(String(e.message ?? e));
        setStatus("error");
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
          setGraph(null);
        }}
      />
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
                placeholder="https://github.com/owner/repo"
                disabled={status === "loading"}
              />
            </div>
            <button className="landing__button" onClick={analyze} disabled={status === "loading"}>
              {status === "loading" ? <Spinner size="sm" /> : <>Analyze repo <span aria-hidden="true">→</span></>}
            </button>
          </div>
          <p className="landing__hint">
            New analyses take a few minutes. Cached repositories open instantly.
          </p>
        </div>

        <button className="landing__fixture" onClick={loadFixture} disabled={status === "loading"}>
          <span aria-hidden="true">✦</span> Try the interactive demo instead
        </button>

        <div className="landing__features">
          <div><span className="feature-icon">◎</span><span><strong>Map the structure</strong><small>Folders, files & dependencies</small></span></div>
          <div><span className="feature-icon">✦</span><span><strong>Read the reasoning</strong><small>PRs turned into plain language</small></span></div>
          <div><span className="feature-icon">⌁</span><span><strong>Explore at your pace</strong><small>Zoom from repo to detail</small></span></div>
        </div>
      </main>

      {status === "loading" && (
        <div className="loading-card" aria-live="polite">
          <Spinner label={stage || "Preparing your repository…"} />
          <p className="loading-card__subtext">This can take a few minutes for a new repo.</p>
        </div>
      )}
      {status === "error" && (
        <div className="landing__status landing__status--error" role="alert">
          <span aria-hidden="true">!</span>{error}
        </div>
      )}

      <footer className="landing__footer">Built for curious engineers <span>·</span> Python repos, for now</footer>
    </div>
  );
}
