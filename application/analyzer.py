from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from task_manager.manager import ProgressCallback, Task
from application.intelavatar import IntelAvatarAnalyzer


class DemoAnalyzer:
    async def execute(self, task: Task, progress: ProgressCallback) -> dict[str, Any]:
        fields = task.payload.get("fields", {})
        source_path = Path(task.payload["source_path"])
        app_name = task.payload.get("application_name", "Demo Analyzer")
        await progress(15, "Reading the selected file")
        file_size = await asyncio.to_thread(lambda: source_path.stat().st_size)
        await progress(55, "Preparing the demo analysis")
        question = str(fields.get("question", "")).strip()
        time_value = str(fields.get("time", "")).strip()
        text = (
            "This is a demo adapter response. Your application is not connected yet.\n\n"
            f"Application: {app_name}\n"
            f"File: {source_path.name}\n"
            f"File size: {file_size} bytes\n"
            f"Time: {time_value or '(not provided)'}\n"
            f"Question: {question or '(not provided)'}\n\n"
            "Implement your application adapter in application/analyzer.py."
        )
        result_dir = Path(task.payload["result_dir"])
        result_dir.mkdir(parents=True, exist_ok=True)
        result_path = result_dir / "analysis.txt"
        await asyncio.to_thread(result_path.write_text, text, encoding="utf-8")
        await progress(90, "Finalizing the result")
        return {
            "text": text,
            "application_id": task.payload.get("application_id", "demo"),
            "files": [{"file_id": "0", "name": result_path.name, "size": result_path.stat().st_size, "path": str(result_path)}],
        }


class ApplicationRouter:
    def __init__(self) -> None:
        self.adapters = {"demo": DemoAnalyzer(), "intelavatar": IntelAvatarAnalyzer()}

    def select_application(
        self,
        applications: list[dict[str, Any]],
        source_name: str,
        question: str,
        preferred_id: str | None = None,
    ) -> dict[str, Any]:
        enabled = [item for item in applications if item.get("enabled", True)]
        if preferred_id:
            selected = next((item for item in enabled if item.get("id") == preferred_id), None)
            if selected is None:
                raise ValueError("The selected application is unavailable.")
            return selected
        searchable = f"{source_name} {question}".casefold()
        return next(
            (
                item for item in enabled
                if any(term.casefold() in searchable for term in item.get("match_terms", []))
            ),
            next(iter(enabled), None),
        ) or _raise_no_application()

    async def execute(self, task: Task, progress: ProgressCallback) -> dict[str, Any]:
        applications = task.payload.get("applications", [])
        selected = self.select_application(
            applications,
            str(task.payload.get("source_name", "")),
            str(task.payload.get("fields", {}).get("question", "")),
            str(task.payload["application_id"]) if task.payload.get("application_id") else None,
        )
        adapter = self.adapters.get(selected.get("adapter", ""))
        if adapter is None:
            raise ValueError("The selected application adapter is not registered.")
        payload = dict(task.payload)
        payload["application_id"] = selected["id"]
        payload["application_name"] = selected["name"]
        routed_task = Task(**{**task.__dict__, "payload": payload})
        result = await adapter.execute(routed_task, progress)
        result["application_id"] = selected["id"]
        result["application_name"] = selected["name"]
        return result


def _raise_no_application() -> dict[str, Any]:
    raise ValueError("No enabled application is configured.")
