"""Live progress for the two long teacher-facing actions: analysis and plan generation.

The browser sends a run id with the request and polls for the current stage, so the
teacher always sees what LessonFlow is doing instead of an unexplained wait.
"""

from __future__ import annotations

import contextvars
import threading
import time
from typing import Any

_MAX_RUNS = 40
_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}


def start(run_id: str, message: str, plan: list[tuple[str, str]] | None = None) -> None:
    """Begin a run, listing every step up front so the teacher sees what is still to come."""
    if not run_id:
        return
    with _lock:
        _runs[run_id] = {
            "percent": 1,
            "message": message,
            "steps": [
                {"key": key, "headline": headline, "detail": "", "status": "pending"}
                for key, headline in (plan or [])
            ],
            "done": False,
            "failed": False,
            "updated_at": time.time(),
        }
        _prune()


def update(run_id: str, percent: int, message: str) -> None:
    if not run_id:
        return
    with _lock:
        run = _runs.get(run_id)
        if run is None or run["done"]:
            return
        # Progress only ever moves forward so the bar never jumps backwards.
        run["percent"] = max(int(run["percent"]), min(99, int(percent)))
        run["message"] = message
        run["updated_at"] = time.time()


def activate(run_id: str, key: str, percent: int, message: str) -> None:
    """Mark the step now running, so the checklist shows where LessonFlow currently is."""
    if not run_id:
        return
    update(run_id, percent, message)
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        for step in run["steps"]:
            if step["key"] == key and step["status"] == "pending":
                step["status"] = "active"
        run["updated_at"] = time.time()


def note(run_id: str, key: str, detail: str = "", headline: str = "") -> None:
    """Tick a step off and record what it actually produced."""
    if not run_id:
        return
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        for step in run["steps"]:
            if step["key"] == key:
                step["status"] = "done"
                step["detail"] = detail
                if headline:
                    step["headline"] = headline
                break
        else:
            run["steps"].append(
                {"key": key, "headline": headline or key, "detail": detail, "status": "done"}
            )
        run["updated_at"] = time.time()


def skip(run_id: str, key: str, detail: str = "") -> None:
    """Mark a step the teacher's settings turned off, rather than hiding it."""
    if not run_id:
        return
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        for step in run["steps"]:
            if step["key"] == key:
                step["status"] = "skipped"
                step["detail"] = detail
                break
        run["updated_at"] = time.time()


def finish(run_id: str, message: str = "Finished.") -> None:
    if not run_id:
        return
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        for step in run["steps"]:
            if step["status"] == "active":
                step["status"] = "done"
        run.update({"percent": 100, "message": message, "done": True, "updated_at": time.time()})


def fail(run_id: str, message: str) -> None:
    if not run_id:
        return
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        for step in run["steps"]:
            if step["status"] == "active":
                step["status"] = "failed"
        run.update({"message": message, "done": True, "failed": True, "updated_at": time.time()})


def read(run_id: str) -> dict[str, Any] | None:
    with _lock:
        run = _runs.get(run_id)
        return dict(run) if run else None


def _prune() -> None:
    if len(_runs) <= _MAX_RUNS:
        return
    for stale in sorted(_runs, key=lambda key: _runs[key]["updated_at"])[: len(_runs) - _MAX_RUNS]:
        _runs.pop(stale, None)


# The run currently being served, so deep code (such as provider fallback) can report
# progress without threading a run id through every function signature.
active_run: contextvars.ContextVar[str] = contextvars.ContextVar("active_run", default="")


def set_active(run_id: str) -> None:
    active_run.set(run_id or "")


def detail(message: str) -> None:
    """Update the message of the run being served, if there is one."""
    run_id = active_run.get()
    if not run_id:
        return
    with _lock:
        run = _runs.get(run_id)
        if run is None or run["done"]:
            return
        run["message"] = message
        run["updated_at"] = time.time()
