"""API server for the repo breakdown viewer.

GET /api/graph with no query params serves the phase-1 fixture (kept as a
zero-cost, zero-latency demo).

Analyzing a real repo is async: POST /api/analyze starts the pipeline in a
background job (or returns the cached result immediately if there is one)
and GET /api/analyze/{job_id} polls for its status. This exists because the
full pipeline takes minutes — a hosting platform's request/proxy timeout
would kill a synchronous call long before it finished.
"""

import os
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import github_client, rationale_store
from app.errors import PipelineError
from app.explain import (
    DEFAULT_FILE_QUESTION,
    DEFAULT_PROJECT_QUESTION,
    DEFAULT_QUESTION,
    explain_file,
    explain_project,
    explain_selection,
)
from app.jobs import get_job, start_job
from app.models import Architecture, FileRationale, Graph, LineRationale, ProjectRationale, SymbolExplainer
from app.symbols import explain_symbols
from app.pipeline import ensure_architecture, load_cached, parse_repo_url, run_pipeline

# Generous cap on what gets sent to the browser for one file — this is about
# rendering cost in the viewer, not the GitHub API limit (github_client
# already can't fetch past ~1MB via the raw contents API).
MAX_FILE_CHARS_FOR_VIEWER = 200_000

# Local dev reads secrets from the repo-root .env; a no-op if the file
# doesn't exist (a deployed instance gets its secrets injected directly
# into the process environment instead, e.g. Replit Secrets).
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(title="repo-explorer backend")

_default_origins = "http://localhost:5173"
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", _default_origins).split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    repo_url: str
    force_refresh: bool = False


class AnalysisStatus(BaseModel):
    job_id: str | None = None
    status: str
    stage: str = ""
    graph: Graph | None = None
    error: str | None = None


class FileContentResponse(BaseModel):
    path: str
    content: str
    truncated: bool


class ExplainRequest(BaseModel):
    path: str
    start_line: int
    end_line: int
    question: str | None = None
    # The exact substring highlighted in the viewer. Optional: a caller that
    # only knows a line range still works, it just asks about whole lines.
    selected_text: str | None = None


class FileExplainRequest(BaseModel):
    path: str
    question: str | None = None


class ProjectExplainRequest(BaseModel):
    question: str | None = None


class SymbolExplainRequest(BaseModel):
    path: str
    force_refresh: bool = False


class ArchitectureRequest(BaseModel):
    force_refresh: bool = False


class ArchitectureResponse(BaseModel):
    architecture: Architecture | None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def serve_frontend() -> FileResponse:
    """Serve the built SPA in production.

    Local development uses Vite on port 5000, while published deployments run
    one FastAPI process that serves both the API and the compiled frontend.
    """
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="Frontend build not found")
    return FileResponse(index)


@app.get("/api/graph", response_model=Graph)
def get_graph(repo_url: str | None = Query(default=None)) -> Graph:
    """Synchronous path — fine for local dev/self-hosting, but a hosted
    deployment should use POST /api/analyze instead to avoid request
    timeouts on an uncached repo."""
    if repo_url is None:
        data = (FIXTURES_DIR / "sample_graph.json").read_text()
        return Graph.model_validate_json(data)
    try:
        graph = run_pipeline(repo_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PipelineError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    return Graph.model_validate(graph)


@app.post("/api/analyze", response_model=AnalysisStatus)
def start_analysis(payload: AnalyzeRequest) -> AnalysisStatus:
    try:
        parse_repo_url(payload.repo_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not payload.force_refresh:
        cached = load_cached(payload.repo_url)
        if cached is not None:
            return AnalysisStatus(status="done", graph=Graph.model_validate(cached))

    job = start_job(payload.repo_url, force_refresh=payload.force_refresh)
    return AnalysisStatus(job_id=job.id, status=job.status, stage=job.stage)


@app.get("/api/analyze/{job_id}", response_model=AnalysisStatus)
def get_analysis(job_id: str) -> AnalysisStatus:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No job with that id")
    graph = Graph.model_validate(job.result) if job.result is not None else None
    return AnalysisStatus(
        job_id=job.id, status=job.status, stage=job.stage, graph=graph, error=job.error
    )


@app.get("/api/repos/{owner}/{name}/file", response_model=FileContentResponse)
def get_file_content(owner: str, name: str, path: str = Query(...)) -> FileContentResponse:
    """Raw source for the in-viewer code panel. Fetched on demand rather than
    stored during analysis — most files in a repo are never opened, so
    caching every file's full text in the analysis cache would mostly be
    waste."""
    try:
        content = github_client.file_contents(owner, name, path)
    except PipelineError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    truncated = len(content) > MAX_FILE_CHARS_FOR_VIEWER
    return FileContentResponse(path=path, content=content[:MAX_FILE_CHARS_FOR_VIEWER], truncated=truncated)


@app.post("/api/repos/{owner}/{name}/architecture", response_model=ArchitectureResponse)
def build_architecture(owner: str, name: str, payload: ArchitectureRequest) -> ArchitectureResponse:
    """Generates (or regenerates) the semantic architecture map for an
    already-analyzed repo. New analyses produce it inline; this exists so
    repos cached before the stage existed get one on first open without a
    full re-analysis, which would cost minutes and real LLM quota."""
    if load_cached(f"https://github.com/{owner}/{name}") is None:
        raise HTTPException(status_code=404, detail="This repository hasn't been analyzed yet.")
    try:
        architecture = ensure_architecture(owner, name, force=payload.force_refresh)
    except PipelineError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    return ArchitectureResponse(architecture=architecture)


@app.get("/api/repos/{owner}/{name}/rationales", response_model=list[LineRationale])
def get_rationales(owner: str, name: str, path: str = Query(...)) -> list[dict]:
    return rationale_store.load_rationales(owner, name, path)


@app.post("/api/repos/{owner}/{name}/rationales", response_model=LineRationale)
def create_rationale(owner: str, name: str, payload: ExplainRequest) -> dict:
    """Answers one "why is this used?" question about a highlighted range of
    lines, then persists it — shared for everyone who later opens this same
    repo, not just the person who asked."""
    if payload.start_line < 1 or payload.end_line < payload.start_line:
        raise HTTPException(status_code=400, detail="Invalid line range.")

    try:
        content = github_client.file_contents(owner, name, payload.path)
    except PipelineError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    lines = content.splitlines()
    if payload.end_line > len(lines):
        raise HTTPException(
            status_code=400, detail=f"{payload.path} only has {len(lines)} lines, not {payload.end_line}."
        )

    file_summary = None
    file_dependencies = None
    cached = load_cached(f"https://github.com/{owner}/{name}")
    if cached is not None:
        node = next((n for n in cached["nodes"] if n["id"] == payload.path), None)
        file_summary = node.get("summary") if node else None
        file_dependencies = node.get("dependencies") if node else None

    try:
        answer = explain_selection(
            payload.path,
            content,
            payload.start_line,
            payload.end_line,
            payload.question,
            file_summary,
            file_dependencies,
            payload.selected_text,
        )
    except PipelineError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    entry = {
        "id": str(uuid.uuid4()),
        "path": payload.path,
        "start_line": payload.start_line,
        "end_line": payload.end_line,
        "selected_text": payload.selected_text
        or "\n".join(lines[payload.start_line - 1 : payload.end_line]),
        "question": payload.question or DEFAULT_QUESTION,
        "answer": answer,
        "created_at": time.time(),
    }
    rationale_store.add_rationale(owner, name, entry)
    return entry


@app.post("/api/repos/{owner}/{name}/symbol-explainers", response_model=list[SymbolExplainer])
def ensure_symbol_explainers(owner: str, name: str, payload: SymbolExplainRequest) -> list[dict]:
    """One-line explainers for every function/class in a file, generated
    proactively the first time the file is opened in the source viewer and
    persisted for everyone after. POST rather than GET because the first
    call has a side effect (one batched LLM call); later calls just read."""
    if not payload.force_refresh:
        cached = rationale_store.load_symbol_explainers(owner, name, payload.path)
        if cached:
            return cached

    try:
        content = github_client.file_contents(owner, name, payload.path)
    except PipelineError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    file_summary = None
    graph = load_cached(f"https://github.com/{owner}/{name}")
    if graph is not None:
        node = next((n for n in graph["nodes"] if n["id"] == payload.path), None)
        file_summary = node.get("summary") if node else None

    try:
        explained = explain_symbols(payload.path, content, file_summary)
    except PipelineError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    now = time.time()
    entries = [{"id": str(uuid.uuid4()), "created_at": now, **item} for item in explained]
    rationale_store.replace_symbol_explainers(owner, name, payload.path, entries)
    return entries


@app.get("/api/repos/{owner}/{name}/file-rationales", response_model=list[FileRationale])
def get_file_rationales(owner: str, name: str, path: str = Query(...)) -> list[dict]:
    return rationale_store.load_file_rationales(owner, name, path)


@app.post("/api/repos/{owner}/{name}/file-rationales", response_model=FileRationale)
def create_file_rationale(owner: str, name: str, payload: FileExplainRequest) -> dict:
    """Answers "why does this file exist in the wider project?" using the
    file's recorded summary and its place in the dependency graph (what it
    depends on, what depends on it), then persists it the same way
    line-level rationale is."""
    cached = load_cached(f"https://github.com/{owner}/{name}")
    if cached is None:
        raise HTTPException(status_code=404, detail="This repository hasn't been analyzed yet.")

    node = next((n for n in cached["nodes"] if n["id"] == payload.path), None)
    if node is None:
        raise HTTPException(status_code=404, detail=f"No file {payload.path!r} found in this repo's analysis.")

    dependents = [n["id"] for n in cached["nodes"] if payload.path in n.get("dependencies", [])]

    try:
        answer = explain_file(
            payload.path, node.get("summary", ""), node.get("dependencies", []), dependents, payload.question
        )
    except PipelineError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    entry = {
        "id": str(uuid.uuid4()),
        "path": payload.path,
        "question": payload.question or DEFAULT_FILE_QUESTION,
        "answer": answer,
        "created_at": time.time(),
    }
    rationale_store.add_file_rationale(owner, name, entry)
    return entry


@app.get("/api/repos/{owner}/{name}/project-rationales", response_model=list[ProjectRationale])
def get_project_rationales(owner: str, name: str) -> list[dict]:
    return rationale_store.load_project_rationales(owner, name)


@app.post("/api/repos/{owner}/{name}/project-rationales", response_model=ProjectRationale)
def create_project_rationale(owner: str, name: str, payload: ProjectExplainRequest) -> dict:
    """Answers a follow-up question about the whole project, grounded in its
    generated overview, then persists it the same way file/line rationale is."""
    cached = load_cached(f"https://github.com/{owner}/{name}")
    if cached is None:
        raise HTTPException(status_code=404, detail="This repository hasn't been analyzed yet.")

    try:
        answer = explain_project(owner, name, cached.get("overview") or "", payload.question)
    except PipelineError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    entry = {
        "id": str(uuid.uuid4()),
        "question": payload.question or DEFAULT_PROJECT_QUESTION,
        "answer": answer,
        "created_at": time.time(),
    }
    rationale_store.add_project_rationale(owner, name, entry)
    return entry


@app.get("/{frontend_path:path}")
def serve_frontend_assets(frontend_path: str) -> FileResponse:
    """Serve Vite assets and fall back to index.html for SPA routes."""
    requested = (FRONTEND_DIST / frontend_path).resolve()
    dist_root = FRONTEND_DIST.resolve()
    if requested.is_relative_to(dist_root) and requested.is_file():
        return FileResponse(requested)

    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend build not found")
