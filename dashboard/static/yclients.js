const ycState = {
  data: null,
  view: "visits",
  requestController: null,
  colorController: null,
  colorPage: 1,
  colorPages: 1,
  colorSearchTimer: null,
};

const ycNumber = new Intl.NumberFormat("ru-RU");
const ycMoney = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 0,
});
const ycDateTime = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

function ycEscape(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function ycFormatMoney(value) {
  return value === null || value === undefined ? "—" : ycMoney.format(Number(value));
}

function ycFormatDate(value) {
  if (!value) return "—";
  return ycDateTime.format(new Date(value));
}

function ycLocalIso(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function ycToday() {
  const parts = document.body.dataset.today.split("-").map(Number);
  return new Date(parts[0], parts[1] - 1, parts[2]);
}

function ycSetDefaultPeriod() {
  const today = ycToday();
  const start = new Date(today.getFullYear(), today.getMonth(), 1);
  const minDate = document.body.dataset.minDate;
  document.querySelector("#dateFrom").value = ycLocalIso(start) < minDate ? minDate : ycLocalIso(start);
  document.querySelector("#dateTo").value = ycLocalIso(today);
}

function ycShowError(message = "") {
  const banner = document.querySelector("#errorBanner");
  banner.hidden = !message;
  banner.textContent = message;
}

function ycSetLoading(active) {
  document.querySelector("#loadingLine").classList.toggle("active", active);
}

function ycSelectedValues(component) {
  return [...component.querySelectorAll('.multi-options input[type="checkbox"]:checked')]
    .map((input) => input.value);
}

function ycUpdateMultiLabel(component) {
  const values = ycSelectedValues(component);
  const label = component.querySelector(".multi-trigger span");
  if (!values.length) label.textContent = component.dataset.allLabel;
  else if (values.length === 1) label.textContent = values[0];
  else label.textContent = `${values.length} выбрано`;
}

function ycCloseMenus(except = null) {
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    if (component === except) return;
    component.querySelector(".multi-menu").hidden = true;
    component.querySelector(".multi-trigger").setAttribute("aria-expanded", "false");
  });
}

function ycFillFilter(key, values) {
  const component = document.querySelector(`[data-multi-filter="${key}"]`);
  component.querySelector(".multi-options").innerHTML = values.map((value) => `
    <label class="multi-option"><input type="checkbox" value="${ycEscape(value)}"><span>${ycEscape(value)}</span></label>
  `).join("");
  ycUpdateMultiLabel(component);
}

function ycSetupFilters() {
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    const trigger = component.querySelector(".multi-trigger");
    const menu = component.querySelector(".multi-menu");
    const all = component.querySelector('.select-all input[value="Все"]');
    trigger.addEventListener("click", () => {
      const shouldOpen = menu.hidden;
      ycCloseMenus(component);
      menu.hidden = !shouldOpen;
      trigger.setAttribute("aria-expanded", String(shouldOpen));
    });
    all.addEventListener("change", () => {
      if (all.checked) {
        component.querySelectorAll('.multi-options input[type="checkbox"]').forEach((input) => { input.checked = false; });
      }
      ycUpdateMultiLabel(component);
      loadYclients();
    });
    component.querySelector(".multi-options").addEventListener("change", () => {
      const checked = ycSelectedValues(component);
      all.checked = !checked.length;
      ycUpdateMultiLabel(component);
      loadYclients();
    });
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-multi-filter]")) ycCloseMenus();
  });
}

function ycParams() {
  const params = new URLSearchParams({
    date_from: document.querySelector("#dateFrom").value,
    date_to: document.querySelector("#dateTo").value,
    detail_limit: "300",
  });
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    const values = ycSelectedValues(component);
    if (!values.length) params.append(component.dataset.multiFilter, "Все");
    else values.forEach((value) => params.append(component.dataset.multiFilter, value));
  });
  return params;
}

function ycColorParams(exportAll = false) {
  const params = ycParams();
  params.delete("detail_limit");
  const visitType = document.querySelector("#colorVisitTypeFilter").value;
  const recordColor = document.querySelector("#recordColorFilter").value;
  const query = document.querySelector("#colorRecordSearch").value.trim();
  if (visitType) params.append("visit_type", visitType);
  if (recordColor) params.append("record_color", recordColor);
  if (query) params.set("q", query);
  if (!exportAll) {
    params.set("page", String(ycState.colorPage));
    params.set("per_page", document.querySelector("#colorPerPage").value);
  }
  return params;
}

function ycColorSwatch(color) {
  if (!color) return '<i class="record-color-swatch default" aria-label="По умолчанию"></i>';
  const safeColor = /^[0-9a-f]{6}$/i.test(String(color)) ? String(color) : "9e9e9e";
  return `<i class="record-color-swatch" style="--record-color:#${safeColor}" aria-hidden="true"></i>`;
}

function ycRenderColorReconciliation(data) {
  const totals = data.totals;
  document.querySelector("#colorTotalVisits").textContent = ycNumber.format(totals.visits);
  document.querySelector("#colorFirstVisits").textContent = ycNumber.format(totals.first_visits);
  document.querySelector("#colorOneOffVisits").textContent = ycNumber.format(totals.one_off_visits);
  document.querySelector("#colorRepeatVisits").textContent = ycNumber.format(totals.repeat_visits);

  const balance = document.querySelector("#colorBalanceStatus");
  const newColorText = data.new_colors.length
    ? ` Обнаружены новые цвета: ${data.new_colors.map((row) => row.color_hex).join(", ")}. Они временно считаются повторными.`
    : " Новых цветов не обнаружено.";
  balance.dataset.status = totals.is_balanced && !data.new_colors.length ? "ok" : "warning";
  balance.textContent = totals.is_balanced
    ? `${ycNumber.format(totals.first_visits)} + ${ycNumber.format(totals.one_off_visits)} + ${ycNumber.format(totals.repeat_visits)} = ${ycNumber.format(totals.visits)}. Все визиты классифицированы.${newColorText}`
    : `Контрольное равенство нарушено: классифицировано ${ycNumber.format(totals.classified_total)} из ${ycNumber.format(totals.visits)}.${newColorText}`;

  document.querySelector("#colorSummaryRows").innerHTML = data.colors.length
    ? data.colors.map((row) => `
      <tr class="${row.is_new ? "new-color-row" : ""}">
        <td><button class="color-summary-filter" type="button" data-color-filter="${row.color || "default"}">${ycColorSwatch(row.color)}<strong>${ycEscape(row.color_label)}</strong>${row.is_new ? '<small>Новый</small>' : ""}</button></td>
        <td class="number">${ycEscape(row.color_hex || "По умолчанию")}</td>
        <td><span class="type-chip">${ycEscape(row.visit_type_label)}</span></td>
        <td class="number"><strong>${ycNumber.format(row.visits)}</strong></td>
        <td class="number">${ycEscape(String(row.share).replace(".", ","))}%</td>
      </tr>`).join("")
    : '<tr><td colspan="5" class="empty-state">Состоявшихся визитов по выбранным фильтрам нет</td></tr>';

  const colorSelect = document.querySelector("#recordColorFilter");
  const selectedColor = colorSelect.value;
  colorSelect.innerHTML = '<option value="">Все цвета</option>' + data.colors.map((row) => `
    <option value="${row.color || "default"}">${ycEscape(row.color_label)}${row.color_hex ? ` · ${ycEscape(row.color_hex)}` : ""}</option>
  `).join("");
  if ([...colorSelect.options].some((option) => option.value === selectedColor)) colorSelect.value = selectedColor;

  document.querySelector("#colorRegistryRows").innerHTML = data.rows.length
    ? data.rows.map((row) => `
      <tr>
        <td class="number"><strong>${ycEscape(row.record_id)}</strong></td>
        <td>${ycFormatDate(row.visit_at)}</td>
        <td><strong>${ycEscape(row.branch)}</strong></td>
        <td><span class="direction-chip ${row.direction === "Не определено" ? "unknown" : ""}">${ycEscape(row.direction)}</span></td>
        <td><span class="record-color-cell">${ycColorSwatch(row.record_color)}<span><strong>${ycEscape(row.record_color ? `#${row.record_color.toUpperCase()}` : "По умолчанию")}</strong><small>${ycEscape(row.record_color ? "" : "Пустое поле API")}</small></span></span></td>
        <td><span class="type-chip">${ycEscape(row.visit_type_label)}</span></td>
        <td class="number">${ycEscape(row.client_id || "—")}</td>
        <td>${row.uses_subscription ? "Да" : "Нет"}</td>
        <td class="service-cell" title="${ycEscape(row.service_text)}">${ycEscape(row.service_text || "—")}</td>
      </tr>`).join("")
    : '<tr><td colspan="9" class="empty-state">Записей по внутренним фильтрам не найдено</td></tr>';

  ycState.colorPage = data.pagination.page;
  ycState.colorPages = data.pagination.pages;
  document.querySelector("#colorRegistryCount").textContent = `Найдено: ${ycNumber.format(data.pagination.total)}`;
  document.querySelector("#colorPageStatus").textContent = `Страница ${data.pagination.page} из ${data.pagination.pages}`;
  document.querySelector("#colorPreviousPage").disabled = data.pagination.page <= 1;
  document.querySelector("#colorNextPage").disabled = data.pagination.page >= data.pagination.pages;
  document.querySelector("#colorReconciliationStatus").textContent = `${ycNumber.format(totals.visits)} визитов · ${ycNumber.format(data.colors.length)} цветов`;
}

async function loadColorReconciliation() {
  const tray = document.querySelector("#colorReconciliationTray");
  if (!tray.open) return;
  if (ycState.colorController) ycState.colorController.abort();
  const controller = new AbortController();
  ycState.colorController = controller;
  document.querySelector("#colorReconciliationStatus").textContent = "Загружаем сверку…";
  try {
    const response = await fetch(`/api/yclients/colors?${ycColorParams().toString()}`, { signal: controller.signal });
    if (response.status === 401) {
      window.location.assign("/login?next=/yclients");
      return;
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не удалось получить сверку цветов");
    ycRenderColorReconciliation(data);
  } catch (error) {
    if (error.name !== "AbortError") {
      document.querySelector("#colorReconciliationStatus").textContent = "Ошибка загрузки";
      ycShowError(error.message);
    }
  } finally {
    if (ycState.colorController === controller) ycState.colorController = null;
  }
}

function ycRenderKpi(data) {
  const kpi = data.kpi;
  document.querySelector("#kpiRevenue").textContent = ycFormatMoney(kpi.revenue);
  document.querySelector("#kpiVisits").textContent = ycNumber.format(kpi.visits);
  document.querySelector("#kpiFirstVisits").textContent = ycNumber.format(kpi.first_visits);
  document.querySelector("#kpiRepeatVisits").textContent = ycNumber.format(kpi.repeat_visits);
  document.querySelector("#kpiSubscriptionVisits").textContent = ycNumber.format(kpi.subscription_visits);
  document.querySelector("#kpiSubscriptions").textContent = ycNumber.format(kpi.subscription_sales);
  document.querySelector("#kpiSubscriptionSplit").textContent = `${ycNumber.format(kpi.first_subscription_sales)} первых · ${ycNumber.format(kpi.repeat_subscription_sales)} повторных`;
  document.querySelector("#kpiSubscriptionRevenue").textContent = ycFormatMoney(kpi.subscription_revenue);
  document.querySelector("#kpiOneOffVisits").textContent = ycNumber.format(kpi.one_off_visits);
  document.querySelector("#kpiServiceRevenue").textContent = ycFormatMoney(kpi.service_revenue);
}

function ycRecordStatus(value) {
  const labels = {
    visited: "Состоялся",
    active: "Активная",
    unconfirmed: "Не подтверждена",
    no_show: "Не пришёл",
    deleted: "Удалена",
  };
  return labels[value] || value || "Состоялся";
}

function ycRenderInstallmentDebt(data) {
  const debt = data.installment_debt || {
    groups: [],
    totals: { services: 0, service_amount: 0, payments: 0, paid_amount: 0, outstanding: 0 },
    rows: [],
    row_total: 0,
  };
  const summaryRows = [
    ...debt.groups,
    { ...debt.totals, direction: "Итого", total: true },
  ];
  document.querySelector("#installmentDebtSummary").innerHTML = summaryRows.map((row) => `
    <article class="${row.total ? "total" : ""}">
      <div><span>${ycEscape(row.direction)}</span><strong>${ycFormatMoney(row.service_amount)}</strong></div>
      <dl>
        <div><dt>Услуг</dt><dd>${ycNumber.format(row.services)}</dd></div>
        <div><dt>Оплачено</dt><dd>${ycFormatMoney(row.paid_amount)}</dd></div>
        <div><dt>Разница</dt><dd>${ycFormatMoney(row.outstanding)}</dd></div>
      </dl>
    </article>
  `).join("");

  document.querySelector("#installmentDebtCount").textContent = debt.row_total
    ? `Показаны ${ycNumber.format(debt.rows.length)} из ${ycNumber.format(debt.row_total)} записей`
    : "Подходящих записей нет";
  document.querySelector("#installmentDebtRows").innerHTML = debt.rows.length
    ? debt.rows.map((row) => `
      <tr>
        <td class="number"><strong>${ycEscape(row.record_id)}</strong></td>
        <td>${ycFormatDate(row.visit_at)}</td>
        <td><strong>${ycEscape(row.branch)}</strong></td>
        <td><span class="direction-chip">${ycEscape(row.direction)}</span></td>
        <td class="service-cell" title="${ycEscape(row.service_title)}">${ycEscape(row.service_title)}</td>
        <td>${ycEscape(ycRecordStatus(row.record_status))}</td>
        <td class="number">${ycEscape(row.client_id || "—")}</td>
        <td class="money-cell">${ycFormatMoney(row.service_amount)}</td>
        <td class="money-cell revenue-cell">${ycFormatMoney(row.paid_amount)}</td>
        <td class="money-cell ${Number(row.outstanding) > 0 ? "debt-difference" : ""}">${ycFormatMoney(row.outstanding)}</td>
        <td class="number" title="${row.transaction_ids ? `ID оплат: ${ycEscape(row.transaction_ids)}` : "Платежей нет"}">${ycNumber.format(row.payment_count)}</td>
        <td>${ycFormatDate(row.last_payment_at)}</td>
        <td><span class="payment-status ${ycEscape(row.payment_status_key)}">${ycEscape(row.payment_status)}</span></td>
      </tr>
    `).join("")
    : '<tr><td colspan="13" class="empty-state">За выбранный период взносов по рассрочке не найдено</td></tr>';

  const total = debt.totals;
  document.querySelector("#installmentDebtTotal").innerHTML = `
    <tr><th colspan="7">Итого</th><th>${ycFormatMoney(total.service_amount)}</th>
    <th>${ycFormatMoney(total.paid_amount)}</th><th>${ycFormatMoney(total.outstanding)}</th>
    <th>${ycNumber.format(total.payments)}</th><th colspan="2"></th></tr>`;
}

function ycRenderBreakdown(data) {
  const body = document.querySelector("#breakdownRows");
  if (!data.groups.length) {
    body.innerHTML = '<tr><td colspan="10" class="empty-state">По выбранным фильтрам данных нет</td></tr>';
  } else {
    body.innerHTML = data.groups.map((row) => `
      <tr>
        <td><strong>${ycEscape(row.branch)}</strong></td>
        <td><span class="direction-chip ${row.direction === "Не определено" ? "unknown" : ""}">${ycEscape(row.direction)}</span></td>
        <td class="number">${ycNumber.format(row.visits)}</td>
        <td class="number">${ycNumber.format(row.first_visits)}</td>
        <td class="number">${ycNumber.format(row.one_off_visits)}</td>
        <td class="number">${ycNumber.format(row.repeat_visits)}</td>
        <td class="number">${ycNumber.format(row.subscription_visits)}</td>
        <td class="number">${ycNumber.format(row.subscription_sales)}</td>
        <td class="money-cell revenue-cell">${ycFormatMoney(row.revenue)}</td>
        <td class="money-cell">${ycFormatMoney(row.subscription_revenue)}</td>
      </tr>
    `).join("");
  }
  const kpi = data.kpi;
  document.querySelector("#breakdownTotal").innerHTML = `
    <tr><th colspan="2">Итого</th><th>${ycNumber.format(kpi.visits)}</th>
    <th>${ycNumber.format(kpi.first_visits)}</th><th>${ycNumber.format(kpi.one_off_visits)}</th>
    <th>${ycNumber.format(kpi.repeat_visits)}</th><th>${ycNumber.format(kpi.subscription_visits)}</th>
    <th>${ycNumber.format(kpi.subscription_sales)}</th>
    <th>${ycFormatMoney(kpi.revenue)}</th><th>${ycFormatMoney(kpi.subscription_revenue)}</th></tr>`;
  const badge = document.querySelector("#qualityBadge");
  if (data.quality.unknown_visits || Number(data.quality.unknown_revenue)) {
    badge.classList.add("warning");
    badge.textContent = `Не определено направление: ${ycNumber.format(data.quality.unknown_visits)} визитов · ${ycFormatMoney(data.quality.unknown_revenue)}`;
  } else {
    badge.classList.remove("warning");
    badge.textContent = "Все строки классифицированы";
  }
}

function ycRenderVisits(rows) {
  document.querySelector("#controlHead").innerHTML = `
    <tr><th>ID записи</th><th>Дата визита</th><th>Филиал</th><th>Направление</th>
    <th>ID клиента</th><th>Тип визита</th><th>Списание абонемента</th><th>Услуги</th></tr>`;
  document.querySelector("#controlRows").innerHTML = rows.length ? rows.map((row) => `
    <tr><td class="number">${ycEscape(row.record_id)}</td><td>${ycFormatDate(row.visit_at)}</td>
    <td><strong>${ycEscape(row.branch)}</strong></td><td>${ycEscape(row.direction)}</td>
    <td class="number">${ycEscape(row.client_id)}</td>
    <td><span class="type-chip" title="${row.record_color ? `Цвет: #${ycEscape(row.record_color)}` : "Цвет: по умолчанию"}">${ycEscape(row.visit_type_label)}</span></td>
    <td>${row.uses_subscription ? "Было списание" : "Не было"}</td>
    <td class="service-cell" title="${ycEscape(row.service_text)}">${ycEscape(row.service_text || "—")}</td></tr>
  `).join("") : '<tr><td colspan="8" class="empty-state">Состоявшихся визитов не найдено</td></tr>';
}

function ycRenderTransactions(rows) {
  document.querySelector("#controlHead").innerHTML = `
    <tr><th>ID операции</th><th>Дата оплаты</th><th>Филиал</th><th>Направление</th>
    <th>Тип дохода</th><th>ID документа</th><th>ID записи</th><th>Сумма</th></tr>`;
  document.querySelector("#controlRows").innerHTML = rows.length ? rows.map((row) => `
    <tr><td class="number">${ycEscape(row.transaction_id)}</td><td>${ycFormatDate(row.transaction_at)}</td>
    <td><strong>${ycEscape(row.branch)}</strong></td><td>${ycEscape(row.direction)}</td>
    <td>${ycEscape(row.expense_title)}</td><td class="number">${ycEscape(row.document_id || "—")}</td>
    <td class="number">${ycEscape(row.yclients_record_id || "—")}</td>
    <td class="money-cell revenue-cell">${ycFormatMoney(row.amount)}</td></tr>
  `).join("") : '<tr><td colspan="8" class="empty-state">Оплат не найдено</td></tr>';
}

function ycRenderControl(data) {
  const isVisits = ycState.view === "visits";
  const rows = data.details[ycState.view];
  document.querySelector("#controlNote").textContent = isVisits
    ? `Показаны последние ${ycNumber.format(rows.length)} из ${ycNumber.format(data.details.visit_total)} состоявшихся визитов.`
    : `Показаны последние ${ycNumber.format(rows.length)} из ${ycNumber.format(data.details.transaction_total)} положительных оплат.`;
  if (isVisits) ycRenderVisits(rows); else ycRenderTransactions(rows);
}

function ycRenderMethod(data) {
  document.querySelector("#methodText").textContent = [
    `${data.method.visits}; деньги — ${data.method.revenue.toLowerCase()}.`,
    `${data.method.visit_types}.`,
    `${data.method.subscription}.`,
    `${data.method.visit_payment_type}.`,
    data.method.administrator_records ? `${data.method.administrator_records}.` : "",
    `${data.method.excluded}.`,
  ].filter(Boolean).join(" ");
}

function ycRender(data) {
  ycState.data = data;
  ycRenderKpi(data);
  ycRenderInstallmentDebt(data);
  ycRenderBreakdown(data);
  ycRenderControl(data);
  ycRenderMethod(data);
  document.querySelector("#syncStatus span:last-child").textContent = data.last_sync
    ? `Данные Yclients: ${data.last_sync}`
    : "Подключено к базе Yclients";
}

async function loadYclientsFilters() {
  const response = await fetch("/api/yclients/filters");
  if (response.status === 401) {
    window.location.assign("/login?next=/yclients");
    return;
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Не удалось загрузить фильтры Yclients");
  ycFillFilter("branch", data.branch);
  ycFillFilter("direction", data.direction);
  ["dateFrom", "dateTo"].forEach((id) => {
    const input = document.querySelector(`#${id}`);
    input.min = data.min_date;
    input.max = data.max_date;
  });
}

async function loadYclients() {
  ycState.colorPage = 1;
  if (ycState.requestController) ycState.requestController.abort();
  const controller = new AbortController();
  ycState.requestController = controller;
  ycSetLoading(true);
  ycShowError();
  try {
    const response = await fetch(`/api/yclients?${ycParams().toString()}`, { signal: controller.signal });
    if (response.status === 401) {
      window.location.assign("/login?next=/yclients");
      return;
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не удалось получить витрину Yclients");
    ycRender(data);
    if (document.querySelector("#colorReconciliationTray").open) loadColorReconciliation();
    else document.querySelector("#colorReconciliationStatus").textContent = "Откройте, чтобы сверить записи";
  } catch (error) {
    if (error.name !== "AbortError") ycShowError(error.message);
  } finally {
    if (ycState.requestController === controller) {
      ycState.requestController = null;
      ycSetLoading(false);
    }
  }
}

function ycApplyPreset(name) {
  const today = ycToday();
  let start = new Date(today);
  if (name === "week") start.setDate(today.getDate() - 6);
  else if (name === "month") start = new Date(today.getFullYear(), today.getMonth(), 1);
  else if (name === "quarter") start = new Date(today.getFullYear(), Math.floor(today.getMonth() / 3) * 3, 1);
  const minDate = document.body.dataset.minDate;
  document.querySelector("#dateFrom").value = ycLocalIso(start) < minDate ? minDate : ycLocalIso(start);
  document.querySelector("#dateTo").value = ycLocalIso(today);
  document.querySelectorAll("[data-preset]").forEach((button) => button.classList.toggle("active", button.dataset.preset === name));
  loadYclients();
}

ycSetDefaultPeriod();
ycSetupFilters();
document.querySelectorAll("#dateFrom,#dateTo").forEach((input) => input.addEventListener("change", loadYclients));
document.querySelectorAll("[data-preset]").forEach((button) => button.addEventListener("click", () => ycApplyPreset(button.dataset.preset)));
document.querySelector("#resetFilters").addEventListener("click", () => {
  ycSetDefaultPeriod();
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    component.querySelectorAll('.multi-options input[type="checkbox"]').forEach((input) => { input.checked = false; });
    component.querySelector('.select-all input[value="Все"]').checked = true;
    ycUpdateMultiLabel(component);
  });
  document.querySelectorAll("[data-preset]").forEach((button) => button.classList.toggle("active", button.dataset.preset === "month"));
  document.querySelector("#colorVisitTypeFilter").value = "";
  document.querySelector("#recordColorFilter").value = "";
  document.querySelector("#colorRecordSearch").value = "";
  ycState.colorPage = 1;
  loadYclients();
});
document.querySelectorAll("[data-yc-view]").forEach((button) => button.addEventListener("click", () => {
  ycState.view = button.dataset.ycView;
  document.querySelectorAll("[data-yc-view]").forEach((item) => {
    const active = item === button;
    item.classList.toggle("active", active);
    item.setAttribute("aria-selected", String(active));
  });
  if (ycState.data) ycRenderControl(ycState.data);
}));

document.querySelector("#colorReconciliationTray").addEventListener("toggle", (event) => {
  if (event.currentTarget.open) {
    ycState.colorPage = 1;
    loadColorReconciliation();
  }
});
document.querySelectorAll("#colorVisitTypeFilter,#recordColorFilter,#colorPerPage").forEach((input) => {
  input.addEventListener("change", () => {
    ycState.colorPage = 1;
    loadColorReconciliation();
  });
});
document.querySelector("#colorRecordSearch").addEventListener("input", () => {
  window.clearTimeout(ycState.colorSearchTimer);
  ycState.colorSearchTimer = window.setTimeout(() => {
    ycState.colorPage = 1;
    loadColorReconciliation();
  }, 350);
});
document.querySelector("#colorSummaryRows").addEventListener("click", (event) => {
  const button = event.target.closest("[data-color-filter]");
  if (!button) return;
  document.querySelector("#recordColorFilter").value = button.dataset.colorFilter;
  ycState.colorPage = 1;
  loadColorReconciliation();
});
document.querySelector("#colorPreviousPage").addEventListener("click", () => {
  if (ycState.colorPage <= 1) return;
  ycState.colorPage -= 1;
  loadColorReconciliation();
});
document.querySelector("#colorNextPage").addEventListener("click", () => {
  if (ycState.colorPage >= ycState.colorPages) return;
  ycState.colorPage += 1;
  loadColorReconciliation();
});
document.querySelector("#exportColorCsv").addEventListener("click", () => {
  window.location.assign(`/api/yclients/colors/export.csv?${ycColorParams(true).toString()}`);
});

loadYclientsFilters()
  .then(loadYclients)
  .catch((error) => ycShowError(error.message));
