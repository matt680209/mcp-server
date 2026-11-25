from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp import Client

from application.analyzer import ApplicationRouter
from application.config import SettingsStore
from application.report import build_report
from mcp_tools.mcp_server import create_mcp_server
from task_manager.manager import TaskManager


class McpToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_result_reports_recorded_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            async def fail(task, progress):
                raise RuntimeError("Synthetic analysis failure")
            manager = TaskManager(fail, root / "users")
            task = await manager.submit("mcp", "test", {})
            await manager.running[task.task_id]
            async with Client(create_mcp_server(manager, SettingsStore(root / "settings.json"), root)) as client:
                response = await client.call_tool("get_result", {"task_id": task.task_id})
                self.assertTrue(response.is_error)
                self.assertIn("FAILED: Synthetic analysis failure", str(response.content))

    async def test_registered_tools_share_the_task_manager(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            router = ApplicationRouter()
            manager = TaskManager(router.execute, root / "users", max_concurrency=2)
            server = create_mcp_server(manager, settings, root)

            async with Client(server) as client:
                tool_response = await client.list_tools()
                names = {tool.name for tool in tool_response.tools}
                self.assertEqual(names, {"analyze_log", "analyze_file", "run_test", "get_task_status", "get_result"})

                response = await client.call_tool(
                    "analyze_log",
                    {"log_text": "example log", "question": "check this", "time_value": ""},
                )
                self.assertFalse(response.is_error)
                task_id = _find_task_id(response.structured_content)
                self.assertTrue(task_id)
                await asyncio.wait_for(manager.running[task_id], timeout=2)

                status = await client.call_tool("get_task_status", {"task_id": task_id})
                self.assertFalse(status.is_error)
                result = await client.call_tool("get_result", {"task_id": task_id})
                self.assertFalse(result.is_error)
                self.assertIn("not connected yet", str(result.structured_content))

    async def test_file_tool_returns_structured_report_and_applies_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "allowed"
            allowed.mkdir()
            source = allowed / "sample.zip"
            source.write_bytes(b"synthetic zip")
            settings = SettingsStore(root / "settings.json")
            settings.settings["source_access"]["shared_roots"] = [str(allowed)]
            router = ApplicationRouter()
            raw = {"type": "report", "data": {"markdown_summary": "## Detail\n\n| A | B |\n|---|---|\n| 1 | 2 |", "unknown": [1, 2]}}

            async def handler(task, progress):
                self.assertEqual(Path(task.payload["source_path"]).read_bytes(), b"synthetic zip")
                return {"text": "old text", "report": build_report(raw), "files": []}

            manager = TaskManager(handler, root / "users")
            server = create_mcp_server(manager, settings, root, router)
            async with Client(server) as client:
                response = await client.call_tool("analyze_file", {
                    "source_kind": "shared", "source_value": str(source),
                    "application_id": "demo", "question": "test", "time_value": "2026/04/21 10:00:07",
                })
                self.assertFalse(response.is_error)
                task_id = _find_task_id(response.structured_content)
                running = manager.running.get(task_id)
                if running is not None:
                    await asyncio.wait_for(running, timeout=2)
                result = await client.call_tool("get_result", {"task_id": task_id})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["report"]["raw"], raw)
                self.assertEqual(result.structured_content["report"]["schema_version"], 1)
                self.assertIn("| A | B |", result.structured_content["report"]["markdown"])
                outside = root / "outside.zip"
                outside.write_bytes(b"synthetic zip")
                rejected = await client.call_tool("analyze_file", {
                    "source_kind": "shared", "source_value": str(outside),
                    "application_id": "demo", "question": "test", "time_value": "2026/04/21 10:00:07",
                })
                self.assertTrue(rejected.is_error)
                self.assertIn("not allowed", str(rejected.content))


def _find_task_id(value: Any) -> str:
    if isinstance(value, dict):
        if "task_id" in value:
            return str(value["task_id"])
        for nested in value.values():
            result = _find_task_id(nested)
            if result:
                return result
    return ""


if __name__ == "__main__":
    unittest.main()
