"""Live progress and partial results for a running analysis.

An analysis is minutes of mostly-waiting on a rate-limited LLM. Two things
make that tolerable, and this module carries both from the pipeline thread
to whoever is polling:

- *What is happening*: a fixed list of stages, each pending/running/done
  with a done/total count where one exists, plus a short feed of recent
  events ("summarized sessions.py"). Fixed, so the UI can draw the whole
  road ahead from the first poll rather than discovering stages as they
  appear.
- *What exists so far*: the partial graph. Structure (files, folders,
  import edges) is known seconds in, long before any summary is written, so
  it's published immediately and re-published as summaries land. The reader
  explores a real graph while the slow part fills it in.

Partial graphs are versioned. A poll says which version it already has and
gets the (large) graph back only when it changed — the difference between
re-sending ~100KB every second and sending it a dozen times in total.
"""

import copy
import threading
import time
from collections import deque

# (key, label) in the order the UI lists them. Some overlap in time — history
# and the overview run alongside the summaries — which is why each carries
# its own status instead of there being one "current stage".
STAGES: list[tuple[str, str]] = [
    ("clone", "Clone repository"),
    ("imports", "Resolve imports"),
    ("summaries", "Summarize files"),
    ("history", "Mine pull requests"),
    ("overview", "Write project overview"),
    ("map", "Map architecture"),
]


class Reporter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = time.time()
        self._stages = {
            key: {"key": key, "label": label, "status": "pending", "detail": None, "done": None, "total": None, "started": None, "seconds": None}
            for key, label in STAGES
        }
        self._events: deque[dict] = deque(maxlen=14)
        self._partial: dict | None = None
        self._partial_version = 0
        self.text = ""

    # -- stages ------------------------------------------------------------

    def start(self, key: str, detail: str | None = None, total: int | None = None) -> None:
        with self._lock:
            stage = self._stages[key]
            stage.update(status="running", detail=detail, total=total, done=0 if total else None, started=time.time())
            self.text = self._describe(stage)

    def advance(self, key: str, done: int, total: int | None = None, detail: str | None = None) -> None:
        with self._lock:
            stage = self._stages[key]
            stage["done"] = done
            if total is not None:
                stage["total"] = total
            if detail is not None:
                stage["detail"] = detail
            self.text = self._describe(stage)

    def finish(self, key: str, detail: str | None = None, status: str = "done") -> None:
        with self._lock:
            stage = self._stages[key]
            stage["status"] = status
            if detail is not None:
                stage["detail"] = detail
            if stage["total"] is not None and status == "done":
                stage["done"] = stage["total"]
            if stage["started"]:
                stage["seconds"] = round(time.time() - stage["started"], 1)

    def note(self, text: str) -> None:
        with self._lock:
            self.text = text

    def event(self, text: str, kind: str = "info") -> None:
        with self._lock:
            self._events.append({"t": round(time.time() - self._started, 1), "text": text, "kind": kind})

    @staticmethod
    def _describe(stage: dict) -> str:
        label = stage["label"].lower()
        if stage["total"]:
            return f"{label} ({stage['done']}/{stage['total']})"
        return f"{label}{': ' + stage['detail'] if stage['detail'] else ''}"

    # -- partial graph -----------------------------------------------------

    def publish(self, graph: dict) -> None:
        snapshot = copy.deepcopy(graph)
        with self._lock:
            self._partial = snapshot
            self._partial_version += 1

    @property
    def partial_version(self) -> int:
        return self._partial_version

    def partial(self) -> dict | None:
        return self._partial

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            stages = []
            for key, _ in STAGES:
                stage = dict(self._stages[key])
                if stage["status"] == "running" and stage["started"]:
                    stage["seconds"] = round(now - stage["started"], 1)
                stage.pop("started")
                stages.append(stage)
            return {
                "elapsed": round(now - self._started, 1),
                "stages": stages,
                "events": list(self._events),
                "partial_version": self._partial_version,
            }
