# Avatar MCP

Avatar MCP is a single-process Python service. One Uvicorn worker hosts the browser UI, REST API, WebSocket task progress, and MCP Streamable HTTP endpoint. Browser and MCP requests share the same in-process task manager, application router, task storage, and results.

## Project architecture

```text
main.py
├── Avatar_MCP_icon.png      Header icon
├── Avatar_MCP_logo.png      Header wordmark
├── web/
│   ├── server.py             FastAPI routes, sessions, WebSockets, and MCP mount
│   ├── index.html            English user interface
│   ├── admin.html            English administrator interface
│   ├── app.js                User submission and progress UI
│   ├── admin.js              Settings and administrator test UI
│   ├── style.css             Layout, responsive styling, and blue palette
├── mcp_tools/
│   └── mcp_server.py         MCP tools using the official Python SDK v2
├── application/
│   ├── analyzer.py           Application router and demo adapter
│   ├── config.py             Settings defaults and validation
│   └── sources.py            Upload, URL, and Shared Folder acquisition
├── task_manager/
│   └── manager.py            Session isolation, queue, concurrency, timeout, persistence
├── tests/                    Manager, HTTP, and MCP integration tests
├── data/                     Runtime settings, uploads, results, and task records
├── requirements.txt          Python dependencies
└── README.md                 Setup and deployment guide
```

The two supplied PNG files are served directly from the project root and used in both page headers.

## Requirements

- Python 3.11 or newer
- Windows, Linux, or macOS
- A DNS name and reachable port for remote access
- An HTTPS certificate and private key for internet-facing deployment
- A callable interface for each real desktop or back-end application

Node.js is not required to run the service. It is only useful for checking the frontend JavaScript during development.

## First-time setup

Create a fresh virtual environment on each computer; do not copy `.venv` between machines. Run commands from the project root.

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
$env:MCP_ADMIN_PASSWORD = "replace-with-a-long-unique-secret"
python main.py
```

Linux or macOS:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
export MCP_ADMIN_PASSWORD='replace-with-a-long-unique-secret'
python main.py
```

Open `http://localhost:8000` for the user interface and `http://localhost:8000/admin` for administrator settings. The MCP endpoint is `http://localhost:8000/mcp` and uses Streamable HTTP. The server starts one Uvicorn worker. Run it from the project root so Python imports and file paths resolve correctly.

On Windows, after creating `.venv` and installing the requirements, double-click `local_start.bat` to start the service and open the user interface when it is ready. On first launch, enter and confirm an administrator password; use printable ASCII characters. The launcher encrypts it with Windows DPAPI for the current Windows user and stores it in `data/admin-password.dpapi`. Later launches load it automatically. The file is not portable to another Windows user or computer; set a new password there. To change it, run `local_start.bat set-password` and restart the service. An existing `MCP_ADMIN_PASSWORD` environment variable takes precedence over the saved password, without changing it.

Keep the console window open while using the service; press Ctrl+C to stop it, or run `local_start.bat stop` in another terminal. The stop command only targets instances started by this launcher and interrupts any running tasks. The launcher uses the configured network address and port, so it does not override the bind address to make the service local-only. With HTTPS, the browser opens automatically only if PowerShell trusts the configured certificate; otherwise, open the URL printed in the console.

The administrator password is not stored in `data/settings.json`. When running `main.py` directly or using a service manager instead of the Windows launcher, set `MCP_ADMIN_PASSWORD` in that process's environment. Do not commit the password or place it in source code. Administrator sessions are process-local and expire when the service restarts. Regular users do not need accounts.

## Deployment values to fill in

Complete or verify these values for every new computer:

1. **Administrator secret:** Configure a unique, long secret through the Windows launcher, or set `MCP_ADMIN_PASSWORD` when running the service directly.
2. **DNS name:** Create a DNS record pointing the chosen hostname to this computer or its public ingress address. Enter the hostname in `/admin` under Network.
3. **Bind address and port:** Set the listening address and port in `/admin`. Use `0.0.0.0` to accept connections on the computer's network interfaces; use `127.0.0.1` for local-only access. A bind address or port change requires a restart.
4. **Firewall and routing:** Allow the selected inbound port on the host firewall and, when applicable, the router or cloud firewall. Confirm clients can route to this computer.
5. **HTTPS certificate:** For direct TLS, set `MCP_SSL_CERTFILE` and `MCP_SSL_KEYFILE` to the certificate and private-key file paths. Set `MCP_COOKIE_SECURE=1` when HTTPS is enabled. Do not expose the anonymous user interface over public plain HTTP.
6. **URL sources:** In `/admin`, list exact public hostnames from which users may retrieve files. URL downloads resolving to private or local network addresses and HTTP redirects are rejected.
7. **Shared Folder sources:** Add each permitted share root, for example `\\fileserver\share`. The server's operating-system account must have read permission. Users enter a specific file path under one of these roots; folders are not enumerated.
8. **Applications:** Set an ID, display name, routing match terms, registered adapter ID, per-app concurrency limit, and timeout. The user interface lets users select an enabled application before choosing a file source. API requests that omit an application ID continue to route using match terms against the source filename and question; if none match, the first enabled application is selected.
9. **Input fields:** Set each field's unique name, English label, type (`text`, `textarea`, or `datetime`), required flag, character limit, and placeholder. Empty datetime fields are filled with the server's current local time.
10. **Task limits:** Set maximum active sessions, tasks per session, global concurrency, and queue capacity. Defaults are 5 sessions, 3 tasks per session, 15 globally concurrent tasks, and 100 queued tasks.

Settings are saved to `data/settings.json`. Network binding changes take effect after restart. Task-limit and application changes are applied to the running manager immediately.

The Windows launcher and `main.py` both read this same settings file on each start. Saving a network change in `/admin` updates the file immediately, but the currently running server keeps its existing bind address, port, and MCP host rules until it is stopped and started again. If the file exists but cannot be read or contains invalid JSON, startup fails instead of silently using the default network address.

## Connecting a real application

The included `DemoAnalyzer` is a placeholder. It reports file metadata and submitted fields, then creates a downloadable text result; it does not call a desktop application.

The experimental IntelAvatar adapter can be selected in the Applications editor. It requires Windows, an interactive desktop, Google Chrome, and exactly one `IntelAvatar*.exe` under `MCP_tool/IntelAvatar`. Install Python dependencies from `requirements.txt`. The adapter opens a visible, automation-controlled Chrome window, which may be separate from any browser window opened by IntelAvatar. The current integration accepts ZIP files containing eligible Wi-Fi ETLs, an issue date/time in `YYYY/MM/DD HH:MM:SS` format, and a question. It attempts to record the log as having no IPS case, selects the newest eligible Wi-Fi ETL using the folder timestamp and numeric suffix, and submits the issue time and question. It fails if the no-case prompt is unavailable or if the result is partial. IntelAvatar tasks share one queue across Application IDs; the task timeout begins when execution starts, not while queued. The adapter refuses to start while another IntelAvatar process is running. The text-only MCP tools cannot supply the required ZIP file; use `analyze_file` for an allowed URL or shared path. Desktop automation, process cleanup, and external analysis depend on the target application's environment and services.

Successful IntelAvatar results retain the existing `text` field and add `report`: `schema_version` is 1, `source` is `intelavatar`, `type` is `report` or `text`, `raw` contains the complete Avatar result event, `markdown` is a portable report, and `html` is a safely rendered preview with raw HTML disabled. The browser shows the preview and offers `analysis.txt`, `analysis.md`, and `analysis.json` downloads. Markdown preserves tables and code blocks as source; diagrams are displayed as code unless the client supports them. Raw fields not represented in Markdown remain in `raw`. Existing task results cannot be upgraded retrospectively because their structured source was not saved.

The adapter pauses two seconds before selecting Local File Upload, opening the file picker, entering the question, and accepting "Use log's last time". It also waits two seconds after Send becomes available, restores the requested question if Avatar's delayed prefill replaced it, and verifies the field before sending. A task-scoped Explorer monitor closes newly observed folder windows under the uploaded file's directory, the Windows Downloads `IntelAvatar_files` directory, or timestamped `%TMP%\log_parser_YYMMDD_HHMMSS_ffffff` workspaces after they remain there for two seconds. Only shell windows hosted by `explorer.exe` are eligible: Avatar browser windows are not popup-cleanup targets. Windows already open at task start and unrelated folders are left alone; Explorer processes are never terminated. This is path-and-window filtering, not proof of process ownership. Monitor problems are logged and included in result `warnings`.

Avatar can open multiple local service ports. Startup checks owned-process listeners for an HTTP 200 page containing both Avatar upload tabs rather than using the first listening port, which may belong to an auxiliary service and return 404. The analysis browser stays open while a task is running; after success, failure, or cancellation, its normal task cleanup closes the automation browser and owned Avatar processes.

Immediately before opening the native local-file picker, the adapter brings its automation-controlled Avatar browser page to the front. It does not enable permanent always-on-top mode or activate a different Avatar browser window.

MCP clients call `analyze_file` with `source_kind` (`url` or `shared`), `source_value`, `application_id`, `question`, and `time_value`, then poll `get_task_status` and call `get_result`. URL and shared sources follow the same administrator allowlists as browser submissions. The full report is returned inline by `get_result`; download links are browser-session scoped. Existing MCP tools use a shared `mcp` task namespace: this is not per-client authorization, so protect the endpoint and do not rely on task IDs for isolation between untrusted clients. Browser tasks remain separate and cannot be read with MCP tools.

Implement an adapter in `application/analyzer.py` with an async `execute(task, progress)` method. It should read `task.payload` for the selected file path, configured field values, and result directory; call the application's documented API, library, or non-interactive entry point; report progress via `await progress(percent, message)`; and return a dictionary with `text` and optional downloadable `files`. Register it in `ApplicationRouter.adapters` using a stable adapter ID, then make that ID available in the administrator editor and settings validator. Keep the adapter cancellation-aware: `asyncio.wait_for` and task cancellation can stop cooperative async work, but cannot forcibly terminate a blocked thread or unrelated GUI process. The service never executes arbitrary user-supplied shell commands.

The `run_test` MCP tool requires the administrator password as an argument. The `/admin` test form is preferred because it uses the authenticated administrator cookie and follows the same task flow as normal browser submissions.

## Data, retention, and migration

When an optional issue time is blank for IntelAvatar, MCP does not substitute the current server time. It follows the Stage 03/04 confirmation choice, "Use log's last time", when Avatar asks for a time anchor. An explicitly supplied time is still entered and verified. If analysis completes but owned-process cleanup fails even after a termination retry, the completed report remains available with a visible `warnings` array; close leftover IntelAvatar processes before the next task. Failed MCP `get_result` calls report the task status and recorded error rather than a generic execution failure.

Runtime files are written under `data/`:

- `settings.json`: network, task limits, applications, input fields, and source allowlists.
- `users/<session-hash>/TASK-<id>/task.json`: persistent task metadata and text inputs/results.
- `uploads/`: uploaded or acquired source files, automatically deleted after five days.
- `results/`: result files. Text files are retained indefinitely; non-text files are deleted after five days.

The default `.gitignore` excludes `data/` and `.venv/`. Back up `data/` separately if settings or permanent text records must survive disk loss. To deploy on another computer, copy the source tree, recreate `.venv`, reinstall dependencies, set the new machine's secrets and certificate paths, and review the saved DNS, bind, Shared Folder, and URL allowlist values before starting.

Only copy the full `data/` directory when migrating existing task history as well as settings. Task records contain local file paths, so keep the project/data folder layout the same or update the stored paths if the new computer uses a different location. Do not copy `.venv`; it contains machine-specific executables.

Uploads have no application-level size or file-extension limit by design. Ensure the destination disk has enough space and protect it with appropriate operating-system permissions.

## Security and operations

- Anonymous users are isolated by an HTTP-only browser Session cookie. Each Session can list, read, cancel, and download only its own tasks.
- Administrator endpoints require an authenticated cookie. Set `MCP_ADMIN_PASSWORD` before exposing the service.
- URL hosts and Shared Folder roots are administrator-controlled to prevent arbitrary internal file access.
- The service starts exactly one Uvicorn worker. Do not increase workers: task state, WebSocket subscriptions, and MCP sessions are in-process.
- The service stores permanent task text and metadata; apply local disk access controls and backups accordingly.
- A same-process Python server cannot forcibly stop a non-cooperative GUI application or blocked thread when a task times out.

## Tests

```powershell
python -m unittest discover -v
```

The tests cover per-application queue fairness, timeout and cancellation, Session ownership, restart recovery of task records, browser upload/result/WebSocket flow, administrator testing, and MCP tool registration and calls.

On Windows, run `powershell -NoProfile -File .\tests\test_admin_password.ps1` to test the launcher's encrypted password storage.
