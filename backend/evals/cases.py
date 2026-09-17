"""Frozen eval inputs.

`freeze` captures, for one repository, exactly what each model call site
would be handed: file contents with their import lists, pull requests with
their descriptions or diffs, and the summarized graph the map stage reads.
No model is called. The result is committed, because an eval whose inputs
drift (the repo gets a new commit, a PR is edited) can't tell a prompt change
from a data change.
"""

import json
import subprocess
import tempfile
import time
from pathlib import Path

CASES_DIR = Path(__file__).resolve().parent / "cases"


def case_dir(owner: str, name: str) -> Path:
    return CASES_DIR / f"{owner}__{name}"


def freeze(owner: str, name: str, n_source: int = 8, n_brief: int = 8, n_prs: int = 10) -> Path:
    from app import github_client
    from app.imports import build_dependency_graph, resolver_for
    from app.miner import _candidate_stated_rationale, _fetch_with_retry, is_trivial
    from app.parser import SUMMARY_TIERS, _templated_summary, clone_repo, discover_files
    from app.pipeline import load_cached

    out = case_dir(owner, name)
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        root = clone_repo(f"https://github.com/{owner}/{name}.git", Path(tmp))
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        files = discover_files(root)
        deps = build_dependency_graph(root, files)
        dependents: dict[str, int] = {}
        for targets in deps.values():
            for t in targets:
                dependents[t] = dependents.get(t, 0) + 1

        entries = {"source": [], "brief": []}
        for f in files:
            rel = f.relative_to(root).as_posix()
            text = f.read_text(encoding="utf-8", errors="ignore")
            if _templated_summary(rel, text) is not None:
                continue
            tier = "source" if resolver_for(rel) else "brief"
            entries[tier].append(
                {"path": rel, "tier": tier, "content": text[: SUMMARY_TIERS[tier][0]], "dependencies": deps.get(rel, [])}
            )
        # The most-connected source files are the ones whose summaries matter
        # most, and the hardest to summarize well; brief files are taken
        # evenly across the listing so docs, config and tests all appear.
        entries["source"].sort(key=lambda e: -(dependents.get(e["path"], 0) * 2 + len(e["dependencies"])))
        step = max(1, len(entries["brief"]) // max(1, n_brief))
        sample = entries["source"][:n_source] + entries["brief"][::step][:n_brief]
        known = {f.relative_to(root).as_posix() for f in files}

    prs = [pr for pr in _fetch_with_retry(owner, name) if not is_trivial(pr, known)][:n_prs]
    pr_items = []
    for pr in prs:
        stated = _candidate_stated_rationale(pr)
        pr_items.append(
            {
                "number": pr.number,
                "title": pr.title,
                "files": pr.files,
                "stated": stated,
                "diff": "" if stated else github_client.pr_diff(owner, name, pr.number)[:4000],
            }
        )

    meta = {"repo": f"{owner}/{name}", "commit": sha, "frozen_at": time.strftime("%Y-%m-%d"), "files": len(sample), "prs": len(pr_items)}
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    (out / "summaries.json").write_text(json.dumps(sample, indent=2))
    (out / "prs.json").write_text(json.dumps(pr_items, indent=2))

    graph = load_cached(f"https://github.com/{owner}/{name}")
    if graph is not None:
        # The map stage's input is the summarized graph, not the map itself.
        graph = {**graph, "architecture": None, "annotations": []}
        (out / "graph.json").write_text(json.dumps(graph, indent=1))

    labels = out / "labels.json"
    if not labels.exists():
        # A template for hand labels. Filling it in turns "the output looks
        # plausible" into "the output matches what a person decided".
        labels.write_text(
            json.dumps(
                {
                    "_how_to": "pr_kind: for each PR, 'stated' if the author explained why, else 'inferred'. "
                    "key_files: the files you would insist appear on an architecture map of this repo. "
                    "Entries left null or empty are skipped when scoring.",
                    "pr_kind": {str(item["number"]): None for item in pr_items},
                    "key_files": [],
                },
                indent=2,
            )
        )
    return out


def load(owner: str, name: str) -> dict:
    directory = case_dir(owner, name)
    if not (directory / "meta.json").exists():
        raise SystemExit(f"No frozen case for {owner}/{name}. Run: python -m evals freeze {owner}/{name}")

    def read(filename: str):
        path = directory / filename
        return json.loads(path.read_text()) if path.exists() else None

    return {
        "meta": read("meta.json"),
        "summaries": read("summaries.json") or [],
        "prs": read("prs.json") or [],
        "graph": read("graph.json"),
        "labels": read("labels.json") or {},
    }
