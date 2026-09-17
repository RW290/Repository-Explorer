"""Architecture map: a small, semantic component graph laid over the file graph.

The file-level graph (parser.py) is exhaustive and literal — every file, every
import. That's the right substrate for "why does this file exist," but it's a
poor first picture of a codebase: a reader wants "frontend talks to an API
layer, which drives a pipeline, which calls an LLM," not 128 boxes.

This stage (inspired by gitdiagram's explanation → graph-AST → validate →
compile pipeline) asks the model for that picture as a bounded JSON AST —
groups, member nodes, flow edges — rather than as free-form Mermaid text.
The model never emits drawing syntax; the frontend compiles the validated AST
deterministically. That split matters for integrity, not just tidiness:

- The map's nodes ARE the parser's nodes. The model doesn't invent
  components; it picks which existing files/folders matter and which
  semantic layer each belongs to. A member path that doesn't resolve to a
  real node is dropped, never guessed at, so every box on the map is a file
  or folder you can open in the explorer.
- Every edge is checked against the real import graph. An edge the imports
  back is drawn solid; one the model asserted but the imports don't show
  (an HTTP call, a subprocess, a runtime hand-off) is kept but drawn dashed
  — same stated-vs-inferred rule the PR annotations follow.
- Counts are capped so the diagram stays a map, not a second file listing.

Unlike gitdiagram, the model gets the per-file summaries and dependency edges
the pipeline already produced, not just a file tree and README — so the
grouping is informed by what each file actually does.
"""

import json
import re
from collections.abc import Callable

from app.audience import AUDIENCE_FRAMING
from app.errors import PipelineError
from app.llm import call_llm, call_with_retry

MAX_GROUPS = 8
MAX_NODES = 24
MAX_EXTERNALS = 6
MAX_EDGES = 28
MAX_LABEL_CHARS = 60
MAX_TYPE_CHARS = 40
MAX_DESCRIPTION_CHARS = 240
# One prompt carries the whole repo listing; this keeps a large repo inside a
# sane context budget while still describing everything that has edges.
MAX_LISTING_CHARS = 28_000
SUMMARY_SNIPPET_CHARS = 150
MAX_ATTEMPTS = 2
# Cool sampling: this is a structured-output task where the second run
# over the same repo should look like the first, not a creative one.
TEMPERATURE = 0.2
# Low reasoning effort: on a repo the size of psf/requests the map takes
# about 17s at "low" against about 300s at the default, for maps of the same
# quality (the same kind of groups, real members, sensible labeled flows).
# The prompt is ~29k characters of listing, and at default effort the model
# deliberates over all of it at length; the validator below — not the model's
# care — is what guarantees integrity.
EFFORT = "low"
# A repair round is a whole second model call, so it's reserved for a map
# that is actually unusable or lost a real share of itself to validation. A
# couple of dropped edges is the validator doing its job, not a failed map.
REPAIR_IF_DROPPED_SHARE = 0.25
# Edge labels that say nothing a line doesn't already say. Dropped so the
# diagram shows a bare arrow instead of "imports" thirty times.
EMPTY_LABELS = {"imports", "import", "uses", "use", "depends on", "dependency", "calls", "call", "references"}

_ID_RE = re.compile(r"[^a-z0-9_]+")


def _slug(raw: object, fallback: str) -> str:
    text = _ID_RE.sub("_", str(raw).strip().lower()).strip("_")
    if not text:
        return fallback
    if not text[0].isalpha():
        text = f"n_{text}"
    return text[:48]


def _clean_text(raw: object, limit: int) -> str | None:
    if raw is None:
        return None
    text = re.sub(r"\s+", " ", str(raw)).strip()
    return text[:limit] if text else None


def _describe_graph(nodes: list[dict]) -> str:
    """Compact listing the model reads: folders first, then files ordered so
    that files with real import edges (the architecturally interesting ones)
    survive truncation ahead of docs and config."""
    files = [n for n in nodes if n["type"] == "file"]
    folders = [n for n in nodes if n["type"] == "folder"]
    dependents: dict[str, int] = {}
    for n in files:
        for dep in n.get("dependencies", []):
            dependents[dep] = dependents.get(dep, 0) + 1

    def weight(n: dict) -> int:
        return len(n.get("dependencies", [])) + dependents.get(n["id"], 0)

    lines = ["Folders:"]
    for f in folders:
        lines.append(f"- {f['id']}/ — {f.get('summary', '')[:SUMMARY_SNIPPET_CHARS]}")
    lines.append("")
    lines.append("Files (path — role; imports):")
    budget = MAX_LISTING_CHARS - sum(len(line) + 1 for line in lines)
    omitted = 0
    for n in sorted(files, key=weight, reverse=True):
        snippet = (n.get("summary") or "").replace("\n", " ")[:SUMMARY_SNIPPET_CHARS]
        deps = n.get("dependencies", [])
        deps_text = f"; imports {', '.join(deps[:6])}" if deps else ""
        line = f"- {n['id']} — {snippet}{deps_text}"
        if len(line) + 1 > budget:
            omitted += 1
            continue
        budget -= len(line) + 1
        lines.append(line)
    if omitted:
        lines.append(f"- … and {omitted} more files (mostly leaf files with no import edges)")
    return "\n".join(lines)


def _prompt(owner: str, name: str, overview: str, listing: str, feedback: str | None) -> str:
    overview_block = f'A brief orientation to the project:\n"{overview}"\n\n' if overview else ""
    repair = (
        "\n\nYour previous attempt had these problems — fix every one of them and "
        f"return the complete corrected graph:\n{feedback}\n"
        if feedback
        else ""
    )
    return f"""You are producing an architecture map of the GitHub project "{owner}/{name}"
for an engineer who has never seen it. The map is a small graph of
*components* — not files — grouped by architectural role, with edges showing
where data and control flow between them.

{AUDIENCE_FRAMING}

{overview_block}Here is what the repository contains. Each file line gives its path, a
one-line role, and the repo files it imports (static analysis — trust it):

{listing}

Build the map:
- 2-6 groups, each a semantic layer or boundary with a short human label,
  e.g. "Frontend / UI", "HTTP API", "Analysis pipeline", "LLM integration",
  "Data & storage", "CLI", "Tests", "Build & CI". Pick groups that fit THIS
  repository; don't force a web-app shape onto a library.
- 8-20 members. A member is one file or folder from the listing, with its
  path copied EXACTLY, assigned to one group. Choose the files a reader
  must know to understand how the system works; a folder is a fine member
  when its files act as one unit (a test suite, a docs tree, a package of
  small helpers). Omit config, lockfiles, docs and leaf helpers unless they
  are architecturally central. Every member must appear in the listing —
  never invent a path.
- 0-6 externals: systems outside the repository the code talks to — a
  third-party library it builds on, an HTTP API, a database, an LLM
  service. Each has a short id, a label, and a one-sentence description.
  Only include ones that matter to understanding the architecture.
- 6-20 edges, each `from` one member path or external id `to` another. This is a map of
  the main flows, NOT the import graph: do not draw one edge per import,
  and never more edges than members plus a handful. Keep only the edges a
  reader needs to follow a request or a piece of data through the system.
  Each label is 1-4 words saying what flows or happens ("HTTP JSON",
  "spawns job", "batched prompts", "prepared request") — a label like
  "imports" or "uses" carries no information and is not allowed. Prefer
  edges the import list supports; add edges for runtime relationships
  imports can't show (HTTP calls, subprocesses, external APIs) only where
  they matter.

Return ONLY a JSON object of this exact shape, no prose, no code fence:
{{
  "groups": [{{"id": "frontend", "label": "Frontend / UI", "description": "one short sentence or null"}}],
  "members": [{{"path": "frontend/src/Viewer.tsx", "group": "frontend"}}],
  "externals": [{{"id": "ollama_cloud", "label": "Ollama Cloud", "description": "Hosted LLM inference the pipeline calls", "group": null}}],
  "edges": [{{"from": "frontend/src/Viewer.tsx", "to": "backend/app/main.py", "label": "fetches graph"}}, {{"from": "backend/app/llm.py", "to": "ollama_cloud", "label": "batched prompts"}}]
}}
Group ids are lowercase snake_case and unique.{repair}
"""


def _parse_json_object(raw: str) -> dict:
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def _resolve_path(raw: object, node_ids: set[str]) -> str | None:
    """Exact match preferred; a unique suffix match tolerates the model
    dropping a leading folder. Anything else becomes null rather than a
    guess — the diagram must never link to a file that isn't there."""
    if not raw:
        return None
    path = str(raw).strip().strip("/")
    if path in node_ids:
        return path
    candidates = [n for n in node_ids if n.endswith("/" + path)]
    return candidates[0] if len(candidates) == 1 else None


def _files_for(path: str | None, nodes_by_id: dict[str, dict], children: dict[str, list[str]]) -> set[str]:
    if path is None or path not in nodes_by_id:
        return set()
    node = nodes_by_id[path]
    if node["type"] == "file":
        return {path}
    return set(children.get(path, []))


def _mark_backed(nodes: list[dict], edges: list[dict], nodes_by_id: dict[str, dict], children: dict[str, list[str]]) -> None:
    """Which edges do the real imports vouch for? Either direction counts —
    the model draws data flow, which may run opposite to the import."""
    files_by_node = {n["id"]: _files_for(n["id"], nodes_by_id, children) for n in nodes}  # externals → empty set
    for e in edges:
        src_files, dst_files = files_by_node.get(e["source"], set()), files_by_node.get(e["target"], set())
        e["backed"] = any(
            dep in dst_files for f in src_files for dep in nodes_by_id[f].get("dependencies", [])
        ) or any(dep in src_files for f in dst_files for dep in nodes_by_id[f].get("dependencies", []))


def recompute_backing(architecture: dict, graph_nodes: list[dict]) -> None:
    """Re-verify an existing map's flows against a changed import graph (the
    resolver learned a language, say). The map itself is the model's and is
    left alone; only the solid/dashed verdict on each edge is refreshed."""
    nodes_by_id = {n["id"]: n for n in graph_nodes}
    children: dict[str, list[str]] = {}
    for n in graph_nodes:
        if n["type"] == "file" and n.get("parent"):
            children.setdefault(n["parent"], []).append(n["id"])
    _mark_backed(architecture.get("nodes", []), architecture.get("edges", []), nodes_by_id, children)


def validate_architecture(raw: dict, graph_nodes: list[dict]) -> tuple[dict | None, list[str]]:
    """Normalize the model's JSON into the stored shape, returning the cleaned
    AST plus a list of problems worth feeding back for a repair attempt.
    Returns (None, problems) when what's left isn't worth drawing."""
    problems: list[str] = []
    nodes_by_id = {n["id"]: n for n in graph_nodes}
    node_ids = set(nodes_by_id)
    children: dict[str, list[str]] = {}
    for n in graph_nodes:
        if n["type"] == "file" and n.get("parent"):
            children.setdefault(n["parent"], []).append(n["id"])

    groups: list[dict] = []
    seen_groups: set[str] = set()
    for i, g in enumerate(raw.get("groups") or []):
        if not isinstance(g, dict):
            continue
        gid = _slug(g.get("id") or g.get("label"), f"group_{i}")
        if gid in seen_groups:
            continue
        label = _clean_text(g.get("label"), MAX_LABEL_CHARS)
        if not label:
            problems.append(f"group {gid!r} has no label")
            continue
        seen_groups.add(gid)
        groups.append({"id": gid, "label": label, "description": _clean_text(g.get("description"), MAX_DESCRIPTION_CHARS)})
    if len(groups) > MAX_GROUPS:
        problems.append(f"too many groups ({len(groups)}); use at most {MAX_GROUPS}")
        groups = groups[:MAX_GROUPS]
    group_ids = {g["id"] for g in groups}

    nodes: list[dict] = []
    seen_nodes: set[str] = set()
    for m in raw.get("members") or raw.get("nodes") or []:
        if not isinstance(m, dict):
            continue
        raw_path = m.get("path") or m.get("id")
        path = _resolve_path(raw_path, node_ids)
        if path is None:
            problems.append(f"member path {str(raw_path)!r} does not exist in the repository; copy a path exactly from the listing")
            continue
        if path in seen_nodes:
            continue
        group = _slug(m.get("group"), "") if m.get("group") else None
        if group and group not in group_ids:
            problems.append(f"member {path!r} references unknown group {group!r}")
            group = None
        seen_nodes.add(path)
        nodes.append({"id": path, "group": group})
    if len(nodes) > MAX_NODES:
        problems.append(f"too many members ({len(nodes)}); use at most {MAX_NODES}")
        nodes = nodes[:MAX_NODES]

    # Externals: the only nodes that aren't files. Namespaced with "ext:" so
    # they can never collide with a real path, and so the viewer can tell
    # them apart without a lookup.
    ext_lookup: dict[str, str] = {}
    for i, x in enumerate(raw.get("externals") or []):
        if not isinstance(x, dict) or len(ext_lookup) >= MAX_EXTERNALS:
            continue
        label = _clean_text(x.get("label") or x.get("id"), MAX_LABEL_CHARS)
        if not label:
            continue
        slug = _slug(x.get("id") or label, f"external_{i}")
        ext_id = f"ext:{slug}"
        if ext_id in ext_lookup.values():
            continue
        group = _slug(x.get("group"), "") if x.get("group") else None
        if group and group not in group_ids:
            group = None
        for key in {slug, str(x.get("id") or "").strip(), label, label.lower()}:
            if key:
                ext_lookup[key] = ext_id
        nodes.append(
            {
                "id": ext_id,
                "group": group,
                "external": True,
                "label": label,
                "description": _clean_text(x.get("description"), MAX_DESCRIPTION_CHARS),
            }
        )
    valid_node_ids = {n["id"] for n in nodes}

    def resolve_endpoint(raw_ref: object) -> str | None:
        ref = str(raw_ref or "").strip()
        if not ref:
            return None
        return ext_lookup.get(ref) or ext_lookup.get(_slug(ref, "")) or _resolve_path(ref, valid_node_ids)

    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()
    for e in raw.get("edges") or []:
        if not isinstance(e, dict):
            continue
        src = resolve_endpoint(e.get("from") or e.get("source"))
        dst = resolve_endpoint(e.get("to") or e.get("target"))
        if src is None or dst is None:
            problems.append(
                f"edge {str(e.get('from'))!r} -> {str(e.get('to'))!r} references something that is not a member or external; edges may only join those"
            )
            continue
        if src == dst or (src, dst) in seen_edges:
            continue
        seen_edges.add((src, dst))
        label = _clean_text(e.get("label"), MAX_LABEL_CHARS)
        if label and label.lower().rstrip(".") in EMPTY_LABELS:
            label = None
        edges.append({"source": src, "target": dst, "label": label, "backed": False})
    if len(edges) > MAX_EDGES:
        problems.append(f"too many edges ({len(edges)}); this is a map of main flows, not the import graph — keep at most {MAX_EDGES}")
        edges = edges[:MAX_EDGES]

    # Drop groups nothing landed in; they'd render as empty boxes.
    used_groups = {n["group"] for n in nodes if n["group"]}
    # An external nobody connected to is decoration; drop it.
    connected = {e["source"] for e in edges} | {e["target"] for e in edges}
    nodes = [n for n in nodes if not n.get("external") or n["id"] in connected]
    groups = [g for g in groups if g["id"] in used_groups]

    _mark_backed(nodes, edges, nodes_by_id, children)

    if len([n for n in nodes if not n.get("external")]) < 3:
        problems.append("fewer than 3 usable members")
        return None, problems
    return {"groups": groups, "nodes": nodes, "edges": edges}, problems


def generate_architecture(
    owner: str, name: str, graph: dict, on_stage: Callable[[str], None] | None = None
) -> dict | None:
    """Best-effort, like the overview: the file graph is the product, the map
    is an orientation aid, so a failure here returns None rather than
    failing the whole analysis. One repair round-trip if the first attempt's
    validation turned up hard problems (missing paths, dangling edges)."""
    listing = _describe_graph(graph["nodes"])
    overview = graph.get("overview") or ""
    feedback: str | None = None
    best: dict | None = None
    for attempt in range(MAX_ATTEMPTS):
        if on_stage:
            on_stage("mapping architecture" + (f" (repair attempt {attempt + 1})" if attempt else ""))
        prompt = _prompt(owner, name, overview, listing, feedback)
        try:
            raw_text = call_with_retry(lambda: call_llm(prompt, temperature=TEMPERATURE, think=EFFORT, max_tokens=5000))
            raw = _parse_json_object(raw_text)
        except PipelineError:
            return best
        except (json.JSONDecodeError, TypeError, ValueError):
            feedback = "the response was not a single valid JSON object"
            continue
        result, problems = validate_architecture(raw, graph["nodes"])
        if result is not None:
            best = result
        asked = len(raw.get("members") or raw.get("nodes") or []) + len(raw.get("edges") or [])
        kept = (len(result["nodes"]) + len(result["edges"])) if result else 0
        dropped_share = 1 - kept / asked if asked else 1.0
        if result is not None and dropped_share < REPAIR_IF_DROPPED_SHARE:
            return result
        feedback = "\n".join(f"- {p}" for p in problems) or "the graph was empty"
    return best
