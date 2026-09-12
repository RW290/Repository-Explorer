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
from collections.abc import Callable
from pathlib import Path

from app.merge import merge
from app.miner import run_miner
from app.parser import run_parser

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


def run_pipeline(repo_url: str, force_refresh: bool = False, on_stage: Callable[[str], None] | None = None) -> dict:
    owner, name = parse_repo_url(repo_url)
    cache_path = cache_path_for(owner, name)

    if not force_refresh and cache_path.exists():
        return json.loads(cache_path.read_text())

    with tempfile.TemporaryDirectory() as tmp:
        nodes = run_parser(f"https://github.com/{owner}/{name}.git", Path(tmp), on_stage=on_stage)

    known_files = {n.id for n in nodes if n.type == "file"}
    extractions = run_miner(owner, name, known_files, on_stage=on_stage)
    if on_stage:
        on_stage("merging annotations into graph")
    graph = merge(nodes, extractions)

    CACHE_DIR.mkdir(exist_ok=True)
    cache_path.write_text(json.dumps(graph, indent=2))
    return graph
