import type { AnalysisStatus, Graph } from "./types";

// Use same-origin API paths by default so Replit's proxy can route requests to
// the local backend. A separately deployed backend can still be configured.
const API_BASE = import.meta.env.VITE_API_BASE ?? "";

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchFixture(): Promise<Graph> {
  return asJson(await fetch(`${API_BASE}/api/graph`));
}

// Starts analysis of a real repo. Returns immediately: either status "done"
// (repo was already cached) or a job_id to poll with pollAnalysis — the
// pipeline runs as a background job because it can take several minutes,
// far longer than a typical hosting platform's request timeout.
export async function startAnalysis(repoUrl: string, forceRefresh = false): Promise<AnalysisStatus> {
  return asJson(
    await fetch(`${API_BASE}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_url: repoUrl, force_refresh: forceRefresh }),
    }),
  );
}

export async function pollAnalysis(jobId: string): Promise<AnalysisStatus> {
  return asJson(await fetch(`${API_BASE}/api/analyze/${jobId}`));
}
