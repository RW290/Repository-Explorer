# Repository-Explorer

A web app that analyzes a public GitHub repository and presents it as an
interactive map: files and folders, import dependencies between them,
model-written summaries, architectural groupings, and rationale extracted
from merged pull requests.

**Live**: [repository-explorer.replit.app](https://repository-explorer.replit.app)

## Features

- File and folder graph with resolved import edges
- Per-file summaries and a project overview
- Architecture map: 8–20 key files and folders in 2–6 groups, with flows between them
- PR history per file, with stated and inferred rationale kept separate
- Function-level explainers for Python, JavaScript and TypeScript
- Questions about a code range, a file, the project overview, or the architecture map
- Progressive loading: the graph appears within seconds and fills in as analysis runs

## Repository layout

```
backend/
  app/            FastAPI application and analysis pipeline
  fixtures/       Demo graph served without network access
  tests/          Unit tests
frontend/
  src/            React + TypeScript viewer
start.sh          Starts backend and frontend together (development)
main.py           Replit workspace placeholder
.env.example      Environment variable template
replit.md         Replit workspace setup
```

### Backend — `backend/app/`

| Module | Responsibility |
|---|---|
| `main.py` | HTTP API and static frontend serving |
| `pipeline.py` | Orchestrates an analysis and caches the result |
| `jobs.py` | In-memory background job tracking |
| `progress.py` | Stage and event reporting for running jobs |
| `github_client.py` | GitHub REST/GraphQL access and cloning |
| `imports.py` | Static import resolution across languages |
| `parser.py` | File summaries and project overview |
| `miner.py` | Pull request fetching and rationale extraction |
| `merge.py` | Attaches PR annotations to file nodes |
| `architecture.py` | Architecture map generation and validation |
| `symbols.py` | Function and class explainers |
| `explain.py` | On-demand questions about code, files, the project and the map |
| `rationale_store.py` | Persistent storage of answers and explainers |
| `audience.py` | Shared prompt framing for reader-facing text |
| `llm.py` | Model client (Ollama Cloud) |
| `errors.py` | User-facing error types |
| `models.py` | Pydantic data model |

### Analysis pipeline

```
clone → resolve imports → ┬ summarize files      ┐
                          ├ mine pull requests   ├→ merge → map architecture → cache
                          └ write overview       ┘
```

Completed graphs are cached in `backend/.cache/` (gitignored).

### Import resolution

| Language | Resolved |
|---|---|
| Python | Relative imports, packages, inferred import roots |
| JS / TS / Vue / Svelte | `import`, `export … from`, `require`, dynamic `import()`, tsconfig `paths`/`baseUrl`, workspace packages, `#subpath` imports, `@/` alias |
| Go | `go.mod` module path to package files |
| Rust | `mod` declarations, `crate::`/`super::`/`self::` paths, workspace crates |
| Java / Kotlin / Scala / Groovy | Fully-qualified, static and wildcard imports |
| C / C++ / Objective-C | `#include` |
| Ruby, PHP, Dart | `require`/`require_relative`, PSR-4 `use`, relative and `package:` imports |
| CSS / SCSS / Less, HTML | `@import`/`@use`/`@forward`, `<script>`/`<link>` |

Only imports that resolve to a file in the repository become edges.

### Frontend — `frontend/src/`

| File | Responsibility |
|---|---|
| `App.tsx` | Landing page and analysis lifecycle |
| `Viewer.tsx`, `layout.ts` | Folder/file graph view |
| `ArchitectureView.tsx`, `mermaid.ts` | Architecture map (Mermaid + ELK) |
| `ArchitectureAsk.tsx` | Question panel for the map |
| `SourceViewer.tsx`, `highlight.ts` | Source view with explainers, overview and questions |
| `DetailPanel.tsx` | Details for folders and external systems |
| `ProjectOverview.tsx` | Project overview panel |
| `AnalysisProgress.tsx` | Stage tracker for running analyses |
| `Skeleton.tsx`, `Spinner.tsx` | Loading states |
| `languages.ts` | File-kind colors |
| `api.ts`, `types.ts` | API client and shared types |

## Data model

One `Graph` per repository (`backend/app/models.py`, mirrored in
`frontend/src/types.ts`):

```jsonc
{
  "repo_url": "https://github.com/owner/name",
  "overview": "…",
  "resolver_version": 2,
  "nodes": [{
    "id": "src/auth/session.py",
    "type": "file",                              // or "folder"
    "parent": "src/auth",
    "summary": "…",
    "dependencies": ["src/auth/tokens.py"],
    "annotations": ["ann_001"]
  }],
  "annotations": [{
    "id": "ann_001", "node_id": "src/auth/session.py",
    "source": "pr", "source_ref": "PR #142", "date": "2024-03-11",
    "rationale_stated": "…",
    "rationale_inferred": null,
    "confidence": "high",                        // high | medium | low
    "diff_summary": "…"
  }],
  "architecture": {
    "groups": [{ "id": "core", "label": "Core HTTP", "description": "…" }],
    "nodes":  [{ "id": "src/auth/session.py", "group": "core" },
               { "id": "ext:redis", "group": null, "external": true,
                 "label": "Redis", "description": "…" }],
    "edges":  [{ "source": "…", "target": "…", "label": "reads cache",
                 "backed": true }]
  }
}
```

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness check |
| `GET /api/graph` | Fixture demo graph |
| `GET /api/graph?repo_url=…` | Synchronous analysis |
| `POST /api/analyze` `{repo_url, force_refresh?}` | Returns a cached graph or starts a job |
| `GET /api/analyze/{job_id}?have=N` | Job status, progress and (partial) graph |
| `POST /api/repos/{owner}/{name}/architecture` | Build the map for a cached graph |
| `POST /api/repos/{owner}/{name}/symbol-explainers` `{path}` | Function explainers for a file |
| `GET /api/repos/{owner}/{name}/file?path=…` | File source from GitHub |
| `GET`/`POST …/rationales`, `…/file-rationales`, `…/project-rationales`, `…/architecture-rationales` | Read or ask questions |

## Running locally

Backend:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` to port 8000. `start.sh` runs both.

Tests:

```bash
cd backend && python -m unittest discover -s tests
```

## Configuration

Read from the environment, or from `.env` at the repo root (see `.env.example`).

| Variable | Required | Purpose |
|---|---|---|
| `OLLAMA_API_KEY` | Yes | Ollama Cloud API key |
| `OLLAMA_MODEL` | No | Model override; default `gpt-oss:20b` |
| `LLM_CONCURRENCY` | No | Concurrent model calls; default `2` |
| `GITHUB_TOKEN` | When deployed | GitHub token with public-repo read access; locally the `gh` CLI login is used |
| `CORS_ORIGINS` | When cross-origin | Allowed origins; default `http://localhost:5173` |
| `VITE_API_BASE` | When cross-origin | Backend URL, set in `frontend/.env` at build time |

## Deployment

Replit Autoscale service. One FastAPI process serves the API and the built
frontend on port 5000.

- Build: `cd frontend && npm ci && npm run build`
- Run: uvicorn on port 5000

Job state is held in memory and the cache is on local disk. There is no
authentication on `/api/analyze`.

## Limitations

- Import edges are static; C# and Swift have none, and runtime-resolved imports are not detected.
- Architecture groupings, external systems and dashed (inferred) flows are model output.
- Function explainers cover Python, JavaScript and TypeScript only.
- PR history covers the 40 most recently updated merged PRs.
- The folder view is two levels deep.
- Public GitHub repositories only.
