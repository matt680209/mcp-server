from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from mcp.server.transport_security import TransportSecuritySettings

from application.analyzer import ApplicationRouter
from application.config import SettingsStore
from application.sources import SourceError, acquire_source, store_upload
from mcp_tools.mcp_server import create_mcp_server
from task_manager.manager import TaskLimitError, TaskManager, TaskNotFoundError


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data"
UPLOADS_ROOT = DATA_ROOT / "uploads"
RESULTS_ROOT = DATA_ROOT / "results"
USERS_ROOT = DATA_ROOT / "users"
WEB_ROOT = ROOT / "web"
SETTINGS = SettingsStore(DATA_ROOT / "settings.json")
ROUTER = ApplicationRouter()
MANAGER = TaskManager(
    ROUTER.execute,
    USERS_ROOT,
    max_concurrency=SETTINGS.settings["limits"]["max_concurrency"],
    per_session_limit=SETTINGS.settings["limits"]["per_session"],
    max_sessions=SETTINGS.settings["limits"]["max_users"],
    max_queue=SETTINGS.settings["limits"]["max_queue"],
    app_concurrency_limits={item["id"]: item["max_concurrency"] for item in SETTINGS.settings["applications"]},
)
MCP_SERVER = create_mcp_server(MANAGER, SETTINGS, DATA_ROOT)
ADMIN_SESSIONS: dict[str, float] = {}


def _mcp_transport_security() -> TransportSecuritySettings:
    allowed_hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
    bind_host = SETTINGS.settings["network"].get("host", "").strip()
    if bind_host and bind_host not in {"0.0.0.0", "::", "[::]"}:
        if ":" in bind_host and not bind_host.startswith("["):
            bind_host = f"[{bind_host}]"
        allowed_hosts.extend([bind_host, f"{bind_host}:*"])
    dns_name = SETTINGS.settings["network"].get("dns_name", "").strip()
    if dns_name:
        allowed_hosts.extend([dns_name, f"{dns_name}:*"])
    return TransportSecuritySettings(allowed_hosts=allowed_hosts)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with MCP_SERVER.session_manager.run():
        cleanup_task = asyncio.create_task(_cleanup_loop())
        try:
            yield
        finally:
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)


app = FastAPI(title="MCP Project Analyzer", lifespan=lifespan)


@app.middleware("http")
async def browser_session(request: Request, call_next):
    session_id = request.cookies.get("mcp_session")
    if not session_id or not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", session_id):
        session_id = secrets.token_urlsafe(32)
    request.state.session_id = session_id
    response = await call_next(request)
    response.set_cookie(
        "mcp_session",
        session_id,
        httponly=True,
        secure=os.getenv("MCP_COOKIE_SECURE", "0") == "1",
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
    return response


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page() -> FileResponse:
    return FileResponse(WEB_ROOT / "admin.html")


@app.get("/style.css")
async def stylesheet() -> FileResponse:
    return FileResponse(WEB_ROOT / "style.css", media_type="text/css")


@app.get("/app.js")
async def frontend_script() -> FileResponse:
    return FileResponse(WEB_ROOT / "app.js", media_type="text/javascript")


@app.get("/admin.js")
async def admin_script() -> FileResponse:
    return FileResponse(WEB_ROOT / "admin.js", media_type="text/javascript")


@app.get("/Avatar_MCP_icon.png")
async def avatar_icon() -> FileResponse:
    return FileResponse(ROOT / "Avatar_MCP_icon.png", media_type="image/png")


@app.get("/Avatar_MCP_logo.png")
async def avatar_logo() -> FileResponse:
    return FileResponse(ROOT / "Avatar_MCP_logo.png", media_type="image/png")


@app.get("/api/session")
async def session_info(request: Request) -> dict[str, Any]:
    return {"session": "ready", "tasks": len(MANAGER.list_for_session(request.state.session_id))}


@app.get("/api/form-config")
async def public_form_config() -> dict[str, Any]:
    return {
        "fields": SETTINGS.settings["fields"],
        "applications": [
            {"id": item["id"], "name": item["name"]}
            for item in SETTINGS.settings["applications"]
            if item["enabled"]
        ],
    }


@app.get("/api/tasks")
async def list_tasks(request: Request) -> list[dict[str, Any]]:
    return [MANAGER._public(task) for task in MANAGER.list_for_session(request.state.session_id)]


async def _submit_request(
    request: Request,
    source_kind: str,
    source_value: str,
    fields_json: str,
    upload: UploadFile | None,
    kind: str = "analyze_log",
    application_id: str | None = None,
):
    if application_id is not None:
        if not application_id or not any(
            item["id"] == application_id and item["enabled"]
            for item in SETTINGS.settings["applications"]
        ):
            raise HTTPException(422, "The selected application is unavailable.")
    try:
        submitted_fields = json.loads(fields_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "Input fields must be valid JSON.") from exc
    fields = _validate_fields(submitted_fields)
    input_token = secrets.token_hex(12)
    source_dir = UPLOADS_ROOT / input_token
    try:
        if source_kind == "upload":
            if upload is None or not upload.filename:
                raise HTTPException(422, "Choose one file to upload.")
            source_dir.mkdir(parents=True, exist_ok=False)
            filename = upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
            safe_filename = "".join(char for char in filename if char.isprintable() and char not in '<>:"/\\|?*')[:180] or "input-file"
            source_path = source_dir / safe_filename
            source_name = await store_upload(upload, source_path)
        else:
            source_path, source_name = await acquire_source(
                source_kind,
                source_value,
                source_dir,
                SETTINGS.settings["source_access"]["allowed_hosts"],
                SETTINGS.settings["source_access"]["shared_roots"],
            )
        apps = SETTINGS.settings["applications"]
        if application_id is not None:
            apps = [item for item in apps if item["id"] == application_id and item["enabled"]]
        selected_app = ROUTER.select_application(
            apps,
            source_name,
            fields.get("question", ""),
            application_id,
        )
        if selected_app["adapter"] == "intelavatar" and not str(submitted_fields.get("time", "")).strip():
            fields["time"] = ""
        payload = {
            "source_path": str(source_path),
            "source_name": source_name,
            "fields": fields,
            "applications": apps,
            "application_id": selected_app["id"],
            "application_adapter": selected_app["adapter"],
            "result_dir": str(RESULTS_ROOT / "{task_id}"),
        }
        task = await MANAGER.submit(
            request.state.session_id,
            kind,
            payload,
            selected_app["timeout_seconds"],
        )
        return MANAGER._public(task)
    except SourceError as exc:
        shutil.rmtree(source_dir, ignore_errors=True)
        raise HTTPException(422, str(exc)) from exc
    except TaskLimitError as exc:
        shutil.rmtree(source_dir, ignore_errors=True)
        raise HTTPException(429, str(exc)) from exc
    except HTTPException:
        shutil.rmtree(source_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(source_dir, ignore_errors=True)
        raise


@app.post("/api/tasks")
async def submit_task(
    request: Request,
    source_kind: str = Form("upload"),
    source_value: str = Form(""),
    fields_json: str = Form("{}"),
    upload: UploadFile | None = File(None),
    application_id: str | None = Form(None),
):
    return await _submit_request(request, source_kind, source_value, fields_json, upload, application_id=application_id)


@app.get("/api/tasks/{task_id}")
async def task_status(task_id: str, request: Request) -> dict[str, Any]:
    try:
        return MANAGER._public(MANAGER.get(task_id, request.state.session_id))
    except TaskNotFoundError as exc:
        raise HTTPException(404, "Task not found.") from exc


@app.delete("/api/tasks/{task_id}")
async def cancel_task(task_id: str, request: Request) -> dict[str, Any]:
    try:
        task = await MANAGER.cancel(task_id, request.state.session_id)
        return MANAGER._public(task)
    except TaskNotFoundError as exc:
        raise HTTPException(404, "Task not found.") from exc


@app.get("/api/tasks/{task_id}/result")
async def task_result(task_id: str, request: Request) -> dict[str, Any]:
    try:
        result = MANAGER.get_result(task_id, request.state.session_id)
    except TaskNotFoundError as exc:
        raise HTTPException(404, "Task not found.") from exc
    except TaskLimitError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _public_result(result)


@app.get("/api/tasks/{task_id}/files/{file_id}")
async def download_result(task_id: str, file_id: str, request: Request):
    try:
        task = MANAGER.get(task_id, request.state.session_id)
    except TaskNotFoundError as exc:
        raise HTTPException(404, "Task not found.") from exc
    files = (task.result or {}).get("files", [])
    selected = next((item for item in files if item.get("file_id") == file_id), None)
    if selected is None:
        raise HTTPException(404, "Result file not found.")
    path = Path(selected["path"]).resolve()
    if not _is_relative_to(path, RESULTS_ROOT.resolve()) or not path.is_file():
        raise HTTPException(404, "Result file is no longer available.")
    return FileResponse(path, filename=selected["name"])


@app.websocket("/ws/tasks/{task_id}")
async def task_progress(websocket: WebSocket, task_id: str):
    session_id = websocket.cookies.get("mcp_session", "")
    try:
        channel = await MANAGER.subscribe(task_id, session_id)
    except TaskNotFoundError:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    try:
        while True:
            update = await channel.get()
            await websocket.send_json(update)
            if update["status"] not in {"QUEUED", "RUNNING"}:
                break
    except WebSocketDisconnect:
        pass
    finally:
        MANAGER.unsubscribe(task_id, channel)


@app.post("/api/admin/login")
async def admin_login(request: Request):
    expected = os.getenv("MCP_ADMIN_PASSWORD", "")
    if not expected:
        raise HTTPException(503, "Set MCP_ADMIN_PASSWORD before enabling administrator access.")
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid login request.") from exc
    password = str(body.get("password", ""))
    if not hmac.compare_digest(password, expected):
        raise HTTPException(401, "Incorrect administrator password.")
    token = secrets.token_urlsafe(32)
    ADMIN_SESSIONS[token] = time.time() + 60 * 60 * 12
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "mcp_admin",
        token,
        httponly=True,
        secure=os.getenv("MCP_COOKIE_SECURE", "0") == "1",
        samesite="strict",
        max_age=60 * 60 * 12,
        path="/",
    )
    return response


@app.get("/api/admin/status")
async def admin_status(request: Request) -> dict[str, bool]:
    token = request.cookies.get("mcp_admin", "")
    authenticated = ADMIN_SESSIONS.get(token, 0) > time.time()
    return {"password_configured": bool(os.getenv("MCP_ADMIN_PASSWORD")), "authenticated": authenticated}


@app.post("/api/admin/logout")
async def admin_logout(request: Request):
    token = request.cookies.get("mcp_admin", "")
    ADMIN_SESSIONS.pop(token, None)
    response = JSONResponse({"ok": True})
    response.delete_cookie("mcp_admin", path="/")
    return response


@app.get("/api/admin/settings")
async def admin_settings(request: Request):
    _require_admin(request)
    return {"settings": SETTINGS.public(), "password_configured": bool(os.getenv("MCP_ADMIN_PASSWORD"))}


@app.put("/api/admin/settings")
async def save_admin_settings(request: Request):
    _require_admin(request)
    try:
        candidate = await request.json()
        updated = SETTINGS.update(candidate)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    limits = updated["limits"]
    MANAGER.per_session_limit = limits["per_session"]
    MANAGER.max_sessions = limits["max_users"]
    MANAGER.max_queue = limits["max_queue"]
    MANAGER.set_max_concurrency(limits["max_concurrency"])
    MANAGER.set_application_limits({item["id"]: item["max_concurrency"] for item in updated["applications"]})
    return {"settings": updated, "restart_required": True}


@app.post("/api/admin/test")
async def admin_test(
    request: Request,
    application_id: str = Form(...),
    source_kind: str = Form("upload"),
    source_value: str = Form(""),
    fields_json: str = Form("{}"),
    upload: UploadFile | None = File(None),
):
    _require_admin(request)
    return await _submit_request(request, source_kind, source_value, fields_json, upload, "run_test", application_id)


def _require_admin(request: Request) -> None:
    token = request.cookies.get("mcp_admin", "")
    expiry = ADMIN_SESSIONS.get(token, 0)
    if expiry <= time.time():
        ADMIN_SESSIONS.pop(token, None)
        raise HTTPException(401, "Administrator login required.")


def _validate_fields(values: Any) -> dict[str, str]:
    if not isinstance(values, dict):
        raise HTTPException(422, "Input fields must be an object.")
    definitions = SETTINGS.settings["fields"]
    allowed = {item["name"] for item in definitions}
    if set(values) - allowed:
        raise HTTPException(422, "Unexpected input field.")
    validated: dict[str, str] = {}
    for definition in definitions:
        name = definition["name"]
        value = values.get(name, "")
        if not isinstance(value, str):
            raise HTTPException(422, f"{definition['label']} must be text.")
        value = value.strip() if definition["type"] != "textarea" else value
        if len(value) > definition["max_length"]:
            raise HTTPException(422, f"{definition['label']} is too long.")
        if definition["required"] and not value.strip():
            raise HTTPException(422, f"{definition['label']} is required.")
        if definition["type"] == "datetime" and not value.strip():
            value = datetime.now().astimezone().strftime("%Y/%m/%d %H:%M:%S")
        elif definition["type"] == "datetime":
            value = _complete_time(value)
        validated[name] = value
    return validated


def _complete_time(value: str) -> str:
    match = re.fullmatch(r"(\d{4}/\d{1,2}/\d{1,2})\s+(\d{1,2}:\d{1,2})", value.strip())
    if match:
        return f"{match.group(1)} {match.group(2)}:00"
    return value


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        **result,
        "files": [
            {key: value for key, value in item.items() if key != "path"}
            for item in result.get("files", [])
        ],
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


async def _cleanup_loop() -> None:
    while True:
        cutoff = time.time() - 5 * 24 * 60 * 60
        await asyncio.to_thread(_cleanup_files, UPLOADS_ROOT, cutoff, False)
        await asyncio.to_thread(_cleanup_files, RESULTS_ROOT, cutoff, True)
        await asyncio.sleep(60 * 60)


def _cleanup_files(root: Path, cutoff: float, preserve_text: bool) -> None:
    if not root.exists():
        return
    text_suffixes = {".txt", ".log", ".json", ".csv", ".md", ".xml", ".yaml", ".yml"}
    for path in root.rglob("*"):
        if not path.is_file() or path.stat().st_mtime >= cutoff:
            continue
        if preserve_text and path.suffix.casefold() in text_suffixes:
            continue
        path.unlink(missing_ok=True)
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


_transport_security = _mcp_transport_security()
_mcp_app = MCP_SERVER.streamable_http_app(
    streamable_http_path="/",
    transport_security=_transport_security,
)
app.mount("/mcp", _mcp_app)
