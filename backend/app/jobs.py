"""A small in-process job runner for calibration work.

Calculating a mapping takes milliseconds, but it is presented as a job anyway so
the UI has one honest pattern: start work, watch progress, read the result or a
plain-language failure. That keeps the interface the same if a future step (a
long marker sweep, a many-view lens calibration) genuinely takes a while.

Jobs live in memory and belong to this process. They are not a durable queue,
and a restart forgets them — which is fine, because every job either finished
and wrote to the database, or failed and left nothing behind.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger("numenor.jobs")

JOB_RETENTION_S = 900        # keep finished jobs long enough for the UI to read them
MAX_JOBS = 200


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"              # queued | running | succeeded | failed
    progress: float = 0.0               # 0..1
    step: str = "Waiting to start"
    result: dict | None = None
    error: str | None = None
    hint: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": round(self.progress, 3),
            "step": self.step,
            "result": self.result,
            "error": self.error,
            "hint": self.hint,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class JobRunner:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _prune(self) -> None:
        now = time.time()
        stale = [j.id for j in self._jobs.values()
                 if j.finished_at and now - j.finished_at > JOB_RETENTION_S]
        for job_id in stale:
            self._jobs.pop(job_id, None)
        if len(self._jobs) > MAX_JOBS:
            oldest = sorted(self._jobs.values(), key=lambda j: j.created_at)
            for job in oldest[: len(self._jobs) - MAX_JOBS]:
                self._jobs.pop(job.id, None)

    def start(self, kind: str,
              work: Callable[[Job], Awaitable[dict]]) -> Job:
        """Schedule ``work``. It receives the job so it can report progress."""
        self._prune()
        job = Job(id=secrets.token_urlsafe(10), kind=kind)
        self._jobs[job.id] = job

        async def run() -> None:
            job.status = "running"
            job.step = "Starting"
            try:
                job.result = await work(job)
                job.status = "succeeded"
                job.progress = 1.0
                job.step = "Finished"
            except Exception as exc:                  # surfaced to the UI, not swallowed
                job.status = "failed"
                job.error = getattr(exc, "message", None) or str(exc)
                job.hint = getattr(exc, "hint", None)
                job.step = "Failed"
                log.info("Job %s (%s) failed: %s", job.id, kind, job.error)
            finally:
                job.finished_at = time.time()

        asyncio.create_task(run())
        return job


runner = JobRunner()
