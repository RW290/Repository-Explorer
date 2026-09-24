import type {
  AnalysisStatus,
  Architecture,
  ArchitectureRationale,
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

export async function startAnalysis(repoUrl: string, forceRefresh = false): Promise<AnalysisStatus> {
  return asJson(
    await fetch(`${API_BASE}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_url: repoUrl, force_refresh: forceRefresh }),
    }),
  );
}

export async function pollAnalysis(jobId: string, have = 0): Promise<AnalysisStatus> {
  return asJson(await fetch(`${API_BASE}/api/analyze/${jobId}?have=${have}`));
}

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

export async function buildArchitecture(
  owner: string,
  name: string,
  forceRefresh = false,
): Promise<Architecture | null> {
  const res = await fetch(`${API_BASE}/api/repos/${owner}/${name}/architecture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force_refresh: forceRefresh }),
  });
  // A backend process running code older than the frontend has no POST route
  // here, so the request falls through to its catch-all GET route and comes
  // back 405 (or 404). That's a stale server, not a problem with the repo —
  // say so, instead of surfacing a bare "Method Not Allowed".
  if (res.status === 405 || res.status === 404) {
    const body = await res.clone().json().catch(() => null);
    if (!body?.detail || body.detail === "Method Not Allowed" || body.detail === "Not Found") {
      throw new Error(
        "The backend is running an older version that doesn't have the architecture-map endpoint yet. " +
          "Restart (or redeploy) the backend so it picks up the new code, then try again.",
      );
    }
  }
  const result = await asJson<{ architecture: Architecture | null }>(res);
  return result.architecture;
}

export async function ensureSymbolExplainers(owner: string, name: string, path: string): Promise<SymbolExplainer[]> {
  const res = await fetch(`${API_BASE}/api/repos/${owner}/${name}/symbol-explainers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  // Same stale-backend signature as buildArchitecture above.
  if (res.status === 405) {
    throw new Error("The backend is running an older version without function explainers. Restart or redeploy it.");
  }
  return asJson(res);
}

export async function fetchArchitectureRationales(owner: string, name: string): Promise<ArchitectureRationale[]> {
  return asJson(await fetch(`${API_BASE}/api/repos/${owner}/${name}/architecture-rationales`));
}

export async function askAboutArchitecture(
  owner: string,
  name: string,
  question?: string,
  focus?: { kind: "group" | "node"; id: string } | null,
): Promise<ArchitectureRationale> {
  const res = await fetch(`${API_BASE}/api/repos/${owner}/${name}/architecture-rationales`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: question || null, focus_kind: focus?.kind ?? null, focus_id: focus?.id ?? null }),
  });
  if (res.status === 405) {
    throw new Error("The backend is running an older version without architecture questions. Restart or redeploy it.");
  }
  return asJson(res);
}
