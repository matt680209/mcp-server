from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt


_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, str):
        return [re.sub(r"^[-*•\s]+", "", item).strip() for item in re.split(r"[\n;]+", value) if item.strip()]
    return [] if value is None else [value]


def build_report(result: dict[str, Any]) -> dict[str, Any]:
    kind = result.get("type")
    data = result.get("data")
    if kind == "text" and isinstance(data, str) and data.strip():
        markdown = data
    elif kind == "report" and isinstance(data, dict) and data:
        sections = ["# Analysis Report"]
        event_time = result.get("issue_time")
        if event_time:
            sections.append("**Event time:** " + _text(event_time))
        confidence = data.get("confidence_score")
        if confidence is not None:
            sections.append("**Confidence:** " + _text(confidence) + "%")
        if data.get("root_cause_summary"):
            sections.append("## Root Cause\n\n" + _text(data["root_cause_summary"]))
        actions = _items(data.get("recommended_actions"))
        if actions:
            sections.append("## Recommendations\n\n" + "\n".join("- " + _text(item).replace("\n", "\n  ") for item in actions))
        skills = _items(data.get("involved_skills"))
        if skills:
            sections.append("## Skills Used\n\n" + "\n".join("- " + _text(item).replace("\n", "\n  ") for item in skills))
        findings = data.get("skill_findings")
        if isinstance(findings, dict) and findings:
            sections.append("## Per-Skill Findings\n\n" + "\n\n".join(
                "### " + str(name).replace("\n", " ") + "\n\n" + _text(value)
                for name, value in findings.items()
            ))
        summary = data.get("markdown_summary")
        if isinstance(summary, str) and summary.strip():
            sections.append(summary)
        markdown = "\n\n".join(sections) + "\n"
    else:
        raise ValueError("IntelAvatar returned an invalid report or text result.")
    return {
        "schema_version": 1,
        "source": "intelavatar",
        "type": kind,
        "raw": result,
        "markdown": markdown,
        "html": _MARKDOWN.render(markdown),
    }


def save_report(result_dir: Path, text: str, report: dict[str, Any]) -> list[dict[str, Any]]:
    result_dir.mkdir(parents=True, exist_ok=True)
    paths = (result_dir / "analysis.txt", result_dir / "analysis.md", result_dir / "analysis.json")
    paths[0].write_text(text, encoding="utf-8")
    paths[1].write_text(report["markdown"], encoding="utf-8")
    paths[2].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return [
        {"file_id": str(index), "name": path.name, "size": path.stat().st_size, "path": str(path)}
        for index, path in enumerate(paths)
    ]
