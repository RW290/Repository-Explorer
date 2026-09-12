"""Orchestrates parser -> miner -> merge for a given repo URL, on demand.

Caches the merged graph to disk per repo. Re-running the full pipeline
costs real LLM and GitHub API calls (Gemini Flash free tier is ~15 req/min),
so repeat requests for the same repo should hit the cache, not re-mine.
"""

import json
import re
import tempfile
from pathlib import Path

from app.merge import merge
from app.miner import run_miner
from app.parser import run_parser

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?/?$", repo_url.strip())
    if not match:
        raise ValueError(f"Could not parse owner/repo from {repo_url!r}")
    return match.group(1), match.group(2)


def run_pipeline(repo_url: str, force_refresh: bool = False) -> dict:
    owner, name = parse_repo_url(repo_url)
    cache_path = CACHE_DIR / f"{owner}__{name}.json"

    if not force_refresh and cache_path.exists():
        return json.loads(cache_path.read_text())

    with tempfile.TemporaryDirectory() as tmp:
        nodes = run_parser(f"https://github.com/{owner}/{name}.git", Path(tmp))

    known_files = {n.id for n in nodes if n.type == "file"}
    extractions = run_miner(owner, name, known_files)
    graph = merge(nodes, extractions)

    CACHE_DIR.mkdir(exist_ok=True)
    cache_path.write_text(json.dumps(graph, indent=2))
    return graph
