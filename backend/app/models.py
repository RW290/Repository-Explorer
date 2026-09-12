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
