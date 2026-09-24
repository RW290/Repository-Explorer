"""In-memory background job tracking for pipeline runs.

The pipeline takes minutes (clone + several paced/retried LLM calls), and
most hosting platforms time out a request well before that — so a deployed
instance must kick the run off in a background thread and let the client
poll for status, rather than blocking one HTTP request on the whole thing.

State is process-local (a plain dict), which is fine for a single instance;
it does not survive a restart or scale past one process. That's an
acceptable tradeoff — the finished result is what gets cached to disk
(see pipeline.py), not the job's in-memory status.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field

from app.errors import PipelineError
from app.pipeline import refresh_dependencies, run_pipeline
from app.progress import Reporter

Status = str  # "pending" | "running" | "done" | "error"


@dataclass
class Job:
    id: str
    repo_url: str
    status: Status = "pending"
    result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    # Live stage tracker + partial graph, written by the pipeline thread and
    # read by polls (see progress.py).
    reporter: Reporter = field(default_factory=Reporter)

    @property
    def stage(self) -> str:
        return self.reporter.text


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def start_job(repo_url: str, force_refresh: bool = False, refresh_edges_only: bool = False) -> Job:
    job = Job(id=str(uuid.uuid4()), repo_url=repo_url)
    with _lock:
        _jobs[job.id] = job

    def run() -> None:
        job.status = "running"
        try:
            if refresh_edges_only:
                job.result = refresh_dependencies(repo_url, on_stage=job.reporter.note)
            else:
                job.result = run_pipeline(repo_url, force_refresh=force_refresh, reporter=job.reporter)
            job.status = "done"
        except Exception as e:
            job.error = _friendly_error(e)
            job.status = "error"

    threading.Thread(target=run, daemon=True).start()
    return job


def _friendly_error(e: Exception) -> str:
    if isinstance(e, PipelineError):
        return str(e)
    detail = str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__
    return (
        "Something went wrong inside this tool while analyzing that repository — "
        f"this is a bug, not something you did. Technical detail: {detail}"
    )


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)
