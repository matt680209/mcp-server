from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from application.report import build_report, save_report


class ReportTests(unittest.TestCase):
    def test_report_preserves_source_fields_and_renders_markdown_safely(self) -> None:
        raw = {
            "type": "report",
            "issue_time": "04/21/2026-10:00:07",
            "data": {
                "root_cause_summary": "Connection failed",
                "confidence_score": 0,
                "recommended_actions": ["Restart service", "Check logs"],
                "skill_findings": {"Wi-Fi": "Full finding " + "x" * 600},
                "markdown_summary": "## Details\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n```text\nline 1\nline 2\n```\n\n<script>alert(1)</script>",
                "unrecognized_field": {"nested": [1, 2]},
            },
        }
        report = build_report(raw)
        self.assertEqual(report["raw"], raw)
        self.assertEqual(report["schema_version"], 1)
        self.assertIn("**Confidence:** 0%", report["markdown"])
        self.assertIn(raw["data"]["skill_findings"]["Wi-Fi"], report["markdown"])
        self.assertIn("| A | B |", report["markdown"])
        self.assertIn("<table>", report["html"])
        self.assertIn("<pre><code", report["html"])
        self.assertNotIn("<script>", report["html"])
        self.assertIn("&lt;script&gt;", report["html"])

    def test_text_result_preserves_markdown(self) -> None:
        raw = {"type": "text", "data": "**Answer**\n\n- item"}
        report = build_report(raw)
        self.assertEqual(report["markdown"], raw["data"])
        self.assertIn("<strong>Answer</strong>", report["html"])

    def test_saved_files_preserve_raw_result_and_markdown(self) -> None:
        raw = {"type": "report", "data": {"markdown_summary": "## Detail\n\n| A | B |\n|---|---|\n|1|2|", "extra": ["完整"]}}
        report = build_report(raw)
        with tempfile.TemporaryDirectory() as directory:
            files = save_report(Path(directory), "plain text", report)
            self.assertEqual([item["name"] for item in files], ["analysis.txt", "analysis.md", "analysis.json"])
            self.assertEqual(Path(files[0]["path"]).read_text(encoding="utf-8"), "plain text")
            self.assertEqual(Path(files[1]["path"]).read_text(encoding="utf-8"), report["markdown"])
            self.assertEqual(json.loads(Path(files[2]["path"]).read_text(encoding="utf-8"))["raw"], raw)

    def test_invalid_result_is_not_success(self) -> None:
        for raw in ({"type": "report", "data": []}, {"type": "text", "data": {}}, {"type": "partial_report", "data": {}}):
            with self.assertRaises(ValueError):
                build_report(raw)
