import { useRef, useState } from "react";
import { fetchFixture, pollAnalysis, startAnalysis } from "./api";
import type { Graph } from "./types";
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
        <button className="landing__button" onClick={analyze} disabled={status === "loading"}>
          Analyze
        </button>
      </div>
      <p className="landing__hint">
        First run for a new repo clones it and calls the LLM for summaries + PR rationale — expect a
        few minutes. Cached after that.
      </p>

      <button className="landing__fixture" onClick={loadFixture} disabled={status === "loading"}>
        Or load the phase-1 fixture demo instead
      </button>

      {status === "loading" && <p className="landing__status">{stage || "Starting…"}</p>}
      {status === "error" && <p className="landing__status landing__status--error">{error}</p>}
    </div>
  );
}
