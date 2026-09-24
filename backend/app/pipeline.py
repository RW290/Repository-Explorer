"""Orchestrates parser -> miner -> merge for a given repo URL, on demand.

Caches the merged graph to disk per repo. Re-running the full pipeline
costs real LLM and GitHub API calls (see llm.py's module docstring for the
current free-tier quota), so repeat requests for the same repo should hit
the cache, not re-mine. A run takes minutes end-to-end (clone + several
batched LLM calls with deliberate pacing) — callers that expose this over
HTTP should run it as a background job (see jobs.py) rather than blocking
a request on it, since most hosting platforms time out long-lived requests.
"""

import json
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from pathlib import Path

from app.architecture import generate_architecture, recompute_backing
from app.imports import RESOLVER_VERSION, build_dependency_graph
from app.merge import merge
from app.miner import run_miner
from app.parser import clone_repo, discover_files, folder_summary_lines, generate_overview, parse_structure, summarize_files
from app.progress import Reporter

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?/?$", repo_url.strip())
    if not match:
        raise ValueError(
            f"{repo_url.strip()!r} doesn't look like a GitHub repository address. Use the "
            "form https://github.com/owner/repository — a link to a specific file, branch, "
            "or pull request won't work, and only GitHub is supported."
        )
    return match.group(1), match.group(2)


def cache_path_for(owner: str, name: str) -> Path:
    return CACHE_DIR / f"{owner}__{name}.json"


def load_cached(repo_url: str) -> dict | None:
    owner, name = parse_repo_url(repo_url)
    path = cache_path_for(owner, name)
    return json.loads(path.read_text()) if path.exists() else None


def ensure_architecture(owner: str, name: str, force: bool = False) -> dict | None:
    cache_path = cache_path_for(owner, name)
    if not cache_path.exists():
        return None
    graph = json.loads(cache_path.read_text())
    if graph.get("architecture") and not force:
        return graph["architecture"]
    architecture = generate_architecture(owner, name, graph)
    if architecture is not None:
        graph["architecture"] = architecture
        cache_path.write_text(json.dumps(graph, indent=2))
    return architecture


def edges_are_stale(graph: dict) -> bool:
    return int(graph.get("resolver_version", 1)) < RESOLVER_VERSION


def refresh_dependencies(repo_url: str, on_stage: Callable[[str], None] | None = None) -> dict:
    owner, name = parse_repo_url(repo_url)
    cache_path = cache_path_for(owner, name)
    graph = json.loads(cache_path.read_text())
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if on_stage:
                on_stage("updating dependency edges (cloning)")
            repo_root = clone_repo(f"https://github.com/{owner}/{name}.git", Path(tmp))
            if on_stage:
                on_stage("updating dependency edges (resolving imports)")
            dependencies = build_dependency_graph(repo_root, discover_files(repo_root))
    except Exception:  # noqa: BLE001 — see docstring: degrade, never fail the open
        return graph

    node_ids = {n["id"] for n in graph["nodes"]}
    for node in graph["nodes"]:
        if node["type"] == "file" and node["id"] in dependencies:
            node["dependencies"] = [d for d in dependencies[node["id"]] if d in node_ids]
    if graph.get("architecture"):
        recompute_backing(graph["architecture"], graph["nodes"])
    graph["resolver_version"] = RESOLVER_VERSION
    cache_path.write_text(json.dumps(graph, indent=2))
    return graph


def run_pipeline(
    repo_url: str,
    force_refresh: bool = False,
    on_stage: Callable[[str], None] | None = None,
    reporter: Reporter | None = None,
) -> dict:
    owner, name = parse_repo_url(repo_url)
    cache_path = cache_path_for(owner, name)

    if not force_refresh and cache_path.exists():
        return json.loads(cache_path.read_text())

    reporter = reporter or Reporter()
    canonical_url = f"https://github.com/{owner}/{name}"

    with tempfile.TemporaryDirectory() as tmp:
        repo_root, nodes = parse_structure(f"{canonical_url}.git", Path(tmp), reporter)
        file_nodes = [n for n in nodes if n.type == "file"]
        known_files = {n.id for n in file_nodes}

        def snapshot(extractions: list[dict], overview: str) -> dict:
            graph = merge(nodes, extractions, overview)
            graph["repo_url"] = canonical_url
            graph["resolver_version"] = RESOLVER_VERSION
            graph["architecture"] = None
            return graph

        reporter.publish(snapshot([], ""))

        # Whatever has landed so far. Three kinds of worker write here (summary
        # batches, the overview, PR history), each re-publishing the partial
        # graph so the viewer shows it without waiting for the others.
        lock = threading.Lock()
        landed: dict = {"extractions": [], "overview": ""}
        by_id = {n.id: n for n in file_nodes}

        def republish() -> None:
            reporter.publish(snapshot(landed["extractions"], landed["overview"]))

        def apply_summaries(batch: dict[str, str]) -> None:
            with lock:
                for path, summary in batch.items():
                    if path in by_id:
                        by_id[path].summary = summary
                republish()

        def overview_stage() -> str:
            text = generate_overview(repo_root, owner, name, folder_summary_lines(nodes), reporter)
            with lock:
                landed["overview"] = text
                republish()
            return text

        def history_stage() -> list[dict]:
            found = run_miner(owner, name, known_files, reporter)
            with lock:
                landed["extractions"] = found
                republish()
            return found

        with ThreadPoolExecutor(max_workers=2) as side:
            history = side.submit(history_stage)
            overview_future = side.submit(overview_stage)
            summarize_files(repo_root, file_nodes, reporter, on_batch=apply_summaries)
            overview = overview_future.result()
            extractions = history.result()

    graph = snapshot(extractions, overview)
    reporter.publish(graph)
    # Last, because it reads the summaries and overview the earlier stages
    # produced. Best-effort (None on failure), like the overview.
    reporter.start("map")
    graph["architecture"] = generate_architecture(owner, name, graph, on_stage=reporter.note)
    reporter.finish("map", None if graph["architecture"] else "skipped (no usable map)")

    CACHE_DIR.mkdir(exist_ok=True)
    cache_path.write_text(json.dumps(graph, indent=2))
    return graph
