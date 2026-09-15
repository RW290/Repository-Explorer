"""Architecture parser (phase 2): local static analysis, no LLM for the graph shape.

Clones the target repo, discovers every file worth showing, and builds a
file-level dependency graph from Python imports using the `ast` module —
not a full type-checker, just best-effort static resolution. Non-Python
files become nodes (and get LLM summaries) the same as Python files, but
never get dependency edges — there's no import-parsing for other
languages here. LLM calls are used only for the batched file summaries and
the project overview, never for the dependency edges themselves.
"""

import ast
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.audience import AUDIENCE_FRAMING
from app.errors import ConfigurationError, PipelineError, RepoCloneError
from app.llm import call_llm, call_with_retry

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
)
ROOT_FOLDER_ID = "(root)"
MAX_FILE_CHARS_FOR_SUMMARY = 3000
SUMMARY_BATCH_SIZE = 6
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


def _extract_import_statements(tree: ast.AST) -> list[tuple[str | None, int, list[str]]]:
    """Returns (module, relative_level, imported_names) for each import statement."""
    statements = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                statements.append((alias.name, 0, []))
        elif isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
            statements.append((node.module, node.level, names))
    return statements


def _resolve_import(
    current_rel: Path,
    module: str | None,
    level: int,
    imported_names: list[str],
    known_files: set[str],
) -> list[str]:
    """Best-effort mapping of one import statement to repo-relative paths already in known_files."""
    results: list[str] = []
    current_dir_parts = list(current_rel.parts[:-1])

    if level > 0:
        base_parts = current_dir_parts[: len(current_dir_parts) - (level - 1)] if level > 1 else current_dir_parts
        candidates = []
        if module:
            candidates.append(base_parts + module.split("."))
        else:
            for name in imported_names:
                candidates.append(base_parts + [name])
        for parts in candidates:
            candidate = "/".join(parts) + ".py"
            if candidate in known_files:
                results.append(candidate)
    else:
        if not module:
            return results
        parts = module.split(".")
        root_pkg = parts[0]
        for prefix in ("", "src/", "lib/"):
            base = f"{prefix}{'/'.join(parts)}"
            if f"{base}.py" in known_files:
                results.append(f"{base}.py")
            elif f"{base}/__init__.py" in known_files:
                results.append(f"{base}/__init__.py")
            elif len(parts) == 1:
                for name in imported_names:
                    candidate = f"{prefix}{root_pkg}/{name}.py"
                    if candidate in known_files:
                        results.append(candidate)
    return results


def build_dependency_graph(repo_root: Path, files: list[Path]) -> dict[str, list[str]]:
    """Every file gets an entry (possibly empty); only .py files get edges,
    since import resolution below is Python-specific."""
    known_files = {str(f.relative_to(repo_root)) for f in files}
    graph: dict[str, list[str]] = {str(f.relative_to(repo_root)): [] for f in files}
    for f in files:
        if f.suffix != ".py":
            continue
        rel = f.relative_to(repo_root)
        rel_str = str(rel)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        deps: set[str] = set()
        for module, level, names in _extract_import_statements(tree):
            for dep in _resolve_import(rel, module, level, names, known_files):
                if dep != rel_str:
                    deps.add(dep)
        graph[rel_str] = sorted(deps)
    return graph


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


def _summary_prompt(batch: list[tuple[str, str, list[str]]]) -> str:
    files_text = "\n\n".join(
        f"### {path}\n"
        + (f"Depends on (imports): {', '.join(deps)}\n" if deps else "")
        + f"```\n{content[:MAX_FILE_CHARS_FOR_SUMMARY]}\n```"
        for path, content, deps in batch
    )
    return f"""For each file below, write a one-paragraph summary of its role in the codebase.

{AUDIENCE_FRAMING}

Where a file's dependencies are listed, use them to ground *why* this file
is scoped the way it is — what responsibility it holds itself versus what
it delegates to the files it depends on. That's more valuable than a plain
restatement of the code.

Return ONLY a JSON array of objects with "path" and "summary" fields, no other text.

Files:
{files_text}
"""


def summarize_files(
    repo_root: Path, nodes: list[ParsedNode], on_stage: Callable[[str], None] | None = None
) -> dict[str, str]:
    import json
    import time

    summaries: dict[str, str] = {}
    contents = []
    for n in nodes:
        try:
            text = (repo_root / n.id).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        contents.append((n.id, text, n.dependencies))

    batch_count = -(-len(contents) // SUMMARY_BATCH_SIZE) if contents else 0
    for i in range(0, len(contents), SUMMARY_BATCH_SIZE):
        if i > 0:
            time.sleep(2)
        if on_stage:
            on_stage(f"summarizing files (batch {i // SUMMARY_BATCH_SIZE + 1}/{batch_count})")
        batch = contents[i : i + SUMMARY_BATCH_SIZE]
        prompt = _summary_prompt(batch)
        raw = call_with_retry(lambda: call_llm(prompt))
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            results = json.loads(cleaned)
            for item in results:
                summaries[item["path"]] = item["summary"]
        except (json.JSONDecodeError, KeyError, TypeError):
            for path, _, _ in batch:
                summaries.setdefault(path, "Summary unavailable (LLM response could not be parsed).")
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

Cover in 4-6 sentences of flowing prose (not a bulleted list):
- What problem this project solves and its core approach
- The shape of its architecture — major components/layers and how they
  relate — as far as you can infer from what's given
- Any design choices worth flagging as deliberate engineering tradeoffs,
  if evident from the source above (e.g. sync vs. async, monolith vs.
  services, a caching or batching strategy) — don't invent one if there's
  no real signal for it.
"""


def generate_overview(repo_root: Path, owner: str, name: str, folder_summaries: list[str]) -> str:
    """Best-effort: an overview is a nice-to-have orientation, not core to the
    graph, so a transient LLM failure degrades to an empty string rather than
    failing the whole analysis."""
    prompt = _overview_prompt(owner, name, _find_readme(repo_root), folder_summaries)
    try:
        return call_with_retry(lambda: call_llm(prompt)).strip()
    except PipelineError:
        return ""


def run_parser(
    repo_url: str, owner: str, name: str, workdir: Path, on_stage: Callable[[str], None] | None = None
) -> tuple[list[ParsedNode], str]:
    if on_stage:
        on_stage("cloning repository")
    repo_root = clone_repo(repo_url, workdir)
    files = discover_files(repo_root)
    if on_stage:
        on_stage(f"analyzing {len(files)} files")
    dependencies = build_dependency_graph(repo_root, files)
    nodes = build_nodes(repo_root, files, dependencies)

    file_nodes = [n for n in nodes if n.type == "file"]
    summaries = summarize_files(repo_root, file_nodes, on_stage=on_stage)
    for n in file_nodes:
        n.summary = summaries.get(n.id, "")

    children_by_folder: dict[str, list[str]] = {}
    for n in file_nodes:
        if n.parent:
            children_by_folder.setdefault(n.parent, []).append(n.id)
    folder_summaries = []
    for n in nodes:
        if n.type == "folder":
            n.summary = build_folder_summary(n.id, children_by_folder.get(n.id, []))
            folder_summaries.append(f"- {n.id}: {n.summary}")

    if on_stage:
        on_stage("writing project overview")
    overview = generate_overview(repo_root, owner, name, folder_summaries)

    return nodes, overview
