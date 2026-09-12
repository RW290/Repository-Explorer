import type { Graph } from "./types";

const API_BASE = "http://localhost:8000";

export async function fetchGraph(repoUrl?: string): Promise<Graph> {
  const url = repoUrl
    ? `${API_BASE}/api/graph?repo_url=${encodeURIComponent(repoUrl)}`
    : `${API_BASE}/api/graph`;
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `Failed to fetch graph: ${res.status}`);
  }
  return res.json();
}
