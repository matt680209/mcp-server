from __future__ import annotations

import asyncio
import hmac
import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from application.analyzer import ApplicationRouter
from application.config import SettingsStore
from application.sources import SourceError, acquire_source
from task_manager.manager import TaskLimitError, TaskManager, TaskNotFoundError


def create_mcp_server(
    manager: TaskManager,
    settings: SettingsStore,
    data_root: Path,
    router: ApplicationRouter | None = None,
) -> MCPServer:
    server = MCPServer("MCP Project Analyzer")
    application_router = router or ApplicationRouter()
    mcp_uploads = data_root / "uploads" / "mcp"
    mcp_uploads.mkdir(parents=True, exist_ok=True)

    async def submit_text_task(
        kind: str,
        log_text: str,
        question: str,
        time_value: str,
        application_id: str | None = None,
    ) -> dict[str, Any]:
        token = secrets.token_hex(12)
        source_dir = mcp_uploads / token
        source_dir.mkdir(parents=True, exist_ok=False)
        source_path = source_dir / "mcp-input.txt"
        await asyncio.to_thread(source_path.write_text, log_text, encoding="utf-8")
        configured_apps = settings.settings["applications"]
        if application_id:
            configured_apps = [item for item in configured_apps if item["id"] == application_id]
        if not configured_apps:
            raise ValueError("The requested application is not configured.")
        selected_app = application_router.select_application(
            configured_apps,
            "mcp-input.txt",
            question,
            application_id,
        )
        payload = {
            "source_path": str(source_path),
            "source_name": "mcp-input.txt",
            "fields": {"time": time_value, "question": question},
            "applications": configured_apps,
            "application_id": selected_app["id"],
            "application_adapter": selected_app["adapter"],
            "result_dir": str(data_root / "results" / "{task_id}"),
        }
        try:
            task = await manager.submit(
                "mcp",
                kind,
                payload,
                selected_app["timeout_seconds"],
            )
        except Exception:
            for path in source_dir.glob("*"):
                path.unlink(missing_ok=True)
            source_dir.rmdir()
            raise
        return {"task_id": task.task_id, "status": task.status, "message": task.message}

    @server.tool()
    async def analyze_log(log_text: str, question: str = "", time_value: str = "") -> dict[str, Any]:
        """Submit log text and a question for asynchronous analysis. Returns a task ID."""
        return await submit_text_task("analyze_log", log_text, question, time_value)

    @server.tool()
    async def analyze_file(
        source_kind: str,
        source_value: str,
        application_id: str,
        question: str,
        time_value: str,
    ) -> dict[str, Any]:
        """Submit an allowed URL or shared-folder file to an enabled application."""
        if source_kind not in {"url", "shared"}:
            raise ToolError("File source must be 'url' or 'shared'.")
        if not application_id:
            raise ToolError("Select an enabled application.")
        applications = settings.settings["applications"]
        try:
            selected = application_router.select_application(applications, "", question, application_id)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        source_dir = mcp_uploads / secrets.token_hex(12)
        source_dir.mkdir(parents=True, exist_ok=False)
        try:
            source_path, source_name = await acquire_source(
                source_kind, source_value, source_dir,
                settings.settings["source_access"]["allowed_hosts"],
                settings.settings["source_access"]["shared_roots"],
            )
            task = await manager.submit(
                "mcp",
                "analyze_log",
                {
                    "source_path": str(source_path),
                    "source_name": source_name,
                    "fields": {"time": time_value, "question": question},
                    "applications": [selected],
                    "application_id": selected["id"],
                    "application_adapter": selected["adapter"],
                    "result_dir": str(data_root / "results" / "{task_id}"),
                },
                selected["timeout_seconds"],
            )
        except (SourceError, TaskLimitError) as exc:
            shutil.rmtree(source_dir)
            raise ToolError(str(exc)) from exc
        except OSError as exc:
            shutil.rmtree(source_dir)
            raise ToolError(f"Unable to read or store the selected file: {exc}") from exc
        except Exception:
            if source_dir.exists():
                shutil.rmtree(source_dir)
            raise
        return {"task_id": task.task_id, "status": task.status, "message": task.message}

    @server.tool()
    async def run_test(
        admin_password: str,
        application_id: str,
        log_text: str,
        question: str = "Administrator test",
        time_value: str = "",
    ) -> dict[str, Any]:
        """Run an administrator-only test through the same analysis task pipeline."""
        expected = os.getenv("MCP_ADMIN_PASSWORD", "")
        if not expected or not hmac.compare_digest(admin_password, expected):
            raise ValueError("Administrator authentication failed.")
        return await submit_text_task("run_test", log_text, question, time_value, application_id)

    @server.tool()
    async def get_task_status(task_id: str) -> dict[str, Any]:
        """Get the status and progress of an MCP-submitted task."""
        try:
            task = manager.get(task_id, "mcp")
        except TaskNotFoundError as exc:
            raise ToolError("Task not found.") from exc
        return manager._public(task)

    @server.tool()
    async def get_result(task_id: str) -> dict[str, Any]:
        """Get the completed result of an MCP-submitted task."""
        try:
            task = manager.get(task_id, "mcp")
            manager.get_result(task_id, "mcp")
        except TaskNotFoundError as exc:
            raise ToolError(str(exc)) from exc
        except TaskLimitError as exc:
            raise ToolError(f"{task.status}: {task.error or task.message}") from exc
        return manager._public(task)["result"] or {}

    return server
