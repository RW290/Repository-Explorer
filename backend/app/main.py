"""API server for the repo breakdown viewer.

GET /api/graph with no query params serves the phase-1 fixture (kept as a
zero-cost, zero-latency demo). Passing ?repo_url=... runs the real
parser -> miner -> merge pipeline on demand (cached to disk per repo) and
returns that instead. Either way, all graph data is fetched once per repo,
never re-fetched during client-side pan/zoom.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.models import Graph
from app.pipeline import run_pipeline

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

app = FastAPI(title="repo-explorer backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/graph", response_model=Graph)
def get_graph(repo_url: str | None = Query(default=None)) -> Graph:
    if repo_url is None:
        data = (FIXTURES_DIR / "sample_graph.json").read_text()
        return Graph.model_validate_json(data)
    try:
        graph = run_pipeline(repo_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Pipeline failed: {e}")
    return Graph.model_validate(graph)
