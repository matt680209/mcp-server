from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import psutil
from playwright.async_api import Error as PlaywrightError, Page, async_playwright, expect

from task_manager.manager import ProgressCallback, Task
from application.report import build_report, save_report


TOOL_DIR = Path(__file__).resolve().parents[2] / "MCP_tool" / "IntelAvatar"
_AVATAR_LOCK = asyncio.Lock()
_LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def _watch_explorers(source: Path) -> AsyncIterator[list[str]]:
    env = os.environ.copy()
    env["MCP_AVATAR_INPUT_DIR"] = str(source.parent)
    helper = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(Path(__file__).with_name("watch_explorers.ps1"))],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    warnings: list[str] = []
    try:
        assert helper.stdout is not None
        ready = await asyncio.wait_for(asyncio.to_thread(helper.stdout.readline), timeout=15)
        if ready.strip() != "READY":
            raise RuntimeError("IntelAvatar Explorer monitor could not start.")
        yield warnings
    finally:
        premature_exit = helper.poll() is not None
        try:
            output, error = await asyncio.to_thread(helper.communicate, "stop\n", timeout=10)
        except subprocess.TimeoutExpired:
            helper.kill()
            output, error = await asyncio.to_thread(helper.communicate)
            error = f"Explorer monitor did not stop in time. {error}"
        if premature_exit or helper.returncode or output.strip() or error.strip():
            warning = f"IntelAvatar Explorer monitor warning: {error.strip() or output.strip() or 'monitor exited unexpectedly'}"
            _LOGGER.warning(warning)
            warnings.append(warning)


def _executable() -> Path:
    candidates = sorted(TOOL_DIR.glob("IntelAvatar*.exe"))
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one IntelAvatar executable in {TOOL_DIR}, found {len(candidates)}.")
    return candidates[0]


def _issue_time(value: str) -> datetime:
    for pattern in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value.strip(), pattern)
        except ValueError:
            continue
    raise ValueError("IntelAvatar requires a date and time in YYYY/MM/DD HH:MM:SS format.")


def _running_avatars() -> list[psutil.Process]:
    found = []
    for process in psutil.process_iter(["name"]):
        name = process.info["name"] or ""
        if name.casefold().startswith("intelavatar") and name.casefold().endswith(".exe"):
            found.append(process)
    return found


def _listening_ports(process: psutil.Process) -> list[int]:
    ports: set[int] = set()
    try:
        candidates = [process, *process.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []
    for candidate in candidates:
        try:
            for connection in candidate.net_connections(kind="tcp"):
                if connection.status == psutil.CONN_LISTEN and connection.laddr:
                    ports.add(connection.laddr.port)
        except psutil.NoSuchProcess:
            continue
    return sorted(ports)


async def _open_avatar_home(page: Page, process: psutil.Process) -> None:
    deadline = asyncio.get_running_loop().time() + 120
    observed: dict[int, str] = {}
    while asyncio.get_running_loop().time() < deadline:
        if not process.is_running():
            raise RuntimeError("IntelAvatar exited before its home page became available.")
        for port in _listening_ports(process):
            url = f"http://127.0.0.1:{port}/"
            try:
                response = await page.request.get(url, timeout=10000)
            except PlaywrightError:
                observed[port] = "not ready for HTTP"
                continue
            try:
                html = await response.text() if response.status == 200 else ""
                valid_home = response.status == 200 and all(
                    re.search(rf"""id\s*=\s*["']{tab}["']""", html)
                    for tab in ("case-number-tab", "local-upload-tab")
                )
                observed[port] = f"HTTP {response.status}" + ("" if valid_home else " without Avatar home")
            finally:
                await response.dispose()
            if valid_home:
                loaded = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if loaded is None or loaded.status != 200:
                    status = loaded.status if loaded is not None else "no response"
                    raise RuntimeError(f"IntelAvatar home navigation failed: HTTP {status}.")
                return
        await asyncio.sleep(1)
    detail = ", ".join(f"{port}: {status}" for port, status in observed.items()) or "no listening ports"
    raise RuntimeError(f"IntelAvatar did not provide its home page within 120 seconds ({detail}).")


async def _track_processes(process: psutil.Process, targets: dict[int, psutil.Process]) -> None:
    while True:
        try:
            if not process.is_running():
                return
            for child in process.children(recursive=True):
                targets[child.pid] = child
        except psutil.NoSuchProcess:
            return
        await asyncio.sleep(0.1)


async def _stop_process(process: subprocess.Popen[Any], targets: dict[int, psutil.Process]) -> None:
    try:
        parent = targets[process.pid]
        if parent.is_running():
            for child in parent.children(recursive=True):
                targets[child.pid] = child
    except psutil.NoSuchProcess:
        pass
    for target in reversed(list(targets.values())):
        try:
            target.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = await asyncio.to_thread(psutil.wait_procs, list(targets.values()), timeout=5)
    if alive:
        for target in alive:
            try:
                target.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = await asyncio.to_thread(psutil.wait_procs, alive, timeout=5)
    if alive:
        raise RuntimeError("IntelAvatar did not stop; close its processes before running another task.")
    await asyncio.to_thread(process.wait, timeout=5)

async def _finish_avatar(
    process: subprocess.Popen[Any],
    targets: dict[int, psutil.Process],
    completed: dict[str, Any] | None,
) -> None:
    try:
        await _stop_process(process, targets)
    except (RuntimeError, psutil.Error, subprocess.TimeoutExpired) as exc:
        if completed is None:
            raise
        warning = f"Analysis completed, but IntelAvatar process cleanup failed: {exc}"
        _LOGGER.warning(warning)
        completed.setdefault("warnings", []).append(warning)


async def _open_upload_tab(page: Page) -> None:
    await page.locator("#case-number-tab.active").wait_for(state="visible", timeout=15000)
    await asyncio.sleep(2)
    await page.locator(".tab", has_text="Local File Upload").click()
    await page.locator("#local-upload-tab.active").wait_for(state="visible", timeout=15000)


async def _select_local_file(page: Page, source: Path, avatar_pid: int) -> None:
    env = os.environ.copy()
    env["MCP_AVATAR_INPUT_FILE"] = str(source)
    env["MCP_AVATAR_PID"] = str(avatar_pid)
    helper = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(Path(__file__).with_name("select_file.ps1"))],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        await asyncio.sleep(2)
        await page.bring_to_front()
        await page.locator(".upload-bar").click(no_wait_after=True)
        while helper.poll() is None:
            await asyncio.sleep(0.2)
        _, error = helper.communicate()
        if helper.returncode:
            raise RuntimeError(f"IntelAvatar file picker failed: {error.decode(errors='replace').strip()}")
    finally:
        if helper.poll() is None:
            helper.terminate()
            await asyncio.to_thread(helper.wait)
    await expect(page.locator("#upload-file-name")).to_have_text(source.name, timeout=15000)
    selected_path = await page.evaluate("() => selectedFiles[0]?.sourcePath || ''")
    if not selected_path or os.path.normcase(os.path.normpath(selected_path)) != os.path.normcase(os.path.normpath(str(source))):
        raise RuntimeError("IntelAvatar selected a different file than the requested upload.")


async def _skip_case(page: Page) -> None:
    button = page.get_by_role("button", name="This log has no case", exact=True)
    await button.wait_for(state="visible", timeout=30000)
    await button.click()
    await page.get_by_role("button", name="Click again to confirm", exact=True).click()
    await page.locator(".ips-prompt-overlay").wait_for(state="hidden", timeout=30000)


def _etl_sort_key(path: Path) -> tuple[datetime, int, str]:
    match = re.search(r"(\d{2})-(\d{2})-(\d{4})_(\d{2})-(\d{2})-(\d{2})", str(path))
    timestamp = datetime.min
    if match:
        day, month, year, hour, minute, second = map(int, match.groups())
        try:
            timestamp = datetime(year, month, day, hour, minute, second)
        except ValueError:
            pass
    suffix = re.search(r"\.etl\.(\d+)", path.name, re.IGNORECASE)
    return timestamp, int(suffix.group(1)) if suffix else -1, str(path).casefold()


async def _latest_wifi_row(page: Page):
    rows = page.locator("#wifi-panel .wifi-file-item:not(.excluded-row)")
    count = await rows.count()
    if not count:
        raise RuntimeError("IntelAvatar did not find an eligible Wi-Fi ETL file.")
    candidates = []
    for index in range(count):
        row = rows.nth(index)
        encoded = await row.get_attribute("data-etl-encoded")
        if not encoded:
            raise RuntimeError("A Wi-Fi ETL entry has no file path.")
        path = Path(unquote(encoded))
        if not path.is_file():
            raise RuntimeError(f"Wi-Fi ETL file is unavailable: {path}")
        candidates.append((_etl_sort_key(path), index))
    return rows.nth(max(candidates)[1])


async def _fill_time(page: Page, value: datetime) -> None:
    for field, part in (
        ("month", value.month), ("day", value.day), ("year", value.year),
        ("hour", value.hour), ("min", value.minute), ("sec", value.second), ("ms", value.microsecond // 1000),
    ):
        control = page.locator(f"#it-{field}")
        if not await control.is_enabled():
            raise RuntimeError(f"IntelAvatar cannot accept the supplied time: {field} is disabled.")
        await control.fill(str(part))


async def _prepare_chat(page: Page, value: datetime | None, question: str) -> None:
    await page.locator("#user-input").wait_for(timeout=180000)
    if value is not None:
        await _fill_time(page, value)
        issue_time = await page.evaluate("() => typeof getIssueTimeString === 'function' ? getIssueTimeString() : ''")
        if issue_time != value.strftime("%m/%d/%Y-%H:%M:%S.000"):
            raise RuntimeError("IntelAvatar did not accept the requested issue date and time.")
    else:
        await page.evaluate("() => clearAllIssueTimes()")
    await asyncio.sleep(2)
    await page.locator("#user-input").fill(question)
    await page.locator("#send-btn:not([disabled])").wait_for(timeout=180000)
    await asyncio.sleep(2)
    # Avatar's delayed context prefill can replace the draft while Send becomes ready.
    await page.locator("#user-input").fill(question)
    await expect(page.locator("#user-input")).to_have_value(question, timeout=5000)


async def _report(page: Page, dialogs: list[str], question: str | None = None) -> tuple[str, dict[str, Any]]:
    confirmed_time = False
    while True:
        if dialogs:
            raise RuntimeError(f"IntelAvatar reported an error: {dialogs[-1]}")
        confirm_time = page.locator("#cm-confirm-btn")
        if not confirmed_time and await confirm_time.is_visible():
            await asyncio.sleep(2)
            if question is not None:
                await page.locator("#user-input").fill(question)
                await expect(page.locator("#user-input")).to_have_value(question, timeout=5000)
            await confirm_time.click()
            confirmed_time = True
        result = await page.evaluate("() => window.__mcpAnalysisResult || null")
        if result:
            if result.get("type") == "partial_report":
                raise RuntimeError("IntelAvatar returned a partial report; analysis did not complete.")
            if result.get("type") not in ("report", "text") or not result.get("data"):
                raise RuntimeError(f"IntelAvatar returned an unsuccessful analysis result: {result.get('type')}")
            report = page.locator("#chat-window .report-card").last
            if await report.is_visible():
                text = (await report.inner_text()).strip()
                if text:
                    return text, result
            if result["type"] == "text":
                return str(result["data"]), result
        error = page.locator("#chat-window .msg-row.assistant .msg-bubble").filter(has_text=re.compile(r"network error|error", re.I)).last
        if await error.is_visible():
            raise RuntimeError(f"IntelAvatar analysis failed: {(await error.inner_text()).strip()}")
        await asyncio.sleep(2)


class IntelAvatarAnalyzer:
    async def execute(self, task: Task, progress: ProgressCallback) -> dict[str, Any]:
        if sys.platform != "win32":
            raise RuntimeError("IntelAvatar requires Windows and an interactive desktop.")
        source = Path(task.payload["source_path"]).resolve(strict=True)
        if not source.is_file():
            raise ValueError("IntelAvatar input must be a file.")
        if source.suffix.casefold() != ".zip":
            raise ValueError("IntelAvatar currently requires a ZIP file containing Wi-Fi ETL logs.")
        if any(re.fullmatch(r"0[01]\d{6}", part) for part in source.parts[:-1]):
            raise ValueError("IntelAvatar input path contains an IPS case folder; use a path without a case number to record no case.")
        fields = task.payload.get("fields", {})
        question = str(fields.get("question", "")).strip()
        if not question:
            raise ValueError("IntelAvatar requires a question.")
        time_value = str(fields.get("time", "")).strip()
        when = _issue_time(time_value) if time_value else None
        await progress(1, "Waiting for exclusive access to IntelAvatar")
        async with _AVATAR_LOCK, _watch_explorers(source) as warnings:
            if _running_avatars():
                raise RuntimeError("IntelAvatar is already running. Close it before starting this task.")
            executable = _executable()
            process = subprocess.Popen([str(executable)], cwd=str(executable.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            parent = psutil.Process(process.pid)
            targets = {parent.pid: parent}
            tracker = asyncio.create_task(_track_processes(parent, targets))
            completed: dict[str, Any] | None = None
            try:
                await progress(5, "Starting IntelAvatar")
                avatar = psutil.Process(process.pid)
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(channel="chrome", headless=False)
                    try:
                        page = await browser.new_page()
                        dialogs: list[str] = []
                        async def handle_dialog(dialog):
                            dialogs.append(dialog.message)
                            await dialog.dismiss()
                        page.on("dialog", handle_dialog)
                        await _open_avatar_home(page, avatar)
                        await _open_upload_tab(page)
                        await progress(15, "Selecting the input file")
                        await _select_local_file(page, source, process.pid)
                        await page.locator("#upload-bar-submit-btn:not([disabled])").click(no_wait_after=True)
                        await _skip_case(page)
                        await page.locator("#analysis-card").wait_for(timeout=180000)
                        await progress(35, "Selecting the newest Wi-Fi ETL")
                        await page.locator("#wifi-tab").click()
                        await (await _latest_wifi_row(page)).click()
                        await page.locator("#wifi-action-chatbot:not([disabled])").click(no_wait_after=True)
                        await page.wait_for_url(re.compile("log_chatbot"), timeout=180000)
                        await _prepare_chat(page, when, question)
                        await page.evaluate("""() => {
                            window.__mcpAnalysisResult = null;
                            document.addEventListener('chatbot:result-rendered', event => {
                                window.__mcpAnalysisResult = event.detail;
                            }, {once: true});
                        }""")
                        await progress(55, "Running IntelAvatar analysis")
                        await expect(page.locator("#user-input")).to_have_value(question, timeout=5000)
                        await page.locator("#send-btn:not([disabled])").click()
                        if dialogs:
                            raise RuntimeError(f"IntelAvatar reported an error: {dialogs[-1]}")
                        text, raw = await _report(page, dialogs, question)
                        report = build_report(raw)
                        result_dir = Path(task.payload["result_dir"])
                        files = await asyncio.to_thread(save_report, result_dir, text, report)
                        await progress(95, "Saving IntelAvatar analysis")
                        completed = {
                            "text": text,
                            "report": report,
                            "files": files,
                            "warnings": warnings,
                        }
                        return completed
                    finally:
                        await browser.close()
            finally:
                tracker.cancel()
                try:
                    await tracker
                except asyncio.CancelledError:
                    pass
                finally:
                    await _finish_avatar(process, targets, completed)
