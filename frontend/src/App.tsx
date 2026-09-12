import { useState } from "react";
import { fetchGraph } from "./api";
import type { Graph } from "./types";
import { Viewer } from "./Viewer";

type Status = "idle" | "loading" | "error";

export function App() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [repoUrl, setRepoUrl] = useState("https://github.com/psf/requests");

  function load(url?: string) {
    setStatus("loading");
    setError(null);
    fetchGraph(url)
      .then((g) => {
        setGraph(g);
        setStatus("idle");
      })
      .catch((e) => {
        setError(String(e.message ?? e));
        setStatus("error");
      });
  }

  if (graph) {
    return <Viewer graph={graph} onBack={() => setGraph(null)} />;
  }

  return (
    <div className="landing">
      <h1>repo-explorer</h1>
      <p className="landing__subtitle">
        Explore a repo's architecture, annotated with why things are the way they are.
      </p>

      <div className="landing__row">
        <input
          className="landing__input"
          value={repoUrl}
          onChange={(e) => setRepoUrl(e.target.value)}
          placeholder="https://github.com/owner/repo"
        />
        <button className="landing__button" onClick={() => load(repoUrl)} disabled={status === "loading"}>
          Analyze
        </button>
      </div>
      <p className="landing__hint">
        First run for a new repo clones it and calls the LLM for summaries + PR rationale — expect a
        couple of minutes. Cached after that.
      </p>

      <button className="landing__fixture" onClick={() => load(undefined)} disabled={status === "loading"}>
        Or load the phase-1 fixture demo instead
      </button>

      {status === "loading" && <p className="landing__status">Analyzing repo…</p>}
      {status === "error" && <p className="landing__status landing__status--error">{error}</p>}
    </div>
  );
}
