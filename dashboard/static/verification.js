const registryState = {
  view: "leads",
  page: 1,
  pages: 1,
  sort: "created_at",
  order: "desc",
  requestController: null,
  searchTimer: null,
};

const registryNumber = new Intl.NumberFormat("ru-RU");
const registryDateTime = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const escapeRegistryHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const registryLocalIso = (value) => {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const registryMoscowToday = () => {
  const [year, month, day] = document.body.dataset.today.split("-").map(Number);
  return new Date(year, month - 1, day, 12);
};

function formatRegistryDate(value) {
  if (!value) return "Дата не зафиксирована";
  const [year, month, day] = value.split("-");
  return `${day}.${month}.${year}`;
}

function setRegistryLoading(active) {
  document.querySelector("#loadingLine").classList.toggle("active", active);
}

function showRegistryError(message = "") {
  const banner = document.querySelector("#errorBanner");
  banner.hidden = !message;
  banner.textContent = message;
}

function selectedRegistryValues(component) {
  return [...component.querySelectorAll('input:not([value="Все"]):checked')]
    .map((input) => input.value);
}

function updateRegistryMultiLabel(component) {
  const selected = selectedRegistryValues(component);
  const label = component.querySelector(".multi-trigger span");
  if (!selected.length) label.textContent = component.dataset.allLabel;
  else if (selected.length === 1) label.textContent = selected[0];
  else label.textContent = `${selected.length} выбрано`;
}

function closeRegistryMultiSelect(component) {
  component.querySelector(".multi-menu").hidden = true;
  component.querySelector(".multi-trigger").setAttribute("aria-expanded", "false");
}

function closeAllRegistryMultiSelects(except = null) {
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    if (component !== except) closeRegistryMultiSelect(component);
  });
}

function fillRegistryMultiFilter(filterName, values) {
  const component = document.querySelector(`[data-multi-filter="${filterName}"]`);
  const selectedBefore = new Set(selectedRegistryValues(component));
  const options = component.querySelector(".multi-options");
  options.innerHTML = "";
  values.forEach((value) => {
    const label = document.createElement("label");
    label.className = "multi-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = value;
    input.checked = selectedBefore.has(value);
    const text = document.createElement("span");
    text.textContent = value;
    label.append(input, text);
    options.append(label);
  });
  component.querySelector('input[value="Все"]').checked = selectedBefore.size === 0;
  updateRegistryMultiLabel(component);
}

function resetRegistryMultiFilters() {
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    component.querySelectorAll('input:not([value="Все"])').forEach((input) => {
      input.checked = false;
    });
    component.querySelector('input[value="Все"]').checked = true;
    updateRegistryMultiLabel(component);
    closeRegistryMultiSelect(component);
  });
}

function setupRegistryMultiFilters() {
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    const trigger = component.querySelector(".multi-trigger");
    const menu = component.querySelector(".multi-menu");
    trigger.addEventListener("click", () => {
      const willOpen = menu.hidden;
      closeAllRegistryMultiSelects(component);
      menu.hidden = !willOpen;
      trigger.setAttribute("aria-expanded", String(willOpen));
    });
    component.addEventListener("change", (event) => {
      if (!(event.target instanceof HTMLInputElement)) return;
      const allInput = component.querySelector('input[value="Все"]');
      const specificInputs = [...component.querySelectorAll('input:not([value="Все"])')];
      if (event.target.value === "Все") {
        if (event.target.checked) specificInputs.forEach((input) => { input.checked = false; });
        else if (!specificInputs.some((input) => input.checked)) allInput.checked = true;
      } else {
        if (event.target.checked) allInput.checked = false;
        if (!specificInputs.some((input) => input.checked)) allInput.checked = true;
      }
      updateRegistryMultiLabel(component);
      registryState.page = 1;
      loadRegistry();
    });
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-multi-filter]")) closeAllRegistryMultiSelects();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAllRegistryMultiSelects();
  });
}

function setRegistryDefaultPeriod() {
  const today = registryMoscowToday();
  const start = new Date(today.getFullYear(), today.getMonth(), 1);
  const minDate = document.body.dataset.minDate;
  document.querySelector("#dateFrom").value = registryLocalIso(start) < minDate
    ? minDate
    : registryLocalIso(start);
  document.querySelector("#dateTo").value = registryLocalIso(today);
}

function applyRegistryPreset(name) {
  const today = registryMoscowToday();
  let start = new Date(today);
  if (name === "week") start.setDate(today.getDate() - 6);
  else if (name === "month") start = new Date(today.getFullYear(), today.getMonth(), 1);
  else if (name === "quarter") {
    start = new Date(today.getFullYear(), Math.floor(today.getMonth() / 3) * 3, 1);
  }
  const minDate = document.body.dataset.minDate;
  document.querySelector("#dateFrom").value = registryLocalIso(start) < minDate
    ? minDate
    : registryLocalIso(start);
  document.querySelector("#dateTo").value = registryLocalIso(today);
  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.classList.toggle("active", button.dataset.preset === name);
  });
  registryState.page = 1;
  loadRegistry();
}

function registryParams(includePage = true) {
  const params = new URLSearchParams({
    view: registryState.view,
    date_from: document.querySelector("#dateFrom").value,
    date_to: document.querySelector("#dateTo").value,
    q: document.querySelector("#registrySearch").value.trim(),
    sort: registryState.sort,
    order: registryState.order,
    per_page: document.querySelector("#perPage").value,
  });
  if (includePage) params.set("page", registryState.page);
  if (registryState.view === "bookings") {
    const bookingFrom = document.querySelector("#bookingDateFrom").value;
    const bookingTo = document.querySelector("#bookingDateTo").value;
    if (bookingFrom) params.set("booking_date_from", bookingFrom);
    if (bookingTo) params.set("booking_date_to", bookingTo);
  }
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    const values = selectedRegistryValues(component);
    if (!values.length) params.append(component.dataset.multiFilter, "Все");
    else values.forEach((value) => params.append(component.dataset.multiFilter, value));
  });
  return params;
}

async function loadRegistryFilters() {
  const response = await fetch("/api/filters");
  if (response.status === 401) {
    window.location.assign("/login?next=/verification");
    return;
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Не удалось загрузить фильтры");
  fillRegistryMultiFilter("branch", data.options.branch);
  fillRegistryMultiFilter("source", data.options.source);
  ["dateFrom", "dateTo", "bookingDateFrom", "bookingDateTo"].forEach((id) => {
    document.querySelector(`#${id}`).min = data.min_date;
    document.querySelector(`#${id}`).max = data.max_date;
  });
  document.querySelector("#syncStatus span:last-child").textContent = data.last_sync
    ? `Данные в БД: ${data.last_sync}`
    : "Подключено к базе amoCRM";
}

function renderRegistryTags(tags) {
  if (!tags.length) return '<span class="tag-empty">Нет тегов</span>';
  const visible = tags.slice(0, 3);
  const rest = tags.length - visible.length;
  const title = escapeRegistryHtml(tags.join(", "));
  const chips = visible.map((tag) => `<span class="tag-chip">${escapeRegistryHtml(tag)}</span>`).join("");
  const more = rest > 0 ? `<span class="tag-more">+${rest}</span>` : "";
  return `<div class="tag-list" title="${title}">${chips}${more}</div>`;
}

function renderRegistryRows(rows) {
  const body = document.querySelector("#registryBody");
  const columns = registryState.view === "bookings" ? 9 : 8;
  if (!rows.length) {
    body.innerHTML = `<tr><td class="registry-empty" colspan="${columns}">По выбранным фильтрам сделок не найдено</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => {
    const id = row.lead_url
      ? `<a class="lead-link" href="${escapeRegistryHtml(row.lead_url)}" target="_blank" rel="noopener">${row.lead_id}</a>`
      : `<strong>${row.lead_id}</strong>`;
    const bookingCell = registryState.view === "bookings"
      ? `<td class="date-cell ${row.booking_date ? "" : "date-missing"}">${formatRegistryDate(row.booking_date)}</td>`
      : "";
    return `
      <tr>
        <td>${id}</td>
        <td class="phone-cell">${escapeRegistryHtml(row.client_phone || "—")}</td>
        <td><strong>${escapeRegistryHtml(row.branch)}</strong></td>
        <td>${escapeRegistryHtml(row.source)}</td>
        <td class="date-cell">${registryDateTime.format(new Date(row.created_at))}</td>
        ${bookingCell}
        <td class="status-cell"><strong>${escapeRegistryHtml(row.status)}</strong><small>${escapeRegistryHtml(row.pipeline)}</small></td>
        <td class="lead-name-cell">${escapeRegistryHtml(row.lead_name)}</td>
        <td>${renderRegistryTags(row.tags)}</td>
      </tr>`;
  }).join("");
}

function renderRegistrySort() {
  document.querySelectorAll("[data-registry-sort]").forEach((button) => {
    const active = button.dataset.registrySort === registryState.sort;
    button.classList.toggle("active", active);
    button.querySelector("span").textContent = active
      ? (registryState.order === "asc" ? "↑" : "↓")
      : "↕";
    button.closest("th").setAttribute(
      "aria-sort",
      active ? (registryState.order === "asc" ? "ascending" : "descending") : "none",
    );
  });
}

function renderRegistry(data) {
  document.querySelector("#leadsCount").textContent = registryNumber.format(data.counts.leads);
  document.querySelector("#bookingsCount").textContent = registryNumber.format(data.counts.bookings);
  document.querySelector("#visibleCount").textContent = `Найдено: ${registryNumber.format(data.pagination.total)}`;
  registryState.page = data.pagination.page;
  registryState.pages = data.pagination.pages;
  document.querySelector("#pageStatus").textContent = `Страница ${data.pagination.page} из ${data.pagination.pages}`;
  document.querySelector("#previousPage").disabled = data.pagination.page <= 1;
  document.querySelector("#nextPage").disabled = data.pagination.page >= data.pagination.pages;
  renderRegistryRows(data.rows);
  renderRegistrySort();
}

async function loadRegistry() {
  if (registryState.requestController) registryState.requestController.abort();
  const controller = new AbortController();
  registryState.requestController = controller;
  setRegistryLoading(true);
  showRegistryError();
  try {
    const response = await fetch(`/api/verification?${registryParams().toString()}`, {
      signal: controller.signal,
    });
    if (response.status === 401) {
      window.location.assign("/login?next=/verification");
      return;
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не удалось получить данные");
    renderRegistry(data);
  } catch (error) {
    if (error.name !== "AbortError") showRegistryError(error.message);
  } finally {
    if (registryState.requestController === controller) {
      registryState.requestController = null;
      setRegistryLoading(false);
    }
  }
}

function setRegistryView(view) {
  registryState.view = view;
  registryState.page = 1;
  registryState.sort = view === "bookings" ? "booking_date" : "created_at";
  registryState.order = "desc";
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".booking-date-filter, .booking-column").forEach((element) => {
    element.hidden = view !== "bookings";
  });
  document.querySelector("#registryKicker").textContent = view === "bookings"
    ? "Записи"
    : "Созданные заявки";
  document.querySelector("#registryTitle").textContent = view === "bookings"
    ? "Сделки, дошедшие до этапа «Клиент записан»"
    : "Сделки, созданные за выбранный период";
  loadRegistry();
}

async function initRegistry() {
  setRegistryDefaultPeriod();
  setupRegistryMultiFilters();
  setRegistryLoading(true);
  try {
    await loadRegistryFilters();
    await loadRegistry();
  } catch (error) {
    showRegistryError(error.message);
    setRegistryLoading(false);
  }

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => setRegistryView(button.dataset.view));
  });
  document.querySelectorAll("#dateFrom, #dateTo, #bookingDateFrom, #bookingDateTo").forEach((input) => {
    input.addEventListener("change", () => {
      document.querySelectorAll("[data-preset]").forEach((button) => button.classList.remove("active"));
      registryState.page = 1;
      loadRegistry();
    });
  });
  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.addEventListener("click", () => applyRegistryPreset(button.dataset.preset));
  });
  document.querySelector("#registrySearch").addEventListener("input", () => {
    window.clearTimeout(registryState.searchTimer);
    registryState.searchTimer = window.setTimeout(() => {
      registryState.page = 1;
      loadRegistry();
    }, 350);
  });
  document.querySelector("#perPage").addEventListener("change", () => {
    registryState.page = 1;
    loadRegistry();
  });
  document.querySelectorAll("[data-registry-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.registrySort;
      if (registryState.sort === key) registryState.order = registryState.order === "asc" ? "desc" : "asc";
      else {
        registryState.sort = key;
        registryState.order = ["lead_id", "created_at", "booking_date"].includes(key) ? "desc" : "asc";
      }
      registryState.page = 1;
      loadRegistry();
    });
  });
  document.querySelector("#previousPage").addEventListener("click", () => {
    if (registryState.page > 1) {
      registryState.page -= 1;
      loadRegistry();
    }
  });
  document.querySelector("#nextPage").addEventListener("click", () => {
    if (registryState.page < registryState.pages) {
      registryState.page += 1;
      loadRegistry();
    }
  });
  document.querySelector("#resetFilters").addEventListener("click", () => {
    resetRegistryMultiFilters();
    setRegistryDefaultPeriod();
    document.querySelector("#bookingDateFrom").value = "";
    document.querySelector("#bookingDateTo").value = "";
    document.querySelector("#registrySearch").value = "";
    document.querySelectorAll("[data-preset]").forEach((button) => {
      button.classList.toggle("active", button.dataset.preset === "month");
    });
    registryState.page = 1;
    loadRegistry();
  });
  document.querySelector("#exportCsv").addEventListener("click", () => {
    window.location.assign(`/api/verification/export.csv?${registryParams(false).toString()}`);
  });
}

document.addEventListener("DOMContentLoaded", initRegistry);
