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


class ArchitectureGroup(BaseModel):
    id: str
    label: str
    description: str | None = None


class ArchitectureNode(BaseModel):
    # A real node id from `Graph.nodes` (file or folder) — the map's boxes
    # are the explorer's nodes, placed into semantic groups. Never a path the
    # validator couldn't find; see architecture.py. The one exception is an
    # external system (`external=True`, id prefixed "ext:"): a third-party
    # library or service the code talks to, which has no file to open and is
    # drawn greyed out.
    id: str
    group: str | None = None
    external: bool = False
    label: str | None = None
    description: str | None = None


class ArchitectureEdge(BaseModel):
    source: str
    target: str
    label: str | None = None
    # True when the static import graph shows a dependency between the two
    # components' files (in either direction); False for a relationship the
    # model asserted that imports can't see (HTTP, subprocess, external API).
    # The viewer draws the latter dashed, the same stated-vs-inferred
    # distinction the PR annotations make.
    backed: bool = False


class Architecture(BaseModel):
    """Semantic map: a handful of groups (Frontend, API, LLM, …) over a
    couple dozen of the graph's own file/folder nodes, plus the main flows
    between them. Generated once per analysis by architecture.py and
    compiled to a Mermaid diagram in the browser."""

    groups: list[ArchitectureGroup]
    nodes: list[ArchitectureNode]
    edges: list[ArchitectureEdge]


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
    # None when generation failed, or for analyses cached before this stage
    # existed — the frontend asks for a lazy backfill in that case.
    architecture: Architecture | None = None
    # Which generation of the import resolver produced `dependencies` (see
    # imports.RESOLVER_VERSION). Analyses from before the field existed were
    # Python-only, i.e. version 1.
    resolver_version: int = 1


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


class SymbolExplainer(BaseModel):
    """One-line explanation of a function, method or class, generated for a
    whole file at once the first time it's opened (see symbols.py)."""

    id: str
    path: str
    name: str
    kind: Literal["function", "method", "class"]
    line: int
    end_line: int
    explanation: str
    created_at: float


class ProjectRationale(BaseModel):
    id: str
    question: str
    answer: str
    created_at: float
