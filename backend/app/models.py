"""Data model for the graph the frontend consumes.

Mirrors the two top-level entities from the build brief: graph nodes (one
per file or folder) and annotations (one per significant historical change,
attached to a node by id).
"""

from typing import Literal

from pydantic import BaseModel


class Annotation(BaseModel):
    id: str
    node_id: str
    source: Literal["pr", "commit", "issue"]
    source_ref: str
    date: str
    rationale_stated: str | None
    rationale_inferred: str | None
    confidence: Literal["high", "medium", "low"]
    diff_summary: str


class GraphNode(BaseModel):
    id: str
    type: Literal["file", "folder"]
    parent: str | None
    summary: str
    dependencies: list[str]
    annotations: list[str]


class Graph(BaseModel):
    nodes: list[GraphNode]
    annotations: list[Annotation]
    # None for the phase-1 fixture demo, which isn't backed by a real GitHub
    # repo — the frontend uses this to decide whether source-viewing and
    # line rationale are available at all.
    repo_url: str | None = None
    # Brief LLM-generated orientation to the whole project, generated once
    # during analysis (see parser.generate_overview) and cached here rather
    # than regenerated per view. Empty string if generation failed or the
    # repo has neither a README nor folder structure to summarize from.
    overview: str = ""


class LineRationale(BaseModel):
    id: str
    path: str
    start_line: int
    end_line: int
    selected_text: str
    question: str
    answer: str
    created_at: float


class FileRationale(BaseModel):
    id: str
    path: str
    question: str
    answer: str
    created_at: float


class ProjectRationale(BaseModel):
    id: str
    question: str
    answer: str
    created_at: float
