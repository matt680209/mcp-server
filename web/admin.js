const loginView = document.querySelector("#login-view");
const settingsView = document.querySelector("#settings-view");
const settingsForm = document.querySelector("#settings-form");
const messageBox = document.querySelector("#settings-message");
let currentSettings = null;

function showMessage(message, isError = false) {
  messageBox.textContent = message;
  messageBox.hidden = !message;
  messageBox.classList.toggle("is-error", isError);
}

function lineList(value) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function makeEditorLabel(caption, control) {
  const label = document.createElement("label");
  const title = document.createElement("span");
  title.className = "field-label";
  title.textContent = caption;
  label.append(title, control);
  return label;
}

function textInput(value, placeholder = "") {
  const input = document.createElement("input");
  input.type = "text";
  input.value = value || "";
  input.placeholder = placeholder;
  return input;
}

function numberInput(value, minimum, maximum) {
  const input = document.createElement("input");
  input.type = "number";
  input.min = String(minimum);
  input.max = String(maximum);
  input.value = String(value);
  return input;
}

function checkInput(checked) {
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = Boolean(checked);
  const label = document.createElement("label");
  label.className = "check-label";
  label.append(input, document.createTextNode("Enabled"));
  return { input, label };
}

function removeButton(row, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "remove-button";
  button.textContent = "Remove";
  button.setAttribute("aria-label", `Remove ${label}`);
  button.addEventListener("click", () => row.remove());
  return button;
}

function addApplication(application = {}) {
  const row = document.createElement("div");
  row.className = "editor-row app-row";
  const id = textInput(application.id || "", "Application ID");
  id.dataset.key = "id";
  const name = textInput(application.name || "", "Display name");
  name.dataset.key = "name";
  const terms = textInput((application.match_terms || []).join(", "), "Match terms, separated by commas");
  terms.dataset.key = "match_terms";
  const adapter = document.createElement("select");
  adapter.dataset.key = "adapter";
  adapter.add(new Option("demo adapter", "demo"));
  adapter.add(new Option("IntelAvatar", "intelavatar"));
  adapter.value = application.adapter || "demo";
  const concurrency = numberInput(application.max_concurrency || 15, 1, 64);
  concurrency.dataset.key = "max_concurrency";
  const timeout = numberInput(application.timeout_seconds || 900, 1, 86400);
  timeout.dataset.key = "timeout_seconds";
  const enabled = checkInput(application.enabled !== false);
  enabled.input.dataset.key = "enabled";
  row.append(
    makeEditorLabel("Application ID", id),
    makeEditorLabel("Name", name),
    makeEditorLabel("Match terms", terms),
    makeEditorLabel("Adapter", adapter),
    makeEditorLabel("Concurrency", concurrency),
    makeEditorLabel("Timeout (seconds)", timeout),
    enabled.label,
    removeButton(row, "Application"),
  );
  document.querySelector("#applications-list").append(row);
  refreshTestApplications();
}

function addField(field = {}) {
  const row = document.createElement("div");
  row.className = "editor-row field-row";
  const name = textInput(field.name || "", "field_name");
  name.dataset.key = "name";
  const label = textInput(field.label || "", "Display label");
  label.dataset.key = "label";
  const type = document.createElement("select");
  type.dataset.key = "type";
  [["text", "Single-line text"], ["textarea", "Multiline text"], ["datetime", "Date and time"]].forEach(([value, title]) => type.add(new Option(title, value)));
  type.value = field.type || "text";
  const maxLength = numberInput(field.max_length || 1000, 1, 100000);
  maxLength.dataset.key = "max_length";
  const required = checkInput(field.required);
  required.input.dataset.key = "required";
  const placeholder = textInput(field.placeholder || "", "Placeholder text");
  placeholder.dataset.key = "placeholder";
  row.append(
    makeEditorLabel("Field name", name),
    makeEditorLabel("Label", label),
    makeEditorLabel("Type", type),
    makeEditorLabel("Character limit", maxLength),
    required.label,
    makeEditorLabel("Placeholder", placeholder),
    removeButton(row, "input field"),
  );
  document.querySelector("#fields-list").append(row);
}

function refreshTestApplications() {
  const selector = document.querySelector("#test-application");
  const selected = selector.value;
  selector.replaceChildren();
  document.querySelectorAll("#applications-list .app-row").forEach((row) => {
    const id = row.querySelector('[data-key="id"]').value.trim();
    const name = row.querySelector('[data-key="name"]').value.trim();
    const enabled = row.querySelector('[data-key="enabled"]').checked;
    if (id && enabled) selector.add(new Option(name || id, id));
  });
  if ([...selector.options].some((option) => option.value === selected)) selector.value = selected;
}

function renderSettings(settings) {
  currentSettings = settings;
  document.querySelector("#dns-name").value = settings.network.dns_name || "";
  document.querySelector("#network-host").value = settings.network.host;
  document.querySelector("#network-port").value = settings.network.port;
  document.querySelector("#max-users").value = settings.limits.max_users;
  document.querySelector("#per-session").value = settings.limits.per_session;
  document.querySelector("#max-concurrency").value = settings.limits.max_concurrency;
  document.querySelector("#max-queue").value = settings.limits.max_queue;
  document.querySelector("#allowed-hosts").value = settings.source_access.allowed_hosts.join("\n");
  document.querySelector("#shared-roots").value = settings.source_access.shared_roots.join("\n");
  document.querySelector("#applications-list").replaceChildren();
  document.querySelector("#fields-list").replaceChildren();
  settings.applications.forEach(addApplication);
  settings.fields.forEach(addField);
  renderTestFields(settings.fields);
  refreshTestApplications();
}

function renderTestFields(fields) {
  const container = document.querySelector("#test-fields");
  container.replaceChildren();
  for (const field of fields) {
    const wrapper = document.createElement("label");
    wrapper.className = "field-wrap";
    const title = document.createElement("span");
    title.className = "field-label";
    title.textContent = field.label;
    const control = field.type === "textarea" ? document.createElement("textarea") : document.createElement("input");
    control.name = field.name;
    control.maxLength = field.max_length;
    control.placeholder = field.placeholder || "";
    control.required = field.required;
    if (field.type === "textarea") control.rows = 3;
    else control.type = "text";
    wrapper.append(title, control);
    container.append(wrapper);
  }
}

function collectSettings() {
  const applications = [...document.querySelectorAll("#applications-list .app-row")].map((row) => ({
    id: row.querySelector('[data-key="id"]').value.trim(),
    name: row.querySelector('[data-key="name"]').value.trim(),
    adapter: row.querySelector('[data-key="adapter"]').value,
    match_terms: lineList(row.querySelector('[data-key="match_terms"]').value.replaceAll(",", "\n")),
    enabled: row.querySelector('[data-key="enabled"]').checked,
    max_concurrency: Number(row.querySelector('[data-key="max_concurrency"]').value),
    timeout_seconds: Number(row.querySelector('[data-key="timeout_seconds"]').value),
  }));
  const fields = [...document.querySelectorAll("#fields-list .field-row")].map((row) => ({
    name: row.querySelector('[data-key="name"]').value.trim(),
    label: row.querySelector('[data-key="label"]').value.trim(),
    type: row.querySelector('[data-key="type"]').value,
    required: row.querySelector('[data-key="required"]').checked,
    max_length: Number(row.querySelector('[data-key="max_length"]').value),
    placeholder: row.querySelector('[data-key="placeholder"]').value,
  }));
  return {
    network: {
      dns_name: document.querySelector("#dns-name").value.trim(),
      host: document.querySelector("#network-host").value.trim(),
      port: Number(document.querySelector("#network-port").value),
    },
    limits: {
      max_users: Number(document.querySelector("#max-users").value),
      per_session: Number(document.querySelector("#per-session").value),
      max_concurrency: Number(document.querySelector("#max-concurrency").value),
      max_queue: Number(document.querySelector("#max-queue").value),
    },
    applications,
    fields,
    source_access: {
      allowed_hosts: lineList(document.querySelector("#allowed-hosts").value),
      shared_roots: lineList(document.querySelector("#shared-roots").value),
    },
  };
}

async function getJson(url, options = {}) {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Request failed.");
  return data;
}

async function loadSettings() {
  const data = await getJson("/api/admin/settings");
  loginView.hidden = true;
  settingsView.hidden = false;
  renderSettings(data.settings);
}

async function initialize() {
  try {
    const status = await getJson("/api/admin/status");
    document.querySelector("#password-notice").hidden = status.password_configured;
    if (!status.authenticated) {
      loginView.hidden = false;
      settingsView.hidden = true;
      return;
    }
    await loadSettings();
  } catch {
    document.querySelector("#password-notice").hidden = true;
    loginView.hidden = false;
    settingsView.hidden = true;
  }
}

document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.querySelector("#login-error");
  error.hidden = true;
  try {
    await getJson("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: document.querySelector("#admin-password").value }),
    });
    document.querySelector("#admin-password").value = "";
    await loadSettings();
  } catch (reason) {
    error.textContent = reason.message;
    error.hidden = false;
  }
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  await getJson("/api/admin/logout", { method: "POST" });
  settingsView.hidden = true;
  loginView.hidden = false;
});

document.querySelector("#add-application").addEventListener("click", () => addApplication());
document.querySelector("#add-field").addEventListener("click", () => addField());
document.querySelector("#applications-list").addEventListener("input", refreshTestApplications);
document.querySelector("#applications-list").addEventListener("change", refreshTestApplications);

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showMessage("");
  try {
    const response = await getJson("/api/admin/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectSettings()),
    });
    renderSettings(response.settings);
    showMessage(response.restart_required ? "Settings saved. Restart the server to apply the DNS name, bind address, or port." : "Settings saved.");
  } catch (error) {
    showMessage(error.message, true);
  }
});

document.querySelector("#test-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const output = document.querySelector("#test-result");
  const file = document.querySelector("#test-file").files[0];
  if (!file) return;
  const fields = Object.fromEntries([...document.querySelectorAll("#test-fields [name]")].map((control) => [control.name, control.value]));
  const data = new FormData();
  data.set("application_id", document.querySelector("#test-application").value);
  data.set("source_kind", "upload");
  data.set("fields_json", JSON.stringify(fields));
  data.set("upload", file, file.name);
  output.textContent = "Submitting test task...";
  try {
    const task = await getJson("/api/admin/test", { method: "POST", body: data });
    output.textContent = `Task ${task.task_id.slice(0, 8)} submitted. Waiting for results...`;
    await pollTask(task.task_id, output);
  } catch (error) {
    output.textContent = error.message;
  }
});

async function pollTask(taskId, output) {
  for (let attempt = 0; attempt < 900; attempt += 1) {
    const task = await getJson(`/api/tasks/${encodeURIComponent(taskId)}`);
    output.textContent = `${task.status} · ${task.message || ""}`;
    if (task.status === "SUCCEEDED") {
      const result = await getJson(`/api/tasks/${encodeURIComponent(taskId)}/result`);
      output.replaceChildren();
      const text = document.createElement("pre");
      text.className = "result-copy";
      text.textContent = result.text || JSON.stringify(result);
      output.append(text);
      for (const file of result.files || []) {
        const link = document.createElement("a");
        link.className = "result-download";
        link.href = `/api/tasks/${encodeURIComponent(taskId)}/files/${encodeURIComponent(file.file_id)}`;
        link.textContent = `Download ${file.name}`;
        output.append(link);
      }
      return;
    }
    if (["FAILED", "CANCELLED", "TIMED_OUT"].includes(task.status)) {
      output.textContent = task.error || task.message || task.status;
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
  output.textContent = "The test is still running. Check the workspace shortly.";
}

initialize();
