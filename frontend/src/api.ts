import type { Graph } from "./types";

const API_BASE = "http://localhost:8000";

export async function fetchGraph(): Promise<Graph> {
  const res = await fetch(`${API_BASE}/api/graph`);
  if (!res.ok) {
    throw new Error(`Failed to fetch graph: ${res.status}`);
  }
  return res.json();
}
