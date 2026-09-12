"""API server for the repo breakdown viewer.

Phase 1 (fixture-first): serves the hand-written fixture graph so the
viewer can be built and sanity-checked before the architecture parser and
history miner exist. GET /api/graph is the only endpoint the viewer needs —
all graph data is fetched once per repo, never re-fetched during pan/zoom.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.models import Graph

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

app = FastAPI(title="repo-explorer backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/graph", response_model=Graph)
def get_graph() -> Graph:
    data = (FIXTURES_DIR / "sample_graph.json").read_text()
    return Graph.model_validate_json(data)
