import type {
  AnalysisStatus,
  Architecture,
  FileContent,
  FileRationale,
  Graph,
  LineRationale,
  ProjectRationale,
  SymbolExplainer,
} from "./types";

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

// Parses "owner/name" out of a GitHub repo URL, mirroring the backend's
// parse_repo_url. Returns null for anything that isn't a github.com repo URL
// (e.g. the fixture demo's graph, whose repo_url is null).
export function parseRepoUrl(repoUrl: string | null): { owner: string; name: string } | null {
  if (!repoUrl) return null;
  const match = repoUrl.trim().match(/github\.com[:/]([^/]+)\/([^/.]+?)(?:\.git)?\/?$/);
  return match ? { owner: match[1], name: match[2] } : null;
}

export async function fetchFileContent(owner: string, name: string, path: string): Promise<FileContent> {
  return asJson(
    await fetch(`${API_BASE}/api/repos/${owner}/${name}/file?path=${encodeURIComponent(path)}`),
  );
}

export async function fetchRationales(owner: string, name: string, path: string): Promise<LineRationale[]> {
  return asJson(
    await fetch(`${API_BASE}/api/repos/${owner}/${name}/rationales?path=${encodeURIComponent(path)}`),
  );
}

export async function askWhy(
  owner: string,
  name: string,
  path: string,
  startLine: number,
  endLine: number,
  question?: string,
  // The exact highlighted substring. The line range alone would widen the
  // question to whole lines, losing which part of the line was picked out.
  selectedText?: string,
): Promise<LineRationale> {
  return asJson(
    await fetch(`${API_BASE}/api/repos/${owner}/${name}/rationales`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path,
        start_line: startLine,
        end_line: endLine,
        question: question || null,
        selected_text: selectedText || null,
      }),
    }),
  );
}

export async function fetchFileRationales(owner: string, name: string, path: string): Promise<FileRationale[]> {
  return asJson(
    await fetch(`${API_BASE}/api/repos/${owner}/${name}/file-rationales?path=${encodeURIComponent(path)}`),
  );
}

export async function askWhyFileExists(
  owner: string,
  name: string,
  path: string,
  question?: string,
): Promise<FileRationale> {
  return asJson(
    await fetch(`${API_BASE}/api/repos/${owner}/${name}/file-rationales`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, question: question || null }),
    }),
  );
}

export async function fetchProjectRationales(owner: string, name: string): Promise<ProjectRationale[]> {
  return asJson(await fetch(`${API_BASE}/api/repos/${owner}/${name}/project-rationales`));
}

export async function askAboutProject(owner: string, name: string, question?: string): Promise<ProjectRationale> {
  return asJson(
    await fetch(`${API_BASE}/api/repos/${owner}/${name}/project-rationales`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question || null }),
    }),
  );
}

// Builds (or returns the cached) semantic architecture map for an analyzed
// repo. New analyses include it in the graph already; this backfills repos
// cached before the stage existed. One LLM call, so expect tens of seconds
// the first time and instant afterwards.
export async function buildArchitecture(
  owner: string,
  name: string,
  forceRefresh = false,
): Promise<Architecture | null> {
  const result = await asJson<{ architecture: Architecture | null }>(
    await fetch(`${API_BASE}/api/repos/${owner}/${name}/architecture`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force_refresh: forceRefresh }),
    }),
  );
  return result.architecture;
}

// One-line explainers for every function/class in a file. The first call
// for a file generates them (one batched LLM call, tens of seconds); later
// calls return the stored set.
export async function ensureSymbolExplainers(owner: string, name: string, path: string): Promise<SymbolExplainer[]> {
  return asJson(
    await fetch(`${API_BASE}/api/repos/${owner}/${name}/symbol-explainers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }),
  );
}
