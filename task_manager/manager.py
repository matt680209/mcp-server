from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
import uuid
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


ProgressCallback = Callable[[int, str], Awaitable[None]]
TaskHandler = Callable[["Task", ProgressCallback], Awaitable[dict[str, Any]]]
ACTIVE_STATUSES = {"QUEUED", "RUNNING"}


@dataclass
class Task:
    task_id: str
    session_id: str
    kind: str
    created_at: float
    work_dir: str
    payload: dict[str, Any]
    timeout_seconds: int
    status: str = "QUEUED"
    progress: int = 0
    message: str = "Waiting in queue"
    result: dict[str, Any] | None = None
    error: str | None = None
    updated_at: float = field(default_factory=time.time)


class TaskLimitError(Exception):
    pass


class TaskNotFoundError(Exception):
    pass


class TaskManager:
    def __init__(
        self,
        handler: TaskHandler,
        root: Path,
        max_concurrency: int = 15,
        per_session_limit: int = 3,
        max_sessions: int = 5,
        max_queue: int = 100,
        app_concurrency_limits: dict[str, int] | None = None,
    ) -> None:
        self.handler = handler
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_concurrency = max_concurrency
        self.per_session_limit = per_session_limit
        self.max_sessions = max_sessions
        self.max_queue = max_queue
        self.app_concurrency_limits = dict(app_concurrency_limits or {})
        self.tasks: dict[str, Task] = {}
        self.queue: deque[str] = deque()
        self.running: dict[str, asyncio.Task[None]] = {}
        self.subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self.lock = asyncio.Lock()
        self._restore()

    async def submit(
        self,
        session_id: str,
        kind: str,
        payload: dict[str, Any],
        timeout_seconds: int = 900,
    ) -> Task:
        async with self.lock:
            active = [task for task in self.tasks.values() if task.status in ACTIVE_STATUSES]
            session_tasks = [task for task in active if task.session_id == session_id]
            active_sessions = {task.session_id for task in active}
            if len(session_tasks) >= self.per_session_limit:
                raise TaskLimitError("This session already has the maximum number of active tasks.")
            if session_id not in active_sessions and len(active_sessions) >= self.max_sessions:
                raise TaskLimitError("The service is at its active-session limit.")
            if len(self.queue) >= self.max_queue:
                raise TaskLimitError("The task queue is full. Please try again later.")

            task_id = uuid.uuid4().hex
            session_dir = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
            work_dir = self.root / session_dir / f"TASK-{task_id[:12]}"
            work_dir.mkdir(parents=True, exist_ok=False)
            task = Task(
                task_id=task_id,
                session_id=session_id,
                kind=kind,
                created_at=time.time(),
                work_dir=str(work_dir),
                payload={
                    **payload,
                    "result_dir": str(payload.get("result_dir", "")).replace("{task_id}", task_id),
                },
                timeout_seconds=timeout_seconds,
            )
            self.tasks[task_id] = task
            self.queue.append(task_id)
            self._persist(task)
            await self._publish(task)
            self._schedule_locked()
            return task

    def _schedule_locked(self) -> None:
        while self.queue and len(self.running) < self.max_concurrency:
            task_id = self._take_next_schedulable()
            if task_id is None:
                break
            task = self.tasks.get(task_id)
            if task is None or task.status != "QUEUED":
                continue
            task.status = "RUNNING"
            task.message = "Processing"
            task.updated_at = time.time()
            self._persist(task)
            self.running[task_id] = asyncio.create_task(self._run(task))
            asyncio.create_task(self._publish(task))

    def _take_next_schedulable(self) -> str | None:
        running_counts = Counter(
            str(self.tasks[task_id].payload.get("application_id", "__default__"))
            for task_id in self.running
            if task_id in self.tasks
        )
        avatar_running = any(
            self.tasks[task_id].payload.get("application_adapter") == "intelavatar"
            for task_id in self.running
            if task_id in self.tasks
        )
        for index, task_id in enumerate(self.queue):
            task = self.tasks.get(task_id)
            if task is None or task.status != "QUEUED":
                del self.queue[index]
                return self._take_next_schedulable()
            app_id = str(task.payload.get("application_id", "__default__"))
            app_limit = self.app_concurrency_limits.get(app_id, self.max_concurrency)
            uses_avatar = task.payload.get("application_adapter") == "intelavatar"
            if running_counts[app_id] < app_limit and not (uses_avatar and avatar_running):
                del self.queue[index]
                return task_id
        return None

    async def _run(self, task: Task) -> None:
        try:
            task.result = await asyncio.wait_for(
                self.handler(task, lambda progress, message: self._set_progress(task, progress, message)),
                timeout=task.timeout_seconds,
            )
            task.status = "SUCCEEDED"
            task.progress = 100
            task.message = "Completed"
        except asyncio.TimeoutError:
            task.status = "TIMED_OUT"
            task.error = "Task timed out and was stopped."
            task.message = task.error
        except asyncio.CancelledError:
            task.status = "CANCELLED"
            task.error = "Task was cancelled and stopped."
            task.message = task.error
        except Exception as exc:
            task.status = "FAILED"
            task.error = str(exc)[:2000]
            task.message = "Processing failed"
        finally:
            task.updated_at = time.time()
            self._persist(task)
            await self._publish(task)
            async with self.lock:
                self.running.pop(task.task_id, None)
                self._schedule_locked()

    async def _set_progress(self, task: Task, progress: int, message: str) -> None:
        task.progress = max(0, min(99, int(progress)))
        task.message = message[:500]
        task.updated_at = time.time()
        self._persist(task)
        await self._publish(task)

    async def cancel(self, task_id: str, session_id: str) -> Task:
        async with self.lock:
            task = self._get_owned(task_id, session_id)
            if task.status not in ACTIVE_STATUSES:
                return task
            if task.status == "QUEUED":
                try:
                    self.queue.remove(task_id)
                except ValueError:
                    pass
                task.status = "CANCELLED"
                task.error = "Task was cancelled before it started."
                task.message = task.error
                task.updated_at = time.time()
                self._persist(task)
                await self._publish(task)
                self._schedule_locked()
                return task
            running = self.running.get(task_id)
            if running is not None:
                running.cancel()
            return task

    def get(self, task_id: str, session_id: str) -> Task:
        return self._get_owned(task_id, session_id)

    def list_for_session(self, session_id: str, limit: int = 50) -> list[Task]:
        tasks = [task for task in self.tasks.values() if task.session_id == session_id]
        return sorted(tasks, key=lambda task: task.created_at, reverse=True)[:limit]

    def get_result(self, task_id: str, session_id: str) -> dict[str, Any]:
        task = self._get_owned(task_id, session_id)
        if task.status != "SUCCEEDED":
            raise TaskLimitError("The task has no completed result yet.")
        return task.result or {}

    async def subscribe(self, task_id: str, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        task = self._get_owned(task_id, session_id)
        channel: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=20)
        self.subscribers.setdefault(task_id, set()).add(channel)
        channel.put_nowait(self._public(task))
        return channel

    def unsubscribe(self, task_id: str, channel: asyncio.Queue[dict[str, Any]]) -> None:
        channels = self.subscribers.get(task_id)
        if channels is not None:
            channels.discard(channel)
            if not channels:
                self.subscribers.pop(task_id, None)

    def set_max_concurrency(self, value: int) -> None:
        self.max_concurrency = max(1, min(64, int(value)))
        self._schedule_locked()

    def set_application_limits(self, limits: dict[str, int]) -> None:
        self.app_concurrency_limits = {
            str(app_id): max(1, min(64, int(limit)))
            for app_id, limit in limits.items()
        }
        self._schedule_locked()

    def _get_owned(self, task_id: str, session_id: str) -> Task:
        task = self.tasks.get(task_id)
        if task is None or task.session_id != session_id:
            raise TaskNotFoundError("Task not found.")
        return task

    async def _publish(self, task: Task) -> None:
        snapshot = self._public(task)
        for channel in tuple(self.subscribers.get(task.task_id, ())):
            if channel.full():
                try:
                    channel.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                channel.put_nowait(snapshot)
            except asyncio.QueueFull:
                pass

    def _persist(self, task: Task) -> None:
        path = Path(task.work_dir) / "task.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(task), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _restore(self) -> None:
        for path in self.root.glob("*/*/task.json"):
            try:
                task = Task(**json.loads(path.read_text(encoding="utf-8")))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if task.status in ACTIVE_STATUSES:
                task.status = "FAILED"
                task.error = "The server restarted before this task completed."
                task.message = task.error
                task.updated_at = time.time()
                self._persist(task)
            self.tasks[task.task_id] = task

    @staticmethod
    def _public(task: Task) -> dict[str, Any]:
        result = task.result
        if result is not None:
            result = {
                **result,
                "files": [
                    {key: value for key, value in item.items() if key != "path"}
                    for item in result.get("files", [])
                ],
            }
        return {
            "task_id": task.task_id,
            "source_name": task.payload.get("source_name", ""),
            "kind": task.kind,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "status": task.status,
            "progress": task.progress,
            "message": task.message,
            "error": task.error,
            "result": result if task.status == "SUCCEEDED" else None,
        }
