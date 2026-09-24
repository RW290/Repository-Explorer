"""Scorers and gates: pure functions from outputs to numbers.

Nothing here calls a model, so all of it is fast, free and unit-tested
(tests/test_eval_checks.py). Each metric exists because of a failure that
actually happened in this project — named in its docstring — which is the
honest way to choose metrics: measure what has gone wrong before.
"""

import re
import statistics

# Summary length bounds per tier, in characters. Outside these, the prompt's
# length instruction was ignored: too short to say anything, or so long that
# generation cost is being wasted (output tokens are the expensive part).
SUMMARY_BOUNDS = {"source": (250, 1500), "brief": (40, 520)}
_BOILERPLATE_OPENINGS = re.compile(r"^\s*(this (file|module|script|document)|the (file|module)\b|`?[\w./-]+`? (is|contains) )", re.I)
_WORD = re.compile(r"[A-Za-z0-9_]+")


def _shingles(text: str, n: int = 5) -> set[tuple[str, ...]]:
    words = [w.lower() for w in _WORD.findall(text or "")]
    return {tuple(words[i : i + n]) for i in range(max(0, len(words) - n + 1))}


def verbatim_overlap(generated: str, source: str) -> float:
    generated_shingles = _shingles(generated)
    if not generated_shingles:
        return 0.0
    return len(generated_shingles & _shingles(source)) / len(generated_shingles)


def score_summaries(inputs: list[dict], outputs: dict[str, str]) -> dict:
    tiers = {item["path"]: item["tier"] for item in inputs}
    present = {p: s for p, s in outputs.items() if p in tiers and s and "unavailable" not in s.lower()}
    in_bounds = 0
    for path, summary in present.items():
        low, high = SUMMARY_BOUNDS[tiers[path]]
        in_bounds += low <= len(summary) <= high
    lengths = {tier: [len(s) for p, s in present.items() if tiers[p] == tier] for tier in ("source", "brief")}
    return {
        # One malformed reply used to blank a whole batch of files.
        "coverage": len(present) / len(inputs) if inputs else 0.0,
        "length_in_bounds": in_bounds / len(present) if present else 0.0,
        "source_mean_chars": round(statistics.mean(lengths["source"])) if lengths["source"] else 0,
        "brief_mean_chars": round(statistics.mean(lengths["brief"])) if lengths["brief"] else 0,
        # "This file contains…" spends the opening words on nothing.
        "boilerplate_opening": sum(bool(_BOILERPLATE_OPENINGS.match(s)) for s in present.values()) / len(present) if present else 0.0,
        # Identifiers should be marked as code, which the prompts ask for.
        "uses_code_markup": sum("`" in s for s in present.values()) / len(present) if present else 0.0,
    }


def score_prs(inputs: list[dict], outputs: list[dict], labels: dict | None = None) -> dict:
    by_number = {int(o["number"]): o for o in outputs if isinstance(o, dict) and str(o.get("number", "")).lstrip("-").isdigit()}
    matched = [(item, by_number[item["number"]]) for item in inputs if item["number"] in by_number]
    both = sum(bool(o.get("rationale_stated")) and bool(o.get("rationale_inferred")) for _, o in matched)
    neither = sum(not o.get("rationale_stated") and not o.get("rationale_inferred") for _, o in matched)
    # The integrity rule: stated when the author gave a reason, inferred only
    # when they didn't. Checked against the input, which needs no hand labels.
    kind_ok = sum((bool(o.get("rationale_stated")) == bool(item["stated"])) for item, o in matched)
    # The prompt's own rule: "high" for a stated reason, "medium"/"low" for an
    # inferred one. A confident guess is the failure this exists to catch.
    confidence_ok = sum(
        o.get("confidence") == "high" if o.get("rationale_stated") else o.get("confidence") in ("medium", "low")
        for _, o in matched
    )
    overlaps = [verbatim_overlap(o["rationale_stated"], item["stated"]) for item, o in matched if o.get("rationale_stated") and item["stated"]]
    metrics = {
        "coverage": len(matched) / len(inputs) if inputs else 0.0,
        "stated_and_inferred_both_set": both,
        "neither_set": neither,
        "kind_matches_input": kind_ok / len(matched) if matched else 0.0,
        "confidence_consistent": confidence_ok / len(matched) if matched else 0.0,
        "verbatim_overlap_mean": round(statistics.mean(overlaps), 3) if overlaps else 0.0,
        "verbatim_overlap_max": round(max(overlaps), 3) if overlaps else 0.0,
    }
    labelled = {int(k): v for k, v in ((labels or {}).get("pr_kind") or {}).items() if v in ("stated", "inferred")}
    if labelled:
        scored = [(n, o) for n, o in by_number.items() if n in labelled]
        hits = sum(("stated" if o.get("rationale_stated") else "inferred") == labelled[n] for n, o in scored)
        metrics["kind_accuracy_vs_labels"] = hits / len(scored) if scored else 0.0
        metrics["labelled_prs"] = len(scored)
    return metrics


def score_map(raw: dict | None, validated: dict | None, problems: list[str], labels: dict | None = None) -> dict:
    from app.architecture import EMPTY_LABELS

    if raw is None:
        return {"usable": 0.0}
    asked_members = raw.get("members") or raw.get("nodes") or []
    asked_edges = raw.get("edges") or []
    members = [n for n in (validated or {}).get("nodes", []) if not n.get("external")]
    edges = (validated or {}).get("edges", [])
    raw_labels = [str(e.get("label") or "").strip().lower().rstrip(".") for e in asked_edges if isinstance(e, dict)]
    metrics = {
        "usable": float(validated is not None),
        # A path that doesn't exist is a hallucination the validator caught.
        "members_resolved": len(members) / len(asked_members) if asked_members else 0.0,
        "edges_kept": len(edges) / len(asked_edges) if asked_edges else 0.0,
        "members": len(members),
        "edges": len(edges),
        "groups": len((validated or {}).get("groups", [])),
        "externals": len((validated or {}).get("nodes", [])) - len(members),
        "edges_backed_by_imports": sum(e.get("backed", False) for e in edges) / len(edges) if edges else 0.0,
        # One run labelled 39 of 40 edges "imports": a map of nothing.
        "empty_edge_labels": sum(label in EMPTY_LABELS or not label for label in raw_labels) / len(raw_labels) if raw_labels else 0.0,
        "validation_problems": len(problems),
    }
    key_files = set((labels or {}).get("key_files") or [])
    if key_files:
        chosen = {n["id"] for n in members}
        metrics["key_file_recall"] = len(chosen & key_files) / len(key_files)
        metrics["key_file_precision"] = len(chosen & key_files) / len(chosen) if chosen else 0.0
    return metrics


def score_graph(graph: dict) -> dict:
    files = [n for n in graph.get("nodes", []) if n["type"] == "file"]
    ids = {n["id"] for n in graph.get("nodes", [])}
    summarized = [n for n in files if n.get("summary") and "unavailable" not in n["summary"].lower()]
    annotations = graph.get("annotations", [])
    architecture = graph.get("architecture") or {}
    members = [n for n in architecture.get("nodes", []) if not n.get("external")]
    edges = architecture.get("edges", [])
    return {
        "files": len(files),
        "summary_coverage": len(summarized) / len(files) if files else 0.0,
        "dangling_dependencies": sum(dep not in ids for n in files for dep in n.get("dependencies", [])),
        "files_with_edges": sum(bool(n.get("dependencies")) for n in files) / len(files) if files else 0.0,
        "annotations": len(annotations),
        "annotations_both_rationales": sum(bool(a.get("rationale_stated")) and bool(a.get("rationale_inferred")) for a in annotations),
        "annotations_on_missing_nodes": sum(a.get("node_id") not in ids for a in annotations),
        "has_map": float(bool(architecture)),
        "map_members_not_in_graph": sum(n["id"] not in ids for n in members),
        "map_edges_backed_by_imports": sum(e.get("backed", False) for e in edges) / len(edges) if edges else 0.0,
    }


# Gates. (metric, comparison, bound): a run fails if any is violated. These
# are floors under behaviour the product depends on, not targets to optimise;
# a number can clear its gate and still have regressed, which is what the
# baseline comparison in the report is for.
GATES: dict[str, list[tuple[str, str, float]]] = {
    "summaries": [("coverage", ">=", 0.95), ("length_in_bounds", ">=", 0.75)],
    "prs": [
        ("coverage", ">=", 0.9),
        ("stated_and_inferred_both_set", "<=", 0),
        ("kind_matches_input", ">=", 0.9),
        ("verbatim_overlap_mean", "<=", 0.35),
    ],
    "map": [("usable", ">=", 1), ("members_resolved", ">=", 0.85), ("empty_edge_labels", "<=", 0.2), ("edges_backed_by_imports", ">=", 0.4)],
    "graph": [
        ("summary_coverage", ">=", 0.95),
        ("dangling_dependencies", "<=", 0),
        ("annotations_both_rationales", "<=", 0),
        ("annotations_on_missing_nodes", "<=", 0),
        ("map_members_not_in_graph", "<=", 0),
    ],
}


def gate(suite: str, metrics: dict) -> list[str]:
    failures = []
    for name, comparison, bound in GATES.get(suite, []):
        if name not in metrics:
            continue
        value = metrics[name]
        ok = value >= bound if comparison == ">=" else value <= bound
        if not ok:
            failures.append(f"{name} = {value:.3g} (needs {comparison} {bound:g})")
    return failures
