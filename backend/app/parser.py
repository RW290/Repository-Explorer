"""Architecture parser (phase 2): local static analysis, no LLM for the graph shape.

Clones the target repo, discovers Python files, and builds a file-level
dependency graph from imports using the `ast` module — not a full
type-checker, just best-effort static resolution. LLM calls are used only
for the batched file summaries, never for the dependency edges themselves.
"""

import ast
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.llm import call_llm, call_with_retry

EXCLUDE_DIR_NAMES = {".git", "__pycache__", "node_modules", ".venv", "venv", "docs", "examples"}
ROOT_FOLDER_ID = "(root)"
MAX_FILE_CHARS_FOR_SUMMARY = 3000
SUMMARY_BATCH_SIZE = 6


@dataclass
class ParsedNode:
    id: str
    type: str  # "file" | "folder"
    parent: str | None
    dependencies: list[str] = field(default_factory=list)
    summary: str = ""


def clone_repo(repo_url: str, workdir: Path) -> Path:
    dest = workdir / "repo"
    subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )
    return dest


def discover_python_files(repo_root: Path) -> list[Path]:
    files = []
    for path in repo_root.rglob("*.py"):
        rel = path.relative_to(repo_root)
        if any(part in EXCLUDE_DIR_NAMES for part in rel.parts):
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
    known_files = {str(f.relative_to(repo_root)) for f in files}
    graph: dict[str, list[str]] = {}
    for f in files:
        rel = f.relative_to(repo_root)
        rel_str = str(rel)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            graph[rel_str] = []
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


def _summary_prompt(batch: list[tuple[str, str]]) -> str:
    files_text = "\n\n".join(
        f"### {path}\n```\n{content[:MAX_FILE_CHARS_FOR_SUMMARY]}\n```" for path, content in batch
    )
    return f"""For each file below, write a one-paragraph summary of its role in the codebase.

Write for someone who has never seen this codebase and isn't a programmer —
a curious non-technical reader. Explain what the file is FOR in plain,
everyday language, as if describing it to a friend. Avoid unexplained
jargon: don't assume the reader knows terms like "API", "WebSocket",
"middleware", "ORM", "async", or similar. If a technical term is
unavoidable, briefly explain what it means in plain words right there in
the sentence. Prefer concrete, everyday analogies over technical precision.

Return ONLY a JSON array of objects with "path" and "summary" fields, no other text.

Files:
{files_text}
"""


def summarize_files(
    repo_root: Path, files: list[Path], on_stage: Callable[[str], None] | None = None
) -> dict[str, str]:
    import json
    import time

    summaries: dict[str, str] = {}
    contents = []
    for f in files:
        rel = str(f.relative_to(repo_root))
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        contents.append((rel, text))

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
            for path, _ in batch:
                summaries.setdefault(path, "Summary unavailable (LLM response could not be parsed).")
    return summaries


def build_folder_summary(folder_id: str, child_ids: list[str]) -> str:
    names = ", ".join(Path(c).name for c in child_ids[:8])
    suffix = "…" if len(child_ids) > 8 else ""
    return f"Folder containing {len(child_ids)} file(s): {names}{suffix}."


def run_parser(repo_url: str, workdir: Path, on_stage: Callable[[str], None] | None = None) -> list[ParsedNode]:
    if on_stage:
        on_stage("cloning repository")
    repo_root = clone_repo(repo_url, workdir)
    files = discover_python_files(repo_root)
    if on_stage:
        on_stage(f"analyzing {len(files)} files")
    dependencies = build_dependency_graph(repo_root, files)
    nodes = build_nodes(repo_root, files, dependencies)

    file_nodes = [n for n in nodes if n.type == "file"]
    summaries = summarize_files(repo_root, [repo_root / n.id for n in file_nodes], on_stage=on_stage)
    for n in file_nodes:
        n.summary = summaries.get(n.id, "")

    children_by_folder: dict[str, list[str]] = {}
    for n in file_nodes:
        if n.parent:
            children_by_folder.setdefault(n.parent, []).append(n.id)
    for n in nodes:
        if n.type == "folder":
            n.summary = build_folder_summary(n.id, children_by_folder.get(n.id, []))

    return nodes
