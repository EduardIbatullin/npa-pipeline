const $ = (sel) => document.querySelector(sel);

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setBusy(busy) {
  const btn = $("#btn-lookup");
  if (btn) btn.disabled = busy;
}

async function loadHealth() {
  const box = $("#health");
  const text = $("#health-text");
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const n = data.authorities_cached ?? 0;
    const its = data.its_cached ?? 0;
    text.textContent = `НПА-органы: ${n} · ИТС в кэше: ${its}`;
    box.className = `status-box ${n || its ? "ok" : "warn"}`;
    return data;
  } catch {
    text.textContent = "сервер недоступен";
    box.className = "status-box err";
    return null;
  }
}

function fileActions(file) {
  const label = file.label ? `${esc(file.label)}: ` : "";
  return `
    <div class="file-row">
      <span class="file-label">${label}</span>
      <a class="btn primary" href="${esc(file.open_url)}" target="_blank" rel="noopener">Открыть</a>
      <a class="btn" href="${esc(file.download_url)}" download>Скачать</a>
    </div>
  `;
}

function renderResult(data) {
  const root = $("#result");
  if (!data) {
    root.className = "result empty";
    root.textContent = "Введите название и нажмите «Найти».";
    return;
  }

  const status = data.status || "unknown";
  const kind = data.kind === "its" ? "ИТС/НДТ" : data.kind === "npa" ? "НПА" : "";

  let body = `
    <div class="result-head">
      <span class="badge ${esc(status)}">${esc(status)}</span>
      ${kind ? `<span class="kind-tag">${esc(kind)}</span>` : ""}
    </div>
  `;

  if (data.description) {
    body += `<p class="doc-description">${esc(data.description)}</p>`;
  } else if (data.title) {
    body += `<p class="doc-description">${esc(data.title)}</p>`;
  }

  if (data.files?.length) {
    body += `<div class="file-actions">${data.files.map(fileActions).join("")}</div>`;
  }

  if (data.candidates?.length) {
    body += `<p class="hint">Найдено несколько вариантов — выберите нужный:</p><ul class="candidate-list">`;
    for (const c of data.candidates) {
      const id = c.url_id != null ? `data-url-id="${esc(c.url_id)}"` : "";
      const disabled = c.url_id == null ? "disabled" : "";
      body += `
        <li>
          <span>${esc(c.description || c.title || "—")}</span>
          <button type="button" class="btn" ${id} ${disabled}>Выбрать</button>
        </li>`;
    }
    body += "</ul>";
  }

  if (data.message) {
    body += `<div class="msg">${esc(data.message)}</div>`;
  }

  if (status === "not_found" && data.kind === "its") {
    body += `<div class="msg">Не найдено в локальном кэше ИТС. Обновите кэш через CLI: <code>npa refresh-its</code>.</div>`;
  }

  root.className = "result";
  root.innerHTML = body;
}

async function lookup(payload) {
  setBusy(true);
  $("#result").textContent = "Ищем…";
  $("#result").className = "result empty";
  try {
    const res = await fetch("/api/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      renderResult({
        status: "invalid_input",
        message: typeof data.detail === "string" ? data.detail : "Ошибка запроса",
      });
      return;
    }
    renderResult(data);
  } catch (err) {
    renderResult({ status: "network_error", message: String(err) });
  } finally {
    setBusy(false);
  }
}

$("#lookup-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#query").value.trim();
  if (!q) {
    renderResult({ status: "invalid_input", message: "Укажите название документа" });
    return;
  }
  lookup({ q });
});

$("#result").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-url-id]");
  if (!btn) return;
  const urlId = Number(btn.dataset.urlId);
  const q = $("#query").value.trim() || null;
  lookup({ q, url_id: urlId });
});

loadHealth();
