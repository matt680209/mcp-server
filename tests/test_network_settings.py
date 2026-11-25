from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from application.config import SettingsStore
import web.server as server


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class NetworkSettingsTests(unittest.TestCase):
    def test_bad_settings_fail_and_failed_save_does_not_change_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Could not load settings"):
                SettingsStore(path)
            path.unlink()
            store = SettingsStore(path)
            changed = store.public()
            changed["network"]["port"] = 12345
            with patch.object(Path, "replace", side_effect=OSError("disk failure")):
                with self.assertRaisesRegex(OSError, "disk failure"):
                    store.update(changed)
            self.assertNotEqual(store.settings["network"]["port"], 12345)

    def test_mcp_host_allowlist_uses_configured_bind_address(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            store.settings["network"] = {
                "host": "10.225.40.127",
                "port": 80,
                "dns_name": "www.avatarmcp.intel.com",
            }
            with patch.object(server, "SETTINGS", store):
                security = server._mcp_transport_security()
            self.assertIn("10.225.40.127", security.allowed_hosts)
            self.assertIn("10.225.40.127:*", security.allowed_hosts)
            self.assertIn("www.avatarmcp.intel.com:*", security.allowed_hosts)

    def test_admin_save_persists_and_new_port_applies_after_restart(self) -> None:
        old_port = _free_port()
        new_port = _free_port()
        while new_port == old_port:
            new_port = _free_port()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("application", "mcp_tools", "task_manager", "web"):
                shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copyfile(ROOT / "main.py", root / "main.py")
            store = SettingsStore(root / "data" / "settings.json")
            settings = store.public()
            settings["network"] = {"host": "127.0.0.1", "port": old_port, "dns_name": ""}
            store.update(settings)
            env = os.environ.copy()
            env["MCP_ADMIN_PASSWORD"] = "test-password"
            env.pop("MCP_SSL_CERTFILE", None)
            env.pop("MCP_SSL_KEYFILE", None)
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            )

            def request(port: int, endpoint: str, data: dict | None = None, method: str = "GET"):
                payload = json.dumps(data).encode() if data is not None else None
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}{endpoint}",
                    data=payload,
                    method=method,
                    headers={"Content-Type": "application/json"},
                )
                with opener.open(req, timeout=2) as response:
                    return json.load(response)

            def start(port: int) -> subprocess.Popen:
                process = subprocess.Popen(
                    [sys.executable, "main.py"],
                    cwd=root,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                for _ in range(100):
                    if process.poll() is not None:
                        self.fail(f"Server exited during startup: {process.returncode}")
                    try:
                        request(port, "/api/session")
                        return process
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.1)
                process.terminate()
                process.wait(timeout=10)
                self.fail(f"Server did not start on port {port}")

            process = start(old_port)
            try:
                request(old_port, "/api/admin/login", {"password": "test-password"}, "POST")
                candidate = request(old_port, "/api/admin/settings")["settings"]
                candidate["network"]["port"] = new_port
                result = request(old_port, "/api/admin/settings", candidate, "PUT")
                self.assertTrue(result["restart_required"])
                self.assertEqual(SettingsStore(store.path).settings["network"]["port"], new_port)
                self.assertEqual(request(old_port, "/api/session")["session"], "ready")
            finally:
                process.terminate()
                process.wait(timeout=10)
            process = start(new_port)
            try:
                self.assertEqual(request(new_port, "/api/session")["session"], "ready")
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
