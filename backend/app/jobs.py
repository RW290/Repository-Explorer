"""In-memory background job tracking for pipeline runs.

The pipeline takes minutes (clone + several paced/retried LLM calls), and
most hosting platforms time out a request well before that — so a deployed
instance must kick the run off in a background thread and let the client
poll for status, rather than blocking one HTTP request on the whole thing.

State is process-local (a plain dict), which is fine for a single instance;
it does not survive a restart or scale past one process. That's an
acceptable v1 tradeoff — the finished result is what gets cached to disk
(see pipeline.py), not the job's in-memory status.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field

from app.pipeline import run_pipeline

Status = str  # "pending" | "running" | "done" | "error"


@dataclass
class Job:
    id: str
    repo_url: str
    status: Status = "pending"
    stage: str = ""
    result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def start_job(repo_url: str, force_refresh: bool = False) -> Job:
    job = Job(id=str(uuid.uuid4()), repo_url=repo_url)
    with _lock:
        _jobs[job.id] = job

    def run() -> None:
        job.status = "running"
        try:
            job.result = run_pipeline(repo_url, force_refresh=force_refresh, on_stage=_make_reporter(job))
            job.status = "done"
        except Exception as e:
            job.error = _friendly_error(e)
            job.status = "error"

    threading.Thread(target=run, daemon=True).start()
    return job


def _make_reporter(job: Job):
    def report(stage: str) -> None:
        job.stage = stage

    return report


def _friendly_error(e: Exception) -> str:
    text = str(e)
    if "RESOURCE_EXHAUSTED" in text or ("429" in text and "quota" in text.lower()):
        return (
            "The free daily AI quota for this tool has been used up for today. "
            "This runs on Gemini's free tier by design (see the build brief) — "
            "try again after the quota resets, or use a repo that's already cached."
        )
    if "clone" in text.lower() and ("not found" in text.lower() or "128" in text):
        return "Could not clone that repository — check the URL and that it's public."
    return text


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)
