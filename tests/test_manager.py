from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from task_manager.manager import Task, TaskManager, TaskNotFoundError


class TaskManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_avatar_queue_is_shared_across_application_ids(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        async def handler(task: Task, progress) -> dict[str, Any]:
            if task.payload.get("wait"):
                started.set()
                await release.wait()
            return {"text": "done"}

        with tempfile.TemporaryDirectory() as directory:
            manager = TaskManager(handler, Path(directory), max_concurrency=2)
            first = await manager.submit("session-a", "test", {"application_id": "a", "application_adapter": "intelavatar", "wait": True})
            first_execution = manager.running[first.task_id]
            await asyncio.wait_for(started.wait(), 1)
            queued = await manager.submit("session-a", "test", {"application_id": "b", "application_adapter": "intelavatar"}, timeout_seconds=1)
            other = await manager.submit("session-a", "test", {"application_id": "c", "application_adapter": "demo"})
            other_execution = manager.running[other.task_id]
            await asyncio.wait_for(other_execution, 1)
            self.assertEqual(queued.status, "QUEUED")
            self.assertEqual(other.status, "SUCCEEDED")
            release.set()
            await asyncio.wait_for(first_execution, 1)
            await _wait_until_finished(manager, queued.task_id)
            self.assertEqual(queued.status, "SUCCEEDED")

    async def test_application_limits_do_not_block_other_apps(self) -> None:
        first_started = asyncio.Event()
        other_app_started = asyncio.Event()
        release_first = asyncio.Event()

        async def handler(task: Task, progress) -> dict[str, Any]:
            app_id = task.payload["application_id"]
            if app_id == "app-a" and task.payload.get("wait"):
                first_started.set()
                await release_first.wait()
            if app_id == "app-b":
                other_app_started.set()
            return {"text": app_id, "files": []}

        with tempfile.TemporaryDirectory() as directory:
            manager = TaskManager(
                handler,
                Path(directory),
                max_concurrency=2,
                app_concurrency_limits={"app-a": 1, "app-b": 1},
            )
            try:
                first = await manager.submit("session-a", "analyze_log", {"application_id": "app-a", "wait": True})
                first_execution = manager.running[first.task_id]
                await asyncio.wait_for(first_started.wait(), timeout=1)
                queued = await manager.submit("session-a", "analyze_log", {"application_id": "app-a"})
                other_app = await manager.submit("session-a", "analyze_log", {"application_id": "app-b"})
                other_execution = manager.running[other_app.task_id]

                await asyncio.wait_for(other_app_started.wait(), timeout=1)
                self.assertEqual(queued.status, "QUEUED")
                await asyncio.wait_for(other_execution, timeout=1)
                self.assertEqual(manager.get(other_app.task_id, "session-a").status, "SUCCEEDED")
                release_first.set()
                await asyncio.wait_for(first_execution, timeout=1)
                await _wait_until_finished(manager, queued.task_id)
                self.assertEqual(manager.get(queued.task_id, "session-a").status, "SUCCEEDED")
            finally:
                release_first.set()
                if manager.running:
                    await asyncio.gather(*tuple(manager.running.values()), return_exceptions=True)

    async def test_task_timeout_marks_task_stopped(self) -> None:
        async def handler(task: Task, progress) -> dict[str, Any]:
            await asyncio.Event().wait()
            return {}

        with tempfile.TemporaryDirectory() as directory:
            manager = TaskManager(handler, Path(directory), max_concurrency=1)
            task = await manager.submit("session-a", "analyze_log", {}, timeout_seconds=0.01)
            await asyncio.wait_for(manager.running[task.task_id], timeout=1)
            self.assertEqual(manager.get(task.task_id, "session-a").status, "TIMED_OUT")

    async def test_queue_cancellation_session_ownership_and_recovery(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(task: Task, progress) -> dict[str, Any]:
            if task.payload.get("wait"):
                started.set()
                await release.wait()
            return {"text": task.payload["text"], "files": []}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = TaskManager(handler, root, max_concurrency=1, per_session_limit=3, max_sessions=2)
            first = await manager.submit("session-a", "analyze_log", {"wait": True, "text": "done"})
            await asyncio.wait_for(started.wait(), timeout=1)
            second = await manager.submit("session-a", "analyze_log", {"wait": False, "text": "queued"})

            self.assertEqual(first.status, "RUNNING")
            self.assertEqual(second.status, "QUEUED")
            with self.assertRaises(TaskNotFoundError):
                manager.get(first.task_id, "session-b")

            cancelled = await manager.cancel(second.task_id, "session-a")
            self.assertEqual(cancelled.status, "CANCELLED")
            release.set()
            await asyncio.wait_for(manager.running[first.task_id], timeout=1)
            self.assertEqual(manager.get(first.task_id, "session-a").status, "SUCCEEDED")

            restored = TaskManager(handler, root, max_concurrency=1)
            recovered = restored.get(first.task_id, "session-a")
            self.assertEqual(recovered.status, "SUCCEEDED")
            self.assertEqual(recovered.result["text"], "done")


if __name__ == "__main__":
    unittest.main()


async def _wait_until_finished(manager: TaskManager, task_id: str) -> None:
    for _ in range(1000):
        if manager.get(task_id, "session-a").status not in {"QUEUED", "RUNNING"}:
            return
        await asyncio.sleep(0)
    raise AssertionError("Task did not finish in time.")
