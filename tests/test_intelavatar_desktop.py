from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

import psutil
from playwright.async_api import async_playwright

from application.intelavatar import (
    _executable,
    _open_avatar_home,
    _open_upload_tab,
    _prepare_chat,
    _report,
    _running_avatars,
    _select_local_file,
    _stop_process,
    _track_processes,
    _watch_explorers,
)


@unittest.skipUnless(
    sys.platform == "win32" and os.getenv("MCP_TEST_INTELAVATAR_DESKTOP") == "1",
    "Requires an interactive Windows desktop and explicit opt-in.",
)
class IntelAvatarDesktopTests(unittest.IsolatedAsyncioTestCase):
    async def test_question_survives_delayed_prefill_and_confirmation(self) -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = await browser.new_page()
                await page.set_content("""<textarea id="user-input"></textarea>
                    <button id="send-btn" disabled>Send</button><script>
                    window.clearAllIssueTimes = () => {};
                    document.querySelector('textarea').addEventListener('input', () => {
                        setTimeout(() => {
                            document.querySelector('textarea').value = '';
                            document.querySelector('#send-btn').disabled = false;
                        }, 50);
                    }, {once: true});
                    document.querySelector('#send-btn').onclick = () => {
                        const button = document.createElement('button');
                        button.id = 'cm-confirm-btn';
                        button.onclick = () => {
                            window.__mcpAnalysisResult = {
                                type: 'text', data: document.querySelector('textarea').value
                            };
                            button.remove();
                        };
                        document.body.appendChild(button);
                        document.querySelector('textarea').value = '';
                    };
                    </script>""")
                question = "What happened?"
                await _prepare_chat(page, None, question)
                self.assertEqual(await page.locator("#user-input").input_value(), question)
                await page.locator("#send-btn").click()
                text, _ = await asyncio.wait_for(_report(page, [], question), timeout=10)
                self.assertEqual(text, question)
            finally:
                await browser.close()

    async def test_explorer_monitor_closes_new_folder_window(self) -> None:
        # Inherit normal folder ACLs so non-elevated Explorer can open this fixture.
        folder = Path(tempfile.gettempdir()) / f"mcp_explorer_test_{uuid.uuid4().hex}"
        folder.mkdir()
        script = r"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject Shell.Application
$target = $env:MCP_TEST_EXPLORER
$seen = $false
$closed = $false
try {
    Start-Process explorer.exe -ArgumentList ('/n,"' + $target + '"')
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt 12) {
        $found = $false
        foreach ($window in @($shell.Windows())) {
            try {
                if ($window.Document.Folder.Self.Path -eq $target) { $found = $true }
            } catch [Runtime.InteropServices.COMException] { }
        }
        if ($found) { $seen = $true }
        if ($seen -and -not $found) { $closed = $true; break }
        Start-Sleep -Milliseconds 100
    }
    if (-not $seen) { throw 'Synthetic Explorer window was not observed' }
    if (-not $closed) { throw 'Synthetic Explorer window was not automatically closed' }
} finally {
    foreach ($window in @($shell.Windows())) {
        try {
            if ($window.Document.Folder.Self.Path -eq $target) { $window.Quit() }
        } catch [Runtime.InteropServices.COMException] { }
    }
}
"""
        try:
            env = os.environ.copy()
            env["MCP_TEST_EXPLORER"] = str(folder)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(channel="chrome", headless=False)
                try:
                    page = await browser.new_page()
                    await page.set_content("<h1>Avatar browser remains open</h1>")
                    async with _watch_explorers(folder / "synthetic.zip") as warnings:
                        result = await asyncio.to_thread(
                            subprocess.run, ["powershell.exe", "-NoProfile", "-Command", script],
                            env=env, capture_output=True, text=True, timeout=18,
                        )
                        self.assertTrue(browser.is_connected())
                        self.assertFalse(page.is_closed())
                        self.assertEqual(await page.locator("h1").inner_text(), "Avatar browser remains open")
                    self.assertEqual(warnings, [])
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(browser.is_connected())
                    self.assertFalse(page.is_closed())
                finally:
                    await browser.close()
        finally:
            folder.rmdir()

    async def test_selects_synthetic_upload_in_visible_window(self) -> None:
        if _running_avatars():
            self.fail("IntelAvatar is already running; close it before the desktop test.")
        with tempfile.TemporaryDirectory(prefix="mcp_avatar_test_") as folder:
            source = Path(folder) / "synthetic.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("synthetic.txt", "synthetic test data")
            executable = _executable()
            process = subprocess.Popen(
                [str(executable)], cwd=str(executable.parent),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            parent = psutil.Process(process.pid)
            targets = {parent.pid: parent}
            tracker = asyncio.create_task(_track_processes(parent, targets))
            try:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(channel="chrome", headless=False)
                    try:
                        page = await browser.new_page()
                        async with _watch_explorers(source) as warnings:
                            await _open_avatar_home(page, parent)
                            await _open_upload_tab(page)
                            await _select_local_file(page, source, process.pid)
                            self.assertTrue(browser.is_connected())
                            self.assertFalse(page.is_closed())
                        self.assertEqual(warnings, [])
                        self.assertTrue(await page.locator("#local-upload-tab.active").is_visible())
                        self.assertEqual(await page.locator("#upload-file-name").inner_text(), source.name)
                        self.assertEqual(
                            os.path.normcase(await page.evaluate("() => selectedFiles[0].sourcePath")),
                            os.path.normcase(str(source)),
                        )
                    finally:
                        await browser.close()
            finally:
                tracker.cancel()
                try:
                    await tracker
                except asyncio.CancelledError:
                    pass
                finally:
                    await _stop_process(process, targets)


if __name__ == "__main__":
    unittest.main()
