const $ = (sel) => document.querySelector(sel);

function setBusy(busy) {
  ["btn-fetch", "btn-resolve", "btn-its-fetch", "btn-refresh-cache"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = busy;
  });
}

async function loadHealth() {
  const box = $("#health");
  const text = $("#health-text");
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const n = data.authorities_cached ?? 0;
    const its = data.its_cached ?? 0;
    text.textContent = n
      ? `кэш органов: ${n} · ИТС в кэше: ${its}`
      : "кэш пуст — нажмите «Обновить кэш органов»";
    box.className = `status-box ${n ? "ok" : "warn"}`;
    return data;
  } catch (err) {
    text.textContent = "сервер недоступен";
    box.className = "status-box err";
    return null;
  }
}

function renderDoc(doc) {
  if (!doc) return "";
  return `
    <div class="kv">
      <div><span class="k">eoNumber</span><span class="v">${esc(doc.eo_number)}</span></div>
      <div><span class="k">номер</span><span class="v">${esc(doc.number)}</span></div>
      <div><span class="k">дата</span><span class="v">${esc(doc.document_date)}</span></div>
      <div><span class="k">название</span><span class="v">${esc(doc.name || "—")}</span></div>
      <div><span class="k">подписанты</span><span class="v">${esc((doc.signatory_ids || []).join(", ") || "—")}</span></div>
    </div>
  `;
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderResult(data) {
  const root = $("#result");
  const status = data.status || "unknown";
  const match = data.match_type ? ` · ${data.match_type}` : "";

  let body = `
    <div class="result-head">
      <span class="badge ${esc(status)}">${esc(status)}</span>
      <span>${esc(match)}</span>
    </div>
  `;

  if (data.document) body += renderDoc(data.document);

  if (data.pdf_url) {
    body += `<p class="links"><a class="btn primary" href="${esc(data.pdf_url)}" target="_blank" rel="noopener">Открыть PDF</a></p>`;
  }

  if (data.candidates?.length) {
    body += `<p><strong>Кандидаты (${data.candidates.length}):</strong></p><ul class="list">`;
    for (const c of data.candidates) {
      body += `<li><code>${esc(c.eo_number)}</code> — ${esc(c.number)} от ${esc(c.document_date)}</li>`;
    }
    body += "</ul>";
  }

  if (data.diagnostics?.length) {
    body += `<p><strong>Диагностика (другие органы):</strong></p><ul class="list">`;
    for (const d of data.diagnostics) {
      body += `<li><code>${esc(d.eo_number)}</code> · орган ${esc(d.authority_id || "—")}</li>`;
    }
    body += "</ul>";
  }

  if (data.message) {
    body += `<div class="msg">${esc(data.message)}</div>`;
  }

  root.className = "result";
  root.innerHTML = body;
}

function formPayload() {
  return {
    authority_name: $("#authority_name").value.trim() || null,
    authority_guid: $("#authority_guid").value.trim() || null,
    number: $("#number").value.trim() || null,
    date: $("#date").value || null,
    download: $("#download").checked,
  };
}

$("#fetch-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  setBusy(true);
  $("#result").textContent = "Ищем…";
  $("#result").className = "result empty";
  try {
    const res = await fetch("/api/fetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formPayload()),
    });
    const data = await res.json();
    if (!res.ok) {
      renderResult({
        status: "invalid_input",
        message: data.detail || "Ошибка запроса",
      });
    } else {
      renderResult(data);
    }
  } catch (err) {
    renderResult({ status: "network_error", message: String(err) });
  } finally {
    setBusy(false);
  }
});

$("#btn-resolve").addEventListener("click", async () => {
  const name = $("#authority_name").value.trim();
  if (!name) {
    renderResult({ status: "invalid_input", message: "Укажите название органа" });
    return;
  }
  setBusy(true);
  try {
    const res = await fetch(`/api/resolve?name=${encodeURIComponent(name)}`);
    const data = await res.json();
    let html = `
      <div class="result-head"><span class="badge found">resolve</span>
      <span>${data.count} кандидат(ов)</span></div>
      <ul class="list">
    `;
    for (const c of data.candidates.slice(0, 50)) {
      html += `<li><code>${esc(c.guid)}</code><br>${esc(c.name)}</li>`;
    }
    if (data.count > 50) html += `<li>… и ещё ${data.count - 50}</li>`;
    html += "</ul>";
    $("#result").className = "result";
    $("#result").innerHTML = html;
  } catch (err) {
    renderResult({ status: "network_error", message: String(err) });
  } finally {
    setBusy(false);
  }
});

/* --- выбор типа документа --- */
const docTypeInputs = [...document.querySelectorAll('input[name="doc_type"]')];
const npaFields = $("#npa-fields");
const itsFields = $("#its-fields");
const npaResult = $("#result");
const itsResult = $("#its-result");
const refreshCacheBtn = $("#btn-refresh-cache");

function currentDocType() {
  return docTypeInputs.find((i) => i.checked)?.value || "npa";
}

function applyDocType(type) {
  const isIts = type === "its";
  npaFields.hidden = isIts;
  itsFields.hidden = !isIts;
  npaResult.hidden = isIts;
  itsResult.hidden = !isIts;
  refreshCacheBtn.textContent = isIts ? "Заполнить кэш ИТС полностью" : "Обновить кэш органов";
  docTypeInputs.forEach((input) => {
    input.closest(".doc-type-opt").classList.toggle("active", input.value === type);
  });
}

docTypeInputs.forEach((input) => {
  input.addEventListener("change", () => {
    if (input.checked) applyDocType(input.value);
  });
});

applyDocType(currentDocType());

/* --- прогресс-бар обновления кэша (общий для органов и ИТС) --- */
const cacheProgress = $("#cache-progress");
const cacheProgressBar = $("#cache-progress-bar");
const cacheProgressText = $("#cache-progress-text");

function showIndeterminateProgress(text) {
  cacheProgress.hidden = false;
  cacheProgress.classList.add("indeterminate");
  cacheProgressBar.style.width = "";
  cacheProgressText.hidden = false;
  cacheProgressText.textContent = text;
}

function showProgress(percent, text) {
  cacheProgress.hidden = false;
  cacheProgress.classList.remove("indeterminate");
  cacheProgressBar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  cacheProgressText.hidden = false;
  cacheProgressText.textContent = text;
}

function hideProgressSoon() {
  setTimeout(() => {
    cacheProgress.hidden = true;
    cacheProgressText.hidden = true;
  }, 2500);
}

async function refreshAuthoritiesCache() {
  setBusy(true);
  showIndeterminateProgress("Обновляю кэш органов…");
  $("#result").textContent = "Обновляю кэш органов (может занять ~20 с)…";
  $("#result").className = "result empty";
  try {
    const res = await fetch("/api/refresh-authorities", { method: "POST" });
    const data = await res.json();
    renderResult({ status: "found", message: `В кэше ${data.authorities} органов`, document: null });
    await loadHealth();
  } catch (err) {
    renderResult({ status: "network_error", message: String(err) });
  } finally {
    setBusy(false);
    hideProgressSoon();
  }
}

async function refreshItsCacheFully() {
  const confirmed = window.confirm(
    "Полное заполнение кэша ИТС — это обход всего диапазона burondt.ru (~2550 запросов) " +
      "и займёт около 20–30 минут. Обычно это разовая операция или нужна после долгого " +
      "перерыва. Не закрывайте страницу до конца. Продолжить?"
  );
  if (!confirmed) return;

  setBusy(true);
  itsResult.className = "result empty";
  let totalScanned = 0;
  let chunk = 0;

  const startHealth = await loadHealth();
  const lo = startHealth?.its_range_lo ?? 10;
  let confirmedHi = startHealth?.its_range_hi_confirmed ?? null;

  try {
    while (true) {
      chunk += 1;
      if (confirmedHi === null) {
        showIndeterminateProgress(
          `Чанк ${chunk} — диапазон ещё не подтверждён, процент появится после первого полного прогона…`
        );
      } else {
        itsResult.textContent = `Чанк ${chunk}…`;
      }

      const res = await fetch("/api/refresh-its", { method: "POST" });
      const data = await res.json();
      totalScanned += data.scanned || 0;

      if (!data.truncated) {
        confirmedHi = data.hi;
        showProgress(100, `Готово: диапазон до UrlId=${data.hi}, всего запросов: ${totalScanned}`);
      } else if (confirmedHi !== null) {
        const denom = confirmedHi - lo;
        const percent = denom > 0 ? ((data.hi - lo) / denom) * 100 : 0;
        showProgress(
          percent,
          `UrlId ${data.hi} из ~${confirmedHi} (запросов за сеанс: ${totalScanned})`
        );
      }

      await loadHealth();

      if (!data.truncated) {
        renderItsResult({
          status: "found",
          message: `Кэш ИТС заполнен полностью. Диапазон дошёл до UrlId=${data.hi}, всего запросов за сеанс: ${totalScanned}.`,
        });
        break;
      }
    }
  } catch (err) {
    renderItsResult({ status: "network_error", message: String(err) });
  } finally {
    setBusy(false);
    hideProgressSoon();
  }
}

refreshCacheBtn.addEventListener("click", () => {
  if (currentDocType() === "its") {
    refreshItsCacheFully();
  } else {
    refreshAuthoritiesCache();
  }
});

loadHealth();

/* --- автодополнение органа --- */
const authInput = $("#authority_name");
const authGuid = $("#authority_guid");
const suggestList = $("#authority-suggest");
let suggestTimer = null;
let suggestItems = [];
let activeIdx = -1;
let suppressSuggest = false;

function hideSuggest() {
  suggestList.hidden = true;
  suggestList.innerHTML = "";
  activeIdx = -1;
}

function renderSuggest(items, query) {
  suggestItems = items;
  activeIdx = items.length ? 0 : -1;
  if (!query.trim()) {
    hideSuggest();
    return;
  }
  if (!items.length) {
    suggestList.innerHTML = `<li class="ac-empty">Ничего не найдено в кэше</li>`;
    suggestList.hidden = false;
    return;
  }
  suggestList.innerHTML = items
    .map(
      (it, i) =>
        `<li role="option" data-idx="${i}" class="${i === 0 ? "active" : ""}">${esc(it.name)}</li>`
    )
    .join("");
  suggestList.hidden = false;
}

function selectSuggest(idx) {
  const item = suggestItems[idx];
  if (!item) return;
  suppressSuggest = true;
  authInput.value = item.name;
  authGuid.value = item.guid;
  hideSuggest();
  setTimeout(() => {
    suppressSuggest = false;
  }, 0);
}

async function fetchSuggest(q) {
  if (q.trim().length < 2) {
    hideSuggest();
    return;
  }
  try {
    const res = await fetch(
      `/api/authorities/suggest?q=${encodeURIComponent(q)}&limit=15`
    );
    const data = await res.json();
    if (authInput.value.trim() !== q.trim()) return;
    renderSuggest(data.items || [], q);
  } catch {
    hideSuggest();
  }
}

authInput.addEventListener("input", () => {
  if (suppressSuggest) return;
  authGuid.value = "";
  clearTimeout(suggestTimer);
  const q = authInput.value;
  suggestTimer = setTimeout(() => fetchSuggest(q), 180);
});

authInput.addEventListener("keydown", (e) => {
  if (suggestList.hidden || !suggestItems.length) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    activeIdx = (activeIdx + 1) % suggestItems.length;
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    activeIdx = (activeIdx - 1 + suggestItems.length) % suggestItems.length;
  } else if (e.key === "Enter" && activeIdx >= 0) {
    e.preventDefault();
    selectSuggest(activeIdx);
    return;
  } else if (e.key === "Escape") {
    hideSuggest();
    return;
  } else {
    return;
  }
  [...suggestList.querySelectorAll("li[data-idx]")].forEach((li) => {
    li.classList.toggle("active", Number(li.dataset.idx) === activeIdx);
  });
});

suggestList.addEventListener("mousedown", (e) => {
  const li = e.target.closest("li[data-idx]");
  if (!li) return;
  e.preventDefault();
  selectSuggest(Number(li.dataset.idx));
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".ac-wrap")) hideSuggest();
});

/* --- ИТС/НДТ --- */
const ITS_ROLE_LABEL = { document: "справочник", order: "приказ", unknown: "файл" };

function itsFileLine(f) {
  const label = ITS_ROLE_LABEL[f.role] || f.role;
  const link = f.pdf_url
    ? `<a href="${esc(f.pdf_url)}" target="_blank" rel="noopener">открыть PDF</a>`
    : "не скачан";
  const caption = f.caption ? ` — <span class="hint">${esc(f.caption)}</span>` : "";
  return `<li>${esc(label)} (file_id ${esc(f.file_id)}) — ${link}${caption}</li>`;
}

function renderItsResult(data) {
  const root = $("#its-result");
  const status = data.status || "unknown";

  let body = `
    <div class="result-head">
      <span class="badge ${esc(status)}">${esc(status)}</span>
      <span>${esc(data.designation || "")}</span>
    </div>
  `;

  if (data.files?.length) {
    body += `<ul class="list">${data.files.map(itsFileLine).join("")}</ul>`;
  }

  if (data.candidates?.length) {
    body += `<p><strong>Версии (${data.candidates.length}), новые сначала:</strong></p>`;
    for (const c of data.candidates) {
      const filesPreview = c.files?.length
        ? `<ul class="list">${c.files.map(itsFileLine).join("")}</ul>`
        : `<p class="hint">Файлы этой версии ещё не просканированы.</p>`;
      body += `
        <div class="candidate">
          <div class="candidate-head">
            <strong>${esc(c.designation)}</strong> <code>url_id ${esc(c.url_id)}</code>
            <button type="button" class="btn" data-fetch-its-url-id="${esc(c.url_id)}">Скачать эту версию</button>
          </div>
          ${filesPreview}
        </div>
      `;
    }
  }

  if (data.message) {
    body += `<div class="msg">${esc(data.message)}</div>`;
  }

  if (status === "not_found") {
    body += `<div class="msg">Не найдено среди уже просканированных карточек — если кэш ИТС заполнен не полностью, это не значит, что документа нет на burondt.ru. Попробуйте «Заполнить кэш ИТС полностью» ниже.</div>`;
  }

  root.className = "result";
  root.innerHTML = body;
}

async function fetchItsAndRender(payload) {
  setBusy(true);
  itsResult.textContent = "Ищем…";
  itsResult.className = "result empty";
  try {
    const res = await fetch("/api/fetch-its", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ download: true, ...payload }),
    });
    const data = await res.json();
    renderItsResult(data);
  } catch (err) {
    renderItsResult({ status: "network_error", message: String(err) });
  } finally {
    setBusy(false);
  }
}

$("#its-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const designation = $("#its_designation").value.trim();
  if (!designation) {
    renderItsResult({ status: "invalid_input", message: "Укажите обозначение" });
    return;
  }
  fetchItsAndRender({ designation });
});

itsResult.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-fetch-its-url-id]");
  if (!btn) return;
  fetchItsAndRender({ url_id: Number(btn.dataset.fetchItsUrlId) });
});

