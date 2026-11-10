"""Background job queue: jobs run one at a time, links within a job run in parallel."""

import json
import queue
import threading
import uuid
from pathlib import Path

from .importer import ImportOptions, SlinkClient, StateStore, now, run_import

HISTORY_LIMIT = 25


class JobManager:
    def __init__(self, client: SlinkClient, state: StateStore, history_path: Path):
        self.client = client
        self.state = state
        self.history_path = history_path
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._load_history()
        threading.Thread(target=self._worker, daemon=True, name="job-runner").start()

    def _load_history(self) -> None:
        if not self.history_path.exists():
            return
        for job in json.loads(self.history_path.read_text()):
            if job["status"] in ("queued", "running"):
                job["status"] = "interrupted"
                for item in job["items"]:
                    if item["status"] in ("pending", "running"):
                        item["status"] = "failed"
                        item["error"] = "interrupted by a server restart"
            self._jobs[job["id"]] = job

    def _save_history(self) -> None:
        # Caller holds self._lock
        jobs = sorted(self._jobs.values(), key=lambda j: j["created"])[-HISTORY_LIMIT:]
        self._jobs = {job["id"]: job for job in jobs}
        tmp = self.history_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(jobs))
        tmp.replace(self.history_path)

    def create(
        self, links: list[str], options: ImportOptions, workers: int, force: bool
    ) -> dict:
        items = []
        for link in links:
            previous = None if force else self.state.get(link)
            if previous and previous.get("status", "ok") == "ok":
                items.append({"link": link, "status": "skipped", "url": previous.get("url")})
            else:
                items.append({"link": link, "status": "pending"})
        job = {
            "id": uuid.uuid4().hex[:12],
            "created": now(),
            "finished": None,
            "status": "queued",
            "workers": workers,
            "options": {
                "public": options.public,
                "description": options.description,
                "collectionIds": list(options.collection_ids),
            },
            "items": items,
        }
        with self._lock:
            self._jobs[job["id"]] = job
            self._save_history()
        self._queue.put(job["id"])
        return self.get(job["id"])

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return with_counts(json.loads(json.dumps(job))) if job else None

    def list(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["created"], reverse=True)
            return [
                with_counts({k: v for k, v in job.items() if k != "items"}, job["items"])
                for job in jobs
            ]

    def failures(self, job_id: str) -> dict[str, str] | None:
        job = self.get(job_id)
        if job is None:
            return None
        return {i["link"]: i.get("error", "") for i in job["items"] if i["status"] == "failed"}

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "running"
                index = {item["link"]: item for item in job["items"]}
                pending = [i["link"] for i in job["items"] if i["status"] == "pending"]
                o = job["options"]
                options = ImportOptions(o["public"], o["description"], tuple(o["collectionIds"]))
                workers = job["workers"]

            def on_update(link: str, entry: dict) -> None:
                with self._lock:
                    index[link].update(entry)
                    if entry["status"] != "running":
                        self._save_history()

            try:
                run_import(self.client, self.state, pending, options, workers, on_update)
                status = "done"
            except Exception:
                status = "error"
            with self._lock:
                job["status"] = status
                job["finished"] = now()
                self._save_history()


def with_counts(job: dict, items: list[dict] | None = None) -> dict:
    counts = {"total": 0, "pending": 0, "running": 0, "ok": 0, "failed": 0, "skipped": 0}
    for item in items if items is not None else job["items"]:
        counts["total"] += 1
        counts[item["status"]] += 1
    job["counts"] = counts
    return job
