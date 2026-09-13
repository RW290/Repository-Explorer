"""Persisted, shared line-level rationale annotations, per repo.

Separate from pipeline.py's analysis cache (nodes/PR annotations, written
once per analysis run and never touched again): these accumulate one entry
at a time, whenever a viewer highlights some code and asks why it's there,
and are meant to grow indefinitely rather than be regenerated. Shared
across everyone who opens the same repo, like a standing margin-comment
thread rather than a private note.
"""

import json
import threading
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"

# One lock for all repos: writes are infrequent (a human clicking "ask why")
# and each is a full read-modify-write of a small per-repo file, so there's
# no throughput reason to shard locks per repo.
_lock = threading.Lock()


def _store_path(owner: str, name: str) -> Path:
    return CACHE_DIR / f"{owner}__{name}.rationales.json"


def load_rationales(owner: str, name: str, path: str | None = None) -> list[dict]:
    store_path = _store_path(owner, name)
    with _lock:
        entries = json.loads(store_path.read_text()) if store_path.exists() else []
    if path is not None:
        entries = [e for e in entries if e["path"] == path]
    return entries


def add_rationale(owner: str, name: str, entry: dict) -> None:
    store_path = _store_path(owner, name)
    with _lock:
        entries = json.loads(store_path.read_text()) if store_path.exists() else []
        entries.append(entry)
        CACHE_DIR.mkdir(exist_ok=True)
        store_path.write_text(json.dumps(entries, indent=2))
