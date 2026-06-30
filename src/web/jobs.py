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

    def fail(self, job_id, error):
        with self._lock:
            job = self._jobs[job_id]
            job.error = error
            job.status = "error"


store = JobStore()
