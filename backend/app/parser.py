"""Repository parser: local static analysis, no LLM for the graph shape.

Clones the target repo, discovers every file worth showing, and builds a
file-level dependency graph from import statements — best-effort static
resolution per language, not a type-checker (see imports.py, which owns
that and lists what's covered). Every file becomes a node and gets an LLM
summary; files in a language the resolver doesn't cover simply have no
edges. LLM calls are used only for the batched file summaries and the
project overview, never for the dependency edges themselves.
"""

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.audience import AUDIENCE_FRAMING
from app.errors import ConfigurationError, PipelineError, RepoCloneError
from app.imports import build_dependency_graph, resolver_for
from app.llm import LLM_CONCURRENCY, call_llm, call_with_retry
from app.progress import Reporter

EXCLUDE_DIR_NAMES = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", "out", "target", ".next", ".nuxt",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage",
}
# Lockfiles: content is auto-generated and not something a reader wants
# summarized — same reasoning as excluding node_modules/.venv wholesale.
EXCLUDE_FILE_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "Pipfile.lock", "poetry.lock", "uv.lock", "Cargo.lock",
    "Gemfile.lock", "composer.lock", "go.sum",
}
# Minified/binary/media: either unreadable as text or meaningless to
# summarize (a summary of compiled or binary bytes says nothing useful).
EXCLUDE_FILE_SUFFIXES = (
    ".min.js", ".min.css", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".icns", ".webp", ".bmp", ".heic", ".tiff",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".zip", ".tar", ".gz", ".whl", ".jar", ".wasm",
    ".pyc", ".so", ".dylib", ".dll", ".exe", ".bin", ".class",
    ".mp3", ".mp4", ".mov", ".wav",
    ".db", ".sqlite",
    # Design-tool documents: binary (or binary-ish) and meaningless as text.
    ".ai", ".psd", ".sketch", ".fig", ".xd", ".eps", ".indd",
)
ROOT_FOLDER_ID = "(root)"
MAX_FILE_CHARS_FOR_SUMMARY = 3000
OVERVIEW_README_NAMES = ("README.md", "README.rst", "README.txt", "README")
MAX_README_CHARS_FOR_OVERVIEW = 6000


@dataclass
class ParsedNode:
    id: str
    type: str  # "file" | "folder"
    parent: str | None
    dependencies: list[str] = field(default_factory=list)
    summary: str = ""


def clone_repo(repo_url: str, workdir: Path) -> Path:
    dest = workdir / "repo"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise ConfigurationError(
            "The `git` command isn't available on this server, so repositories can't be "
            "downloaded for analysis. Install git in the deployment environment."
        ) from None
    except subprocess.CalledProcessError as e:
        raise _translate_clone_error(e, repo_url) from None
    return dest


def _translate_clone_error(e: subprocess.CalledProcessError, repo_url: str) -> Exception:
    stderr = (e.stderr or "").strip()
    lowered = stderr.lower()
    if "not found" in lowered or "repository not found" in lowered:
        return RepoCloneError(
            f"There's no public repository at {repo_url}. Check the URL for typos — private "
            "repositories can't be analyzed unless this server has credentials for them."
        )
    if "authentication failed" in lowered or "could not read username" in lowered:
        return RepoCloneError(
            f"{repo_url} needs credentials to download, which usually means it's private. "
            "This tool can only analyze repositories it can read anonymously."
        )
    if "could not resolve host" in lowered or "network" in lowered or "timed out" in lowered:
        return RepoCloneError(
            "Couldn't reach GitHub to download the repository. Check this server's network "
            "connection and try again."
        )
    detail = stderr.splitlines()[-1] if stderr else f"git exited with code {e.returncode}"
    return RepoCloneError(f"Downloading {repo_url} failed: {detail}")


def discover_files(repo_root: Path) -> list[Path]:
    files = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root)
        if any(part in EXCLUDE_DIR_NAMES for part in rel.parts):
            continue
        if path.name in EXCLUDE_FILE_NAMES:
            continue
        if path.name.lower().endswith(EXCLUDE_FILE_SUFFIXES):
            continue
        files.append(path)
    return sorted(files)


def folder_id_for(rel_path: Path) -> str:
    parts = rel_path.parts[:-1]
    if not parts:
        return ROOT_FOLDER_ID
    if parts[0] in ("src", "lib") and len(parts) > 1:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def build_nodes(repo_root: Path, files: list[Path], dependencies: dict[str, list[str]]) -> list[ParsedNode]:
    folder_ids: dict[str, None] = {}
    nodes: list[ParsedNode] = []
    for f in files:
        rel = f.relative_to(repo_root)
        folder_id = folder_id_for(rel)
        if folder_id:
            folder_ids[folder_id] = None
        nodes.append(
            ParsedNode(
                id=str(rel),
                type="file",
                parent=folder_id,
                dependencies=dependencies.get(str(rel), []),
            )
        )
    folder_nodes = [ParsedNode(id=fid, type="folder", parent=None) for fid in sorted(folder_ids)]
    return folder_nodes + nodes


# --- Summaries ---------------------------------------------------------------
#
# The slow part of an analysis, and what makes it slow is *output*: a reasoning
# model generates tokens serially, so cost tracks how much it is asked to
# write, not how much it reads. Every choice below is about writing less, in
# fewer calls, in the order that matters most:
#
# - Tiers. A source file earns a paragraph about its design. A README, a YAML
#   workflow or a test fixture earns a sentence or two. A LICENSE or an empty
#   `__init__.py` earns no LLM call at all — a template says all there is to
#   say. Most repos are majority non-source, so this is most of the saving.
# - Size-budgeted batches rather than a fixed six files: many small files
#   share a call, so per-call overhead (the framing, the model's warm-up
#   reasoning) is paid fewer times.
# - Most-connected files first, because results stream to the viewer as they
#   land (see progress.py) and the hubs are what a reader opens first.

SUMMARY_TIERS = {
    # tier: (max input chars per file, max files per batch, input budget per batch)
    "source": (MAX_FILE_CHARS_FOR_SUMMARY, 8, 16_000),
    "brief": (1_200, 16, 12_000),
}
_BOILERPLATE_NAMES = {
    "license": "The project's license text.",
    "license.md": "The project's license text.",
    "license.txt": "The project's license text.",
    "copying": "The project's license text.",
    "notice": "Attribution and copyright notices required by the license.",
    ".gitignore": "Paths git should not track (build output, caches, local environment files).",
    ".gitattributes": "Per-path git attributes such as line-ending and diff handling.",
    ".editorconfig": "Editor-agnostic formatting defaults (indentation, line endings) for contributors.",
    ".nojekyll": "Empty marker telling GitHub Pages not to run Jekyll on this directory.",
    "py.typed": "Empty PEP 561 marker declaring that this package ships type information.",
    ".dockerignore": "Paths excluded from the Docker build context.",
    ".npmrc": "npm client configuration for this project.",
    ".prettierignore": "Paths the Prettier formatter should skip.",
    ".eslintignore": "Paths the ESLint linter should skip.",
}
_MIN_MEANINGFUL_CHARS = 40
SUMMARY_UNAVAILABLE = "Summary unavailable (the model's response for this file could not be parsed)."


def _templated_summary(path: str, text: str) -> str | None:
    name = Path(path).name.lower()
    if name in _BOILERPLATE_NAMES:
        return _BOILERPLATE_NAMES[name]
    if len("".join(text.split())) < _MIN_MEANINGFUL_CHARS:
        if name == "__init__.py":
            return "Empty package marker: makes this directory importable as a Python package and nothing more."
        return "Effectively empty file (a placeholder or marker); there is no content to summarize."
    return None


def _summary_prompt(batch: list[tuple[str, str, list[str]]], tier: str = "source") -> str:
    limit = SUMMARY_TIERS[tier][0]
    files_text = "\n\n".join(
        f"### {path}\n"
        + (f"Depends on (imports): {', '.join(deps)}\n" if deps else "")
        + f"```\n{content[:limit]}\n```"
        for path, content, deps in batch
    )
    if tier == "brief":
        ask = (
            "For each file below, write a summary of its role in the project in ONE or TWO "
            "sentences. These are supporting files (docs, configuration, data, tests, templates): "
            "say what the file is for and anything about it a newcomer would not guess, and stop."
        )
        guidance = ""
    else:
        ask = "For each file below, write a one-paragraph summary of its role in the codebase."
        guidance = """
Where a file's dependencies are listed, use them to ground *why* this file
is scoped the way it is — what responsibility it holds itself versus what
it delegates to the files it depends on. That's more valuable than a plain
restatement of the code.
"""
    return f"""{ask}

{AUDIENCE_FRAMING}
{guidance}
Return ONLY a JSON array of objects with "path" and "summary" fields, one per
file, no other text.

Files:
{files_text}
"""


def _parse_summaries(raw: str) -> dict[str, str] | None:
    import json

    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    for candidate in (cleaned, cleaned[cleaned.find("[") : cleaned.rfind("]") + 1] if "[" in cleaned else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return {
                str(item["path"]): str(item["summary"])
                for item in data
                if isinstance(item, dict) and item.get("path") and item.get("summary")
            }
    # Not valid JSON as a whole — typically cut off by the token cap. The
    # entries before the cut are still good: take every complete object, and
    # let the caller re-ask only for the files that are missing.
    salvaged: dict[str, str] = {}
    for match in re.finditer(r'\{\s*"path"\s*:\s*("(?:[^"\\]|\\.)*")\s*,\s*"summary"\s*:\s*("(?:[^"\\]|\\.)*")\s*\}', cleaned):
        try:
            salvaged[json.loads(match.group(1))] = json.loads(match.group(2))
        except json.JSONDecodeError:
            continue
    return salvaged or None


def _plan_batches(entries: list[tuple[str, str, list[str]]], tier: str) -> list[list[tuple[str, str, list[str]]]]:
    limit, max_files, budget = SUMMARY_TIERS[tier]
    batches: list[list[tuple[str, str, list[str]]]] = []
    current: list[tuple[str, str, list[str]]] = []
    used = 0
    for entry in entries:
        size = min(len(entry[1]), limit) + 120
        if current and (len(current) >= max_files or used + size > budget):
            batches.append(current)
            current, used = [], 0
        current.append(entry)
        used += size
    if current:
        batches.append(current)
    return batches


def summarize_files(
    repo_root: Path,
    nodes: list[ParsedNode],
    reporter: Reporter | None = None,
    on_batch: Callable[[dict[str, str]], None] | None = None,
) -> dict[str, str]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    summaries: dict[str, str] = {}
    dependents: dict[str, int] = {}
    for n in nodes:
        for dep in n.dependencies:
            dependents[dep] = dependents.get(dep, 0) + 1

    tiers: dict[str, list[tuple[str, str, list[str]]]] = {"source": [], "brief": []}
    templated: dict[str, str] = {}
    for n in nodes:
        try:
            text = (repo_root / n.id).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        template = _templated_summary(n.id, text)
        if template is not None:
            templated[n.id] = template
            continue
        tier = "source" if resolver_for(n.id) is not None else "brief"
        tiers[tier].append((n.id, text, n.dependencies))

    # Hubs first: what the most files depend on, then what depends on the most.
    for entries in tiers.values():
        entries.sort(key=lambda e: -(dependents.get(e[0], 0) * 2 + len(e[2])))

    total = len(nodes)
    done = len(templated)
    if reporter:
        reporter.start("summaries", total=total)
    if templated:
        summaries.update(templated)
        if reporter:
            reporter.advance("summaries", done)
            reporter.event(f"{len(templated)} boilerplate/empty files summarized without the model")
        if on_batch:
            on_batch(templated)

    plan = [(tier, batch) for tier in ("source", "brief") for batch in _plan_batches(tiers[tier], tier)]

    def run(tier: str, batch: list[tuple[str, str, list[str]]]) -> dict[str, str]:
        prompt = _summary_prompt(batch, tier)
        # ~350 tokens covers a paragraph, ~110 a sentence or two; doubled for
        # slack. A fuse against runaway generation, not a length target.
        budget = 600 + len(batch) * (700 if tier == "source" else 220)
        result: dict[str, str] = {}
        # A malformed reply must not blank the whole batch: keep whatever
        # parsed, and ask once more for only the files still missing.
        remaining = batch
        for _ in range(2):
            ask = prompt if remaining is batch else _summary_prompt(remaining, tier)
            parsed = _parse_summaries(call_with_retry(lambda: call_llm(ask, think="low", max_tokens=budget)))
            if parsed:
                result.update(parsed)
            remaining = [entry for entry in batch if entry[0] not in result]
            if not remaining:
                break
        return {path: result.get(path, SUMMARY_UNAVAILABLE) for path, _, _ in batch}

    if plan:
        with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as pool:
            futures = {pool.submit(run, tier, batch): batch for tier, batch in plan}
            for future in as_completed(futures):
                batch_result = future.result()
                summaries.update(batch_result)
                done += len(batch_result)
                if reporter:
                    reporter.advance("summaries", done)
                    names = [Path(path).name for path in batch_result][:4]
                    more = len(batch_result) - len(names)
                    reporter.event("summarized " + ", ".join(names) + (f" +{more} more" if more > 0 else ""), kind="summary")
                if on_batch:
                    on_batch(batch_result)
    if reporter:
        reporter.finish("summaries", f"{total} files in {len(plan)} model calls")
    return summaries


def build_folder_summary(folder_id: str, child_ids: list[str]) -> str:
    names = ", ".join(Path(c).name for c in child_ids[:8])
    suffix = "…" if len(child_ids) > 8 else ""
    return f"Folder containing {len(child_ids)} file(s): {names}{suffix}."


def _find_readme(repo_root: Path) -> str | None:
    for candidate_name in OVERVIEW_README_NAMES:
        candidate = repo_root / candidate_name
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return None
    return None


def _overview_prompt(owner: str, name: str, readme: str | None, folder_summaries: list[str]) -> str:
    if readme:
        source = f"Its README:\n```\n{readme[:MAX_README_CHARS_FOR_OVERVIEW]}\n```"
    else:
        source = "It has no README. Here's what its top-level folders contain:\n" + "\n".join(folder_summaries)
    return f"""Write a brief technical orientation to the GitHub project "{owner}/{name}".

{AUDIENCE_FRAMING}

{source}

Cover in 4-6 sentences across 2-3 short paragraphs (not a bulleted list):
- What problem this project solves and its core approach
- The shape of its architecture — major components/layers and how they
  relate — as far as you can infer from what's given
- Any design choices worth flagging as deliberate engineering tradeoffs,
  if evident from the source above (e.g. sync vs. async, monolith vs.
  services, a caching or batching strategy) — don't invent one if there's
  no real signal for it.

Keep each paragraph focused on one idea, and leave a blank line between paragraphs.
Write flowing prose — no headings, bullets, or links. Do wrap any identifier
in backticks (`Session`, `requests.get`, `urllib3`), so code reads as code
rather than running together with the prose.
"""


def generate_overview(
    repo_root: Path, owner: str, name: str, folder_summaries: list[str], reporter: Reporter | None = None
) -> str:
    if reporter:
        reporter.start("overview")
    prompt = _overview_prompt(owner, name, _find_readme(repo_root), folder_summaries)
    try:
        overview = call_with_retry(lambda: call_llm(prompt, think="low", max_tokens=1500)).strip()
    except PipelineError:
        overview = ""
    if reporter:
        reporter.finish("overview", None if overview else "skipped (model unavailable)")
        if overview:
            reporter.event("project overview written")
    return overview


def parse_structure(repo_url: str, workdir: Path, reporter: Reporter | None = None) -> tuple[Path, list[ParsedNode]]:
    if reporter:
        reporter.start("clone")
    repo_root = clone_repo(repo_url, workdir)
    files = discover_files(repo_root)
    if reporter:
        reporter.finish("clone", f"{len(files)} files")
        reporter.event(f"cloned — {len(files)} files worth showing")
        reporter.start("imports")
    dependencies = build_dependency_graph(repo_root, files)
    nodes = build_nodes(repo_root, files, dependencies)

    children_by_folder: dict[str, list[str]] = {}
    for n in nodes:
        if n.type == "file" and n.parent:
            children_by_folder.setdefault(n.parent, []).append(n.id)
    for n in nodes:
        if n.type == "folder":
            n.summary = build_folder_summary(n.id, children_by_folder.get(n.id, []))

    if reporter:
        edges = sum(len(d) for d in dependencies.values())
        linked = sum(1 for d in dependencies.values() if d)
        reporter.finish("imports", f"{edges} edges from {linked} files")
        reporter.event(f"resolved {edges} import edges across {len(children_by_folder)} folders")
    return repo_root, nodes


def folder_summary_lines(nodes: list[ParsedNode]) -> list[str]:
    return [f"- {n.id}: {n.summary}" for n in nodes if n.type == "folder"]
