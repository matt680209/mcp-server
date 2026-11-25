from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import web.server as server
from application.config import SettingsStore
from task_manager.manager import TaskManager


@asynccontextmanager
async def _test_mcp_session():
    yield


class ApiFlowTests(unittest.TestCase):
    def test_blank_avatar_time_reaches_handler_unchanged(self) -> None:
        captured = []
        async def handler(task, progress):
            captured.append(task.payload["fields"]["time"])
            return {"text": "synthetic result", "files": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            settings.settings["applications"][0]["adapter"] = "intelavatar"
            manager = TaskManager(handler, root / "users")
            with patch.object(server, "SETTINGS", settings), patch.object(server, "MANAGER", manager), \
                 patch.object(server, "UPLOADS_ROOT", root / "uploads"), \
                 patch.object(server.MCP_SERVER.session_manager, "run", _test_mcp_session):
                with TestClient(server.app) as client:
                    task = self._submit(client, "/api/tasks", application_id="demo")
                    self.assertEqual(self._wait_for_task(client, task["task_id"])["status"], "SUCCEEDED")
            self.assertEqual(captured, [""])

    def test_application_selection_and_public_options(self) -> None:
        previous_settings = server.SETTINGS
        previous_manager = server.MANAGER
        previous_uploads = server.UPLOADS_ROOT
        previous_results = server.RESULTS_ROOT
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            demo = settings.settings["applications"][0]
            settings.settings["applications"] = [
                {**demo, "id": "first", "name": "First", "match_terms": ["example"]},
                {**demo, "id": "second", "name": "Second"},
                {**demo, "id": "disabled", "name": "Disabled", "enabled": False},
            ]
            server.SETTINGS = settings
            server.UPLOADS_ROOT = root / "uploads"
            server.RESULTS_ROOT = root / "results"
            server.MANAGER = TaskManager(server.ROUTER.execute, root / "users")
            try:
                with patch.object(server.MCP_SERVER.session_manager, "run", _test_mcp_session), TestClient(server.app) as client:
                    options = client.get("/api/form-config").json()["applications"]
                    self.assertEqual(options, [
                        {"id": "first", "name": "First"},
                        {"id": "second", "name": "Second"},
                    ])
                    selected = self._submit(client, "/api/tasks", application_id="second")
                    result = self._wait_for_task(client, selected["task_id"])
                    self.assertEqual(result["status"], "SUCCEEDED")
                    self.assertEqual(result["result"]["application_id"], "second")
                    automatic = self._submit(client, "/api/tasks")
                    self.assertEqual(
                        self._wait_for_task(client, automatic["task_id"])["result"]["application_id"],
                        "first",
                    )
                    for application_id in ("disabled", "missing"):
                        response = client.post(
                            "/api/tasks",
                            data={"application_id": application_id, "fields_json": '{"question":"test"}'},
                            files={"upload": ("example.log", b"sample")},
                        )
                        self.assertEqual(response.status_code, 422)
                        self.assertEqual(response.json()["detail"], "The selected application is unavailable.")
            finally:
                server.SETTINGS = previous_settings
                server.MANAGER = previous_manager
                server.UPLOADS_ROOT = previous_uploads
                server.RESULTS_ROOT = previous_results

    def test_browser_analysis_and_admin_test_use_same_pipeline(self) -> None:
        previous_settings = server.SETTINGS
        previous_manager = server.MANAGER
        previous_uploads = server.UPLOADS_ROOT
        previous_results = server.RESULTS_ROOT
        previous_password = os.environ.get("MCP_ADMIN_PASSWORD")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server.SETTINGS = SettingsStore(root / "settings.json")
            server.UPLOADS_ROOT = root / "uploads"
            server.RESULTS_ROOT = root / "results"
            server.MANAGER = TaskManager(
                server.ROUTER.execute,
                root / "users",
                max_concurrency=2,
                per_session_limit=3,
                max_sessions=5,
            )
            os.environ["MCP_ADMIN_PASSWORD"] = "test-password"
            try:
                with TestClient(server.app) as client:
                    config = client.get("/api/form-config")
                    self.assertEqual(config.status_code, 200)
                    self.assertEqual({field["name"] for field in config.json()["fields"]}, {"time", "question"})

                    task = self._submit(client, "/api/tasks")
                    completed = self._wait_for_task(client, task["task_id"])
                    self.assertEqual(completed["status"], "SUCCEEDED")
                    self.assertIn("not connected yet", completed["result"]["text"])
                    result = client.get(f"/api/tasks/{task['task_id']}/result")
                    self.assertEqual(result.status_code, 200)
                    self.assertNotIn("path", result.json()["files"][0])
                    download = client.get(f"/api/tasks/{task['task_id']}/files/0")
                    self.assertEqual(download.status_code, 200)
                    with client.websocket_connect(f"/ws/tasks/{task['task_id']}") as websocket:
                        self.assertEqual(websocket.receive_json()["status"], "SUCCEEDED")

                    isolated = TestClient(server.app)
                    self.assertEqual(isolated.get(f"/api/tasks/{task['task_id']}").status_code, 404)
                    isolated.close()

                    login = client.post("/api/admin/login", json={"password": "test-password"})
                    self.assertEqual(login.status_code, 200)
                    self.assertEqual(client.get("/api/admin/settings").status_code, 200)
                    test_task = self._submit(client, "/api/admin/test", application_id="demo")
                    test_completed = self._wait_for_task(client, test_task["task_id"])
                    self.assertEqual(test_completed["status"], "SUCCEEDED")
            finally:
                server.SETTINGS = previous_settings
                server.MANAGER = previous_manager
                server.UPLOADS_ROOT = previous_uploads
                server.RESULTS_ROOT = previous_results
                if previous_password is None:
                    os.environ.pop("MCP_ADMIN_PASSWORD", None)
                else:
                    os.environ["MCP_ADMIN_PASSWORD"] = previous_password

    @staticmethod
    def _submit(client: TestClient, endpoint: str, application_id: str | None = None) -> dict:
        data = {
            "source_kind": "upload",
            "fields_json": json.dumps({"time": "", "question": "Analyze this test log."}),
        }
        if application_id:
            data["application_id"] = application_id
        response = client.post(
            endpoint,
            data=data,
            files={"upload": ("example.log", b"sample log content", "application/octet-stream")},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        return response.json()

    @staticmethod
    def _wait_for_task(client: TestClient, task_id: str) -> dict:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = client.get(f"/api/tasks/{task_id}")
            if response.status_code != 200:
                raise AssertionError(response.text)
            task = response.json()
            if task["status"] not in {"QUEUED", "RUNNING"}:
                return task
            time.sleep(0.01)
        raise AssertionError("Task did not finish in time.")


if __name__ == "__main__":
    unittest.main()
