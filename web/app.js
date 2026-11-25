const form = document.querySelector("#task-form");
const fieldContainer = document.querySelector("#dynamic-fields");
const taskList = document.querySelector("#task-list");
const formError = document.querySelector("#form-error");
const submitButton = document.querySelector("#submit-task");
const applicationSelect = document.querySelector("#application-id");
const serviceStatus = document.querySelector("#service-status");
const stateDot = document.querySelector(".state-dot");
const tasks = new Map();
const sockets = new Map();

function showError(message) {
  formError.textContent = message;
  formError.hidden = !message;
}

function makeField(definition) {
  const wrapper = document.createElement("div");
  wrapper.className = "field-wrap";
  const label = document.createElement("label");
  label.className = "field-label";
  label.htmlFor = `field-${definition.name}`;
  label.textContent = definition.label;
  if (!definition.required) {
    const optional = document.createElement("span");
    optional.className = "optional";
    optional.textContent = "Optional";
    label.append(optional);
  }
  const control = definition.type === "textarea" ? document.createElement("textarea") : document.createElement("input");
  control.id = `field-${definition.name}`;
  control.name = definition.name;
  control.maxLength = definition.max_length;
  control.placeholder = definition.placeholder || "";
  control.required = definition.required;
  if (definition.type === "textarea") {
    control.rows = 5;
  } else {
    control.type = "text";
    control.autocomplete = "off";
    if (definition.type === "datetime") control.inputMode = "text";
  }
  const hint = document.createElement("small");
  hint.className = "field-hint";
  hint.textContent = `Up to ${definition.max_length} characters`;
  wrapper.append(label, control, hint);
  return wrapper;
}

function selectedSourceKind() {
  return document.querySelector('input[name="source-kind"]:checked')?.value || "upload";
}

function updateSourceView() {
  const kind = selectedSourceKind();
  document.querySelector("#upload-source").hidden = kind !== "upload";
  document.querySelector("#url-source").hidden = kind !== "url";
  document.querySelector("#shared-source").hidden = kind !== "shared";
  document.querySelectorAll(".source-choice").forEach((choice) => {
    choice.classList.toggle("is-selected", choice.querySelector("input").checked);
  });
}

function formatTime(timestamp) {
  if (!timestamp) return "Just now";
  return new Intl.DateTimeFormat("en-US", { dateStyle: "short", timeStyle: "short" }).format(new Date(timestamp * 1000));
}

function isActive(task) {
  return task.status === "QUEUED" || task.status === "RUNNING";
}

function renderTasks() {
  const sorted = [...tasks.values()].sort((a, b) => b.created_at - a.created_at);
  document.querySelector("#task-count").textContent = `${sorted.length} task${sorted.length === 1 ? "" : "s"}`;
  const activeCount = sorted.filter(isActive).length;
  document.querySelector("#queue-state").textContent = activeCount ? `${activeCount} active or queued` : "No active tasks";
  taskList.replaceChildren();
  if (!sorted.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = '<span class="empty-mark" aria-hidden="true">—</span><p>No tasks yet</p><small>Submitted tasks will appear here.</small>';
    taskList.append(empty);
    return;
  }
  for (const task of sorted) taskList.append(makeTaskCard(task));
}

function makeTaskCard(task) {
  const card = document.createElement("article");
  card.className = "task-item";
  const top = document.createElement("div");
  top.className = "task-item-top";
  const title = document.createElement("div");
  title.className = "task-title";
  title.textContent = task.source_name || `Task ${task.task_id.slice(0, 8)}`;
  const status = document.createElement("span");
  status.className = `status-pill ${task.status}`;
  status.textContent = statusLabels[task.status] || task.status;
  top.append(title, status);
  const meta = document.createElement("div");
  meta.className = "task-meta";
  const created = document.createElement("span");
  created.textContent = formatTime(task.created_at);
  const identifier = document.createElement("span");
  identifier.textContent = task.task_id.slice(0, 8).toUpperCase();
  meta.append(created, identifier);
  const track = document.createElement("div");
  track.className = "progress-track";
  const fill = document.createElement("div");
  fill.className = "progress-fill";
  fill.style.width = `${Math.max(0, Math.min(100, task.progress || 0))}%`;
  track.append(fill);
  const actions = document.createElement("div");
  actions.className = "task-actions";
  const message = document.createElement("span");
  message.className = "task-message";
  message.textContent = task.error || task.message || "";
  actions.append(message);
  if (isActive(task)) {
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "small-action";
    cancel.textContent = "Cancel task";
    cancel.addEventListener("click", () => cancelTask(task.task_id));
    actions.append(cancel);
  }
  card.append(top, meta, track, actions);
  if (task.status === "SUCCEEDED" && task.result) {
    for (const warning of task.result.warnings || []) {
      const notice = document.createElement("p");
      notice.className = "form-error";
      notice.textContent = warning;
      card.append(notice);
    }
    if (task.result.report && typeof task.result.report.html === "string") {
      const report = document.createElement("section");
      report.className = "analysis-report";
      report.innerHTML = task.result.report.html;
      card.append(report);
    } else if (task.result.text) {
      const output = document.createElement("p");
      output.className = "result-copy";
      output.textContent = task.result.text;
      card.append(output);
    }
    for (const file of task.result.files || []) {
      const link = document.createElement("a");
      link.className = "result-download";
      link.href = `/api/tasks/${encodeURIComponent(task.task_id)}/files/${encodeURIComponent(file.file_id)}`;
      link.textContent = `Download ${file.name}`;
      link.download = file.name;
      card.append(link);
    }
  }
  return card;
}

const statusLabels = {
  QUEUED: "Queued",
  RUNNING: "Running",
  SUCCEEDED: "Completed",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
  TIMED_OUT: "Timed out",
};

function updateTask(task) {
  tasks.set(task.task_id, { ...(tasks.get(task.task_id) || {}), ...task });
  renderTasks();
  if (isActive(task)) connectTask(task.task_id);
  else closeSocket(task.task_id);
}

function closeSocket(taskId) {
  const socket = sockets.get(taskId);
  if (socket) {
    socket.close();
    sockets.delete(taskId);
  }
}

function connectTask(taskId) {
  if (sockets.has(taskId)) return;
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${location.host}/ws/tasks/${encodeURIComponent(taskId)}`);
  sockets.set(taskId, socket);
  socket.addEventListener("message", (event) => {
    try { updateTask(JSON.parse(event.data)); } catch {}
  });
  socket.addEventListener("close", () => sockets.delete(taskId));
  socket.addEventListener("error", () => socket.close());
}

async function loadTasks() {
  const response = await fetch("/api/tasks", { credentials: "same-origin" });
  if (!response.ok) throw new Error("Could not load task history.");
  const items = await response.json();
  tasks.clear();
  for (const task of items) tasks.set(task.task_id, task);
  renderTasks();
  for (const task of items.filter(isActive)) connectTask(task.task_id);
}

async function cancelTask(taskId) {
  try {
    const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not cancel the task.");
    updateTask(data);
  } catch (error) {
    showToast(error.message);
  }
}

function showToast(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  window.setTimeout(() => toast.classList.remove("is-visible"), 3200);
}

async function initialize() {
  try {
    await fetch("/api/session", { credentials: "same-origin" });
    const response = await fetch("/api/form-config");
    if (!response.ok) throw new Error("Could not load form settings.");
    const config = await response.json();
    fieldContainer.replaceChildren(...config.fields.map(makeField));
    applicationSelect.replaceChildren(new Option("Select an application", ""));
    for (const application of config.applications) {
      applicationSelect.add(new Option(application.name, application.id));
    }
    if (!config.applications.length) throw new Error("No applications are available. Contact an administrator.");
    applicationSelect.disabled = false;
    serviceStatus.textContent = "Service ready";
    await loadTasks();
  } catch (error) {
    serviceStatus.textContent = "Connection error";
    stateDot.classList.add("is-error");
    showError(error.message);
  }
}

document.querySelectorAll('input[name="source-kind"]').forEach((input) => input.addEventListener("change", updateSourceView));
document.querySelector("#upload-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  document.querySelector("#file-label").textContent = file ? file.name : "Choose one file";
  document.querySelector("#file-hint").textContent = file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : "Any file type";
});
document.querySelector("#refresh-tasks").addEventListener("click", () => loadTasks().catch((error) => showToast(error.message)));

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  const sourceKind = selectedSourceKind();
  const sourceValue = sourceKind === "url" ? document.querySelector("#source-url").value.trim() : sourceKind === "shared" ? document.querySelector("#shared-path").value.trim() : "";
  const fields = Object.fromEntries([...fieldContainer.querySelectorAll("input[name], textarea[name]")].map((control) => [control.name, control.value]));
  const data = new FormData();
  if (!applicationSelect.value) return showError("Select an application.");
  data.set("application_id", applicationSelect.value);
  data.set("source_kind", sourceKind);
  data.set("source_value", sourceValue);
  data.set("fields_json", JSON.stringify(fields));
  if (sourceKind === "upload") {
    const file = document.querySelector("#upload-file").files[0];
    if (!file) return showError("Choose a file first.");
    data.set("upload", file, file.name);
  } else if (!sourceValue) {
    return showError(sourceKind === "url" ? "Enter a file URL." : "Enter a Shared Folder file path.");
  }
  submitButton.disabled = true;
  submitButton.querySelector("span").textContent = "Submitting...";
  try {
    const response = await fetch("/api/tasks", { method: "POST", body: data, credentials: "same-origin" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Task submission failed.");
    result.source_name = sourceKind === "upload" ? document.querySelector("#upload-file").files[0].name : sourceValue;
    updateTask(result);
    showToast("Task submitted.");
    document.querySelector("#upload-file").value = "";
    document.querySelector("#file-label").textContent = "Choose one file";
    document.querySelector("#file-hint").textContent = "Any file type";
  } catch (error) {
    showError(error.message);
  } finally {
    submitButton.disabled = false;
    submitButton.querySelector("span").textContent = "Submit analysis";
  }
});

updateSourceView();
initialize();
