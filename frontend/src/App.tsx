import { useEffect, useState } from "react";
import { fetchGraph } from "./api";
import type { Graph } from "./types";
import { Viewer } from "./Viewer";

export function App() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchGraph().then(setGraph).catch((e) => setError(String(e)));
  }, []);

  if (error) {
    return <div className="status status--error">Failed to load graph: {error}</div>;
  }
  if (!graph) {
    return <div className="status">Loading graph…</div>;
  }
  return <Viewer graph={graph} />;
}
