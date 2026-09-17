"""Persisted, shared rationale annotations, per repo — three kinds, one
storage shape.

Separate from pipeline.py's analysis cache (nodes/PR annotations, written
once per analysis run and never touched again): these accumulate one entry
at a time, whenever a viewer asks "why," and are meant to grow indefinitely
rather than be regenerated. Shared across everyone who opens the same repo,
like a standing margin-comment thread rather than a private note.

Line rationale (path + line range), file rationale (path only), and
project rationale (neither) are stored in separate per-repo files so they
never collide, but share the same read-modify-write logic.
"""

import json
import threading
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"

# One lock for all repos: writes are infrequent (a human clicking "ask why")
# and each is a full read-modify-write of a small per-repo file, so there's
# no throughput reason to shard locks per repo.
_lock = threading.Lock()


def _store_path(owner: str, name: str, kind: str) -> Path:
    return CACHE_DIR / f"{owner}__{name}.{kind}.json"


def _load(owner: str, name: str, kind: str, path: str | None) -> list[dict]:
    store_path = _store_path(owner, name, kind)
    with _lock:
        entries = json.loads(store_path.read_text()) if store_path.exists() else []
    if path is not None:
        entries = [e for e in entries if e["path"] == path]
    return entries


def _add(owner: str, name: str, kind: str, entry: dict) -> None:
    store_path = _store_path(owner, name, kind)
    with _lock:
        entries = json.loads(store_path.read_text()) if store_path.exists() else []
        entries.append(entry)
        CACHE_DIR.mkdir(exist_ok=True)
        store_path.write_text(json.dumps(entries, indent=2))


def load_rationales(owner: str, name: str, path: str | None = None) -> list[dict]:
    return _load(owner, name, "rationales", path)


def add_rationale(owner: str, name: str, entry: dict) -> None:
    _add(owner, name, "rationales", entry)


def load_file_rationales(owner: str, name: str, path: str | None = None) -> list[dict]:
    return _load(owner, name, "file_rationales", path)


def add_file_rationale(owner: str, name: str, entry: dict) -> None:
    _add(owner, name, "file_rationales", entry)


def load_project_rationales(owner: str, name: str) -> list[dict]:
    return _load(owner, name, "project_rationales", None)


def add_project_rationale(owner: str, name: str, entry: dict) -> None:
    _add(owner, name, "project_rationales", entry)


def load_symbol_explainers(owner: str, name: str, path: str | None = None) -> list[dict]:
    return _load(owner, name, "symbol_explainers", path)


def replace_symbol_explainers(owner: str, name: str, path: str, entries: list[dict]) -> None:
    """Explainers are generated for a whole file at once, so they're
    replaced as a set for that path rather than appended one at a time."""
    store_path = _store_path(owner, name, "symbol_explainers")
    with _lock:
        existing = json.loads(store_path.read_text()) if store_path.exists() else []
        kept = [e for e in existing if e["path"] != path]
        CACHE_DIR.mkdir(exist_ok=True)
        store_path.write_text(json.dumps(kept + entries, indent=2))
