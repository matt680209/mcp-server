from __future__ import annotations

import tempfile
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from application.intelavatar import IntelAvatarAnalyzer, _etl_sort_key, _fill_time, _issue_time, _latest_wifi_row, _open_upload_tab, _prepare_chat, _report, _skip_case
from task_manager.manager import Task
from application.intelavatar import _finish_avatar, _stop_process, _open_avatar_home, _listening_ports, _select_local_file


class IntelAvatarTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_picker_brings_avatar_page_forward_before_opening(self) -> None:
        page = MagicMock()
        page.bring_to_front = AsyncMock()
        source = Path(r"D:\uploads\synthetic.zip")
        page.evaluate = AsyncMock(return_value=str(source))
        async def open_picker(**kwargs):
            page.bring_to_front.assert_awaited_once()
        page.locator.return_value.click = AsyncMock(side_effect=open_picker)
        helper = MagicMock(returncode=0)
        helper.poll.return_value = 0
        helper.communicate.return_value = (None, b"")
        with patch("application.intelavatar.subprocess.Popen", return_value=helper), \
             patch("application.intelavatar.asyncio.sleep", new_callable=AsyncMock), \
             patch("application.intelavatar.expect") as check:
            check.return_value.to_have_text = AsyncMock()
            await _select_local_file(page, source, 123)
        page.locator.return_value.click.assert_awaited_once_with(no_wait_after=True)

    async def test_home_ignores_auxiliary_listener_and_keeps_browser_open(self) -> None:
        page = MagicMock()
        wrong = MagicMock(status=404)
        wrong.text = AsyncMock(return_value="<h1>Not found</h1>")
        unrelated = MagicMock(status=200)
        unrelated.text = AsyncMock(return_value="<h1>Auxiliary service</h1>")
        home = MagicMock(status=200)
        home.text = AsyncMock(return_value='<div id="case-number-tab"></div><div id="local-upload-tab"></div>')
        page.request.get = AsyncMock(side_effect=[wrong, unrelated, home])
        for response in (wrong, unrelated, home):
            response.dispose = AsyncMock()
        page.goto = AsyncMock(return_value=home)
        page.close = AsyncMock()
        with patch("application.intelavatar._listening_ports", return_value=[51001, 51002, 51003]):
            await _open_avatar_home(page, MagicMock())
        page.goto.assert_awaited_once_with(
            "http://127.0.0.1:51003/", wait_until="domcontentloaded", timeout=60000,
        )
        page.close.assert_not_awaited()
        self.assertEqual(page.request.get.await_count, 3)
        for call in page.request.get.await_args_list:
            self.assertEqual(call.kwargs["timeout"], 10000)
        for response in (wrong, unrelated, home):
            response.dispose.assert_awaited_once()

    def test_listening_ports_include_all_owned_services(self) -> None:
        parent = MagicMock()
        child = MagicMock()
        parent.children.return_value = [child]
        parent.net_connections.return_value = [
            MagicMock(status="LISTEN", laddr=MagicMock(port=48596)),
            MagicMock(status="ESTABLISHED", laddr=MagicMock(port=50001)),
        ]
        child.net_connections.return_value = [
            MagicMock(status="LISTEN", laddr=MagicMock(port=51303)),
            MagicMock(status="LISTEN", laddr=MagicMock(port=48596)),
        ]
        self.assertEqual(_listening_ports(parent), [48596, 51303])

    @unittest.skipUnless(sys.platform == "win32", "Requires Windows PowerShell.")
    def test_explorer_filter_preserves_existing_and_unrelated_windows(self) -> None:
        helper = Path(__file__).resolve().parents[1] / "application" / "watch_explorers.ps1"
        script = r"""
$ErrorActionPreference = 'Stop'
. '__HELPER__'
$shell = [PSCustomObject]@{ Entries = @(
    [PSCustomObject]@{
        FullName = 'C:\Windows\explorer.exe'; HWND = 1
        Document = @{ Folder = @{ Self = @{ Path = 'shell:::{This-PC}' } } }
    },
    [PSCustomObject]@{
        FullName = 'C:\Program Files\Google\Chrome\Application\chrome.exe'; HWND = 2
        Document = @{ Folder = @{ Self = @{ Path = 'C:\upload\extracted' } } }
    }
) }
$shell | Add-Member -MemberType ScriptMethod -Name Windows -Value { $this.Entries }
$snapshot = @(Get-ExplorerWindows)
if ($snapshot.Count -ne 1 -or $snapshot[0].Handle -ne '1' -or $snapshot[0].Path -ne '') {
    throw 'Explorer baseline is wrong or includes the Avatar browser'
}
function Entry($id, $path) {
    $window = [PSCustomObject]@{ Closed = $false }
    $window | Add-Member -MemberType ScriptMethod -Name Quit -Value { $this.Closed = $true }
    [PSCustomObject]@{ Handle = $id; Path = $path; Window = $window }
}
$entries = @(
    (Entry 'old' 'C:\upload\existing'),
    (Entry 'new1' 'C:\upload\extracted'),
    (Entry 'new2' 'C:\Downloads\IntelAvatar_files\log_parser_123'),
    (Entry 'other' 'C:\personal'),
    (Entry 'prefix' 'C:\uploads\personal'),
    (Entry 'navigated' 'C:\upload\initial'),
    (Entry 'parser' 'C:\Temp\log_parser_261123_154500_000001'),
    (Entry 'unrelated-temp' 'C:\Temp\personal'),
    (Entry 'wrong-parser-root' 'C:\personal\log_parser_261123_154500_000001')
)
$baseline = @{ old = $true }
$pending = @{}
$roots = @('C:\upload', 'C:\Downloads\IntelAvatar_files')
$start = [DateTime]::UtcNow
Close-NewExplorerWindows $entries $baseline $pending $roots $start 'C:\Temp'
Close-NewExplorerWindows $entries $baseline $pending $roots ($start.AddMilliseconds(1999)) 'C:\Temp'
foreach ($entry in $entries) { if ($entry.Window.Closed) { throw 'Closed before two seconds' } }
$entries[5].Path = 'C:\personal'
Close-NewExplorerWindows $entries $baseline $pending $roots ($start.AddSeconds(2)) 'C:\Temp'
foreach ($index in @(1, 2, 6)) {
    if (-not $entries[$index].Window.Closed) { throw 'Expected popup was not closed' }
}
foreach ($index in @(0, 3, 4, 5, 7, 8)) {
    if ($entries[$index].Window.Closed) { throw 'Unrelated or existing window was closed' }
}
Write-Output 'PASS'
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script.replace("__HELPER__", str(helper).replace("'", "''"))],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "PASS")

    async def test_completed_report_survives_cleanup_failure_with_warning(self) -> None:
        completed = {"text": "finished", "report": {"raw": {"type": "report"}}}
        with patch("application.intelavatar._stop_process", new_callable=AsyncMock, side_effect=RuntimeError("still running")):
            with self.assertLogs("application.intelavatar", level="WARNING"):
                await _finish_avatar(MagicMock(), {}, completed)
        self.assertEqual(completed["text"], "finished")
        self.assertIn("cleanup failed", completed["warnings"][0])

    async def test_cleanup_failure_without_report_is_not_success(self) -> None:
        with patch("application.intelavatar._stop_process", new_callable=AsyncMock, side_effect=RuntimeError("still running")):
            with self.assertRaisesRegex(RuntimeError, "still running"):
                await _finish_avatar(MagicMock(), {}, None)

    async def test_cleanup_escalates_only_owned_surviving_processes(self) -> None:
        process = MagicMock(pid=123)
        parent = MagicMock()
        parent.is_running.return_value = True
        parent.children.return_value = []
        with patch("application.intelavatar.psutil.wait_procs", side_effect=[([], [parent]), ([parent], [])]):
            await _stop_process(process, {123: parent})
        parent.terminate.assert_called_once()
        parent.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)

    def test_issue_time_and_log_order(self) -> None:
        self.assertEqual(_issue_time("2026/04/21 10:00:07"), datetime(2026, 4, 21, 10, 0, 7))
        with self.assertRaises(ValueError):
            _issue_time("not a time")
        paths = [
            Path(r"C:\logs\21-04-2026_09-00-00\wifi.etl.9"),
            Path(r"C:\logs\21-04-2026_10-00-00\wifi.etl.2"),
            Path(r"C:\logs\21-04-2026_10-00-00\wifi.etl.10"),
        ]
        self.assertEqual(max(paths, key=_etl_sort_key), paths[2])

    async def test_skip_case_requires_two_clicks(self) -> None:
        first = MagicMock()
        second = MagicMock()
        first.wait_for = AsyncMock()
        first.click = AsyncMock()
        second.click = AsyncMock()
        overlay = MagicMock()
        overlay.wait_for = AsyncMock()
        page = MagicMock()
        page.get_by_role.side_effect = [first, second]
        page.locator.return_value = overlay
        await _skip_case(page)
        first.click.assert_awaited_once()
        second.click.assert_awaited_once()
        first.wait_for.assert_awaited_once()
        overlay.wait_for.assert_awaited_once_with(state="hidden", timeout=30000)

    async def test_open_upload_tab_waits_for_active_tab(self) -> None:
        page = MagicMock()
        case_tab, upload_tab, active_tab = MagicMock(), MagicMock(), MagicMock()
        case_tab.wait_for = AsyncMock()
        upload_tab.click = AsyncMock()
        active_tab.wait_for = AsyncMock()
        page.locator.side_effect = lambda selector, **kwargs: {
            "#case-number-tab.active": case_tab,
            ".tab": upload_tab,
            "#local-upload-tab.active": active_tab,
        }[selector]
        with patch("application.intelavatar.asyncio.sleep", new_callable=AsyncMock) as delay:
            await _open_upload_tab(page)
        delay.assert_awaited_with(2)
        case_tab.wait_for.assert_awaited_once()
        upload_tab.click.assert_awaited_once()
        active_tab.wait_for.assert_awaited_once()

    async def test_latest_wifi_row_selects_newest_etl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            older = Path(directory) / "wifi.etl.1"
            newer = Path(directory) / "wifi.etl.2"
            older.touch()
            newer.touch()
            items = [MagicMock(), MagicMock()]
            items[0].get_attribute = AsyncMock(return_value=str(older))
            items[1].get_attribute = AsyncMock(return_value=str(newer))
            rows = MagicMock()
            rows.count = AsyncMock(return_value=2)
            rows.nth.side_effect = lambda index: items[index]
            page = MagicMock()
            page.locator.return_value = rows
            self.assertIs(await _latest_wifi_row(page), items[1])

    async def test_issue_time_fills_all_fields(self) -> None:
        controls = {}
        page = MagicMock()
        def locate(name):
            control = MagicMock()
            control.is_enabled = AsyncMock(return_value=True)
            control.fill = AsyncMock()
            controls[name] = control
            return control
        page.locator.side_effect = locate
        await _fill_time(page, datetime(2026, 4, 21, 10, 0, 7))
        for field, value in {"month": "4", "day": "21", "year": "2026", "hour": "10", "min": "0", "sec": "7", "ms": "0"}.items():
            controls[f"#it-{field}"].fill.assert_awaited_once_with(value)

    async def test_partial_report_is_not_success(self) -> None:
        page = MagicMock()
        page.locator.return_value.is_visible = AsyncMock(return_value=False)
        page.evaluate = AsyncMock(return_value={"type": "partial_report", "data": {"text": "incomplete"}})
        with self.assertRaisesRegex(RuntimeError, "partial report"):
            await _report(page, [])

    async def test_report_capture_preserves_complete_avatar_event(self) -> None:
        raw = {"type": "report", "issue_time": "04/21/2026-10:00:07", "data": {"markdown_summary": "## Details", "extra": {"x": 1}}}
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=raw)
        card = MagicMock()
        card.is_visible = AsyncMock(return_value=True)
        card.inner_text = AsyncMock(return_value="Analysis Report\nDetails")
        page.locator.return_value.is_visible = AsyncMock(return_value=False)
        page.locator.return_value.last = card
        text, captured = await _report(page, [])
        self.assertEqual(text, "Analysis Report\nDetails")
        self.assertEqual(captured, raw)

    async def test_missing_time_popup_uses_log_last_time_before_report(self) -> None:
        raw = {"type": "text", "data": "Completed"}
        page = MagicMock()
        page.locator.return_value.is_visible = AsyncMock(return_value=True)
        page.locator.return_value.click = AsyncMock()
        page.locator.return_value.last.is_visible = AsyncMock(return_value=False)
        page.evaluate = AsyncMock(return_value=raw)
        with patch("application.intelavatar.asyncio.sleep", new_callable=AsyncMock) as delay:
            text, captured = await _report(page, [])
        delay.assert_awaited_with(2)
        page.locator.return_value.click.assert_awaited_once()
        self.assertEqual(text, "Completed")
        self.assertEqual(captured, raw)

    async def test_missing_time_confirmation_restores_question_before_resending(self) -> None:
        page = MagicMock()
        page.locator.return_value.is_visible = AsyncMock(return_value=True)
        page.locator.return_value.click = AsyncMock()
        page.locator.return_value.fill = AsyncMock()
        page.locator.return_value.last.is_visible = AsyncMock(return_value=False)
        page.evaluate = AsyncMock(return_value={"type": "text", "data": "Completed"})
        with patch("application.intelavatar.asyncio.sleep", new_callable=AsyncMock) as delay, \
             patch("application.intelavatar.expect") as check:
            check.return_value.to_have_value = AsyncMock()
            await _report(page, [], "Requested question")
        delay.assert_awaited_once_with(2)
        page.locator.return_value.fill.assert_awaited_once_with("Requested question")
        check.return_value.to_have_value.assert_awaited_once_with("Requested question", timeout=5000)
        page.locator.return_value.click.assert_awaited_once()

    async def test_empty_time_is_not_replaced_by_server_time(self) -> None:
        page = MagicMock()
        page.locator.return_value.wait_for = AsyncMock()
        page.locator.return_value.fill = AsyncMock()
        page.evaluate = AsyncMock()
        with patch("application.intelavatar._fill_time", new_callable=AsyncMock) as fill, \
             patch("application.intelavatar.asyncio.sleep", new_callable=AsyncMock), \
             patch("application.intelavatar.expect") as check:
            check.return_value.to_have_value = AsyncMock()
            await _prepare_chat(page, None, "Question")
        fill.assert_not_awaited()
        page.evaluate.assert_awaited_once_with("() => clearAllIssueTimes()")

    async def test_question_is_entered_before_waiting_for_send(self) -> None:
        question_entered = False
        user_input = MagicMock()
        user_input.wait_for = AsyncMock()
        async def fill_question(value):
            nonlocal question_entered
            question_entered = True
        user_input.fill = AsyncMock(side_effect=fill_question)
        send = MagicMock()
        async def wait_for_send(**kwargs):
            self.assertTrue(question_entered)
        send.wait_for = AsyncMock(side_effect=wait_for_send)
        page = MagicMock()
        page.locator.side_effect = lambda name: user_input if name == "#user-input" else send
        page.evaluate = AsyncMock(return_value="04/21/2026-10:00:07.000")
        with patch("application.intelavatar._fill_time", new_callable=AsyncMock), \
             patch("application.intelavatar.asyncio.sleep", new_callable=AsyncMock) as delay, \
             patch("application.intelavatar.expect") as check:
            check.return_value.to_have_value = AsyncMock()
            await _prepare_chat(page, datetime(2026, 4, 21, 10, 0, 7), "What happened?")
        self.assertEqual(user_input.fill.await_args_list[-1].args, ("What happened?",))
        self.assertGreaterEqual(user_input.fill.await_count, 2)
        delay.assert_awaited_with(2)
        check.return_value.to_have_value.assert_awaited_with("What happened?", timeout=5000)
        send.wait_for.assert_awaited_once()

    async def test_non_zip_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.txt"
            source.write_text("test", encoding="utf-8")
            task = Task("id", "session", "test", 0, directory, {"source_path": str(source)}, 10)
            with patch("application.intelavatar.sys.platform", "win32"):
                with self.assertRaisesRegex(ValueError, "ZIP"):
                    await IntelAvatarAnalyzer().execute(task, AsyncMock())


if __name__ == "__main__":
    unittest.main()
