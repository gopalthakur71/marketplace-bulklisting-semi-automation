import threading
import uuid
from dataclasses import dataclass, field

STEPS = ["Ingest CSV", "Map attributes", "Images → S3", "Fill & validate", "Ready"]


@dataclass
class Job:
    id: str
    status: str = "running"
    steps: list = field(default_factory=lambda: [
        {"name": n, "state": "pending", "count": None} for n in STEPS])
    result: dict | None = None
    error: str | None = None
    batch_id: str | None = None
    range: list | None = None
    cancel_requested: bool = False


class JobStore:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def create(self):
        job = Job(id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id):
        return self._jobs.get(job_id)

    def set_step(self, job_id, name, state, count=None):
        with self._lock:
            job = self._jobs[job_id]
            for s in job.steps:
                if s["name"] == name:
                    s["state"] = state
                    if count is not None:
                        s["count"] = count

    def finish(self, job_id, result):
        with self._lock:
            job = self._jobs[job_id]
            job.result = result
            job.status = "done"

    def request_cancel(self, job_id):
        """Ask a running build to stop. The worker notices at its next checkpoint,
        so the job stays 'running' until it actually winds down. Unknown ids are
        ignored — a Stop click can arrive after the job has already finished."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.cancel_requested = True

    def cancel(self, job_id):
        """The worker has stopped. Distinct from fail(): nothing went wrong."""
        with self._lock:
            job = self._jobs[job_id]
            job.status = "cancelled"

    def fail(self, job_id, error):
        with self._lock:
            job = self._jobs[job_id]
            job.error = error
            job.status = "error"

    def drop(self, job_id):
        """Forget a job entirely. Used by an upload that turned out unreadable and
        by the Preview screen's Clear."""
        with self._lock:
            return self._jobs.pop(job_id, None)


store = JobStore()
