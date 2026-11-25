from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "network": {"host": "0.0.0.0", "port": 8000, "dns_name": ""},
    "limits": {
        "max_users": 5,
        "per_session": 3,
        "max_concurrency": 15,
        "max_queue": 100,
    },
    "applications": [
        {
            "id": "demo",
            "name": "Demo Analyzer",
            "adapter": "demo",
            "match_terms": [],
            "enabled": True,
            "max_concurrency": 15,
            "timeout_seconds": 900,
        }
    ],
    "fields": [
        {
            "name": "time",
            "label": "Date and time",
            "type": "datetime",
            "required": False,
            "max_length": 40,
            "placeholder": "2026/11/09 13:25:10",
        },
        {
            "name": "question",
            "label": "Your question",
            "type": "textarea",
            "required": True,
            "max_length": 10000,
            "placeholder": "Describe what you want analyzed",
        },
    ],
    "source_access": {"allowed_hosts": [], "shared_roots": []},
}

FIELD_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")
FIELD_TYPES = {"text", "textarea", "datetime"}


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return json.loads(json.dumps(DEFAULT_SETTINGS))
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not load settings from {self.path}: {exc}") from exc
        if not isinstance(saved, dict):
            raise ValueError(f"Settings in {self.path} must be an object.")
        settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        for key in settings:
            if key in saved:
                settings[key] = saved[key]
        return settings

    def public(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.settings, ensure_ascii=False))

    def update(self, candidate: dict[str, Any]) -> dict[str, Any]:
        validated = validate_settings(candidate)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        self.settings = validated
        return self.public()


def validate_settings(candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError("Settings must be an object.")
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    for key in settings:
        if key in candidate:
            settings[key] = candidate[key]

    network = settings["network"]
    if not isinstance(network, dict):
        raise ValueError("Network settings are invalid.")
    host = str(network.get("host", "0.0.0.0")).strip()
    dns_name = str(network.get("dns_name", "")).strip()
    port = int(network.get("port", 8000))
    if not host or len(host) > 253 or not 1 <= port <= 65535:
        raise ValueError("Host or port is invalid.")
    network.update(host=host, port=port, dns_name=dns_name)

    limits = settings["limits"]
    if not isinstance(limits, dict):
        raise ValueError("Task limits are invalid.")
    limits = {
        "max_users": _bounded_int(limits.get("max_users"), 1, 100, "max_users"),
        "per_session": _bounded_int(limits.get("per_session"), 1, 20, "per_session"),
        "max_concurrency": _bounded_int(limits.get("max_concurrency"), 1, 64, "max_concurrency"),
        "max_queue": _bounded_int(limits.get("max_queue"), 1, 10000, "max_queue"),
    }
    settings["limits"] = limits

    fields = settings["fields"]
    if not isinstance(fields, list) or len(fields) > 50:
        raise ValueError("At most 50 input fields are supported.")
    names: set[str] = set()
    normalized_fields = []
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError("Each input field must be an object.")
        name = str(field.get("name", "")).strip()
        label = str(field.get("label", "")).strip()
        field_type = str(field.get("type", "text"))
        max_length = _bounded_int(field.get("max_length", 1000), 1, 100000, "max_length")
        if not FIELD_NAME.fullmatch(name) or name in names or name in {"file", "source"}:
            raise ValueError(f"Invalid or duplicate field name: {name}")
        if not label or len(label) > 100 or field_type not in FIELD_TYPES:
            raise ValueError(f"Invalid field definition: {name}")
        names.add(name)
        normalized_fields.append({
            "name": name,
            "label": label,
            "type": field_type,
            "required": bool(field.get("required", False)),
            "max_length": max_length,
            "placeholder": str(field.get("placeholder", ""))[:200],
        })
    settings["fields"] = normalized_fields

    applications = settings["applications"]
    if not isinstance(applications, list) or not applications or len(applications) > 20:
        raise ValueError("Configure between 1 and 20 applications.")
    app_ids: set[str] = set()
    normalized_apps = []
    for application in applications:
        if not isinstance(application, dict):
            raise ValueError("Each application must be an object.")
        app_id = str(application.get("id", "")).strip()
        name = str(application.get("name", "")).strip()
        adapter = str(application.get("adapter", "demo"))
        terms = application.get("match_terms", [])
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", app_id) or app_id in app_ids:
            raise ValueError(f"Invalid or duplicate application ID: {app_id}")
        if not name or len(name) > 100 or adapter not in {"demo", "intelavatar"}:
            raise ValueError("Only registered adapters can be selected.")
        if not isinstance(terms, list) or any(not isinstance(term, str) or len(term) > 100 for term in terms):
            raise ValueError(f"Invalid match terms for application: {app_id}")
        app_ids.add(app_id)
        normalized_apps.append({
            "id": app_id,
            "name": name,
            "adapter": adapter,
            "match_terms": [term.strip() for term in terms if term.strip()],
            "enabled": bool(application.get("enabled", True)),
            "max_concurrency": _bounded_int(application.get("max_concurrency", 15), 1, 64, "application max_concurrency"),
            "timeout_seconds": _bounded_int(application.get("timeout_seconds", 900), 1, 86400, "application timeout_seconds"),
        })
    if not any(application["enabled"] for application in normalized_apps):
        raise ValueError("At least one application must be enabled.")
    settings["applications"] = normalized_apps

    source_access = settings["source_access"]
    if not isinstance(source_access, dict):
        raise ValueError("Source access settings are invalid.")
    allowed_hosts = source_access.get("allowed_hosts", [])
    shared_roots = source_access.get("shared_roots", [])
    if not isinstance(allowed_hosts, list) or any(not isinstance(item, str) for item in allowed_hosts):
        raise ValueError("Allowed URL hosts must be a list of host names.")
    if not isinstance(shared_roots, list) or any(not isinstance(item, str) for item in shared_roots):
        raise ValueError("Shared roots must be a list of paths.")
    source_access["allowed_hosts"] = [item.strip().lower() for item in allowed_hosts if item.strip()]
    source_access["shared_roots"] = [item.strip() for item in shared_roots if item.strip()]
    return settings


def _bounded_int(value: Any, minimum: int, maximum: int, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return number
