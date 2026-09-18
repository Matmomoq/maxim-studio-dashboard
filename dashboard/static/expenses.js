const expenseState = {
  month: "",
  monthFrom: "",
  monthTo: "",
  branches: [],
  directions: [],
  rates: [],
  expenses: [],
  filterMonth: "",
  reconciliationLoaded: false,
};

const expenseCsrf = document.body.dataset.csrfToken;
const moneyFormatter = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 2,
});

const rateDirectionsBySource = {
  "РИС": ["Массаж", "Лазер"],
  "Флоктори": ["Массаж", "Лазер"],
  "РИС_xs": ["Массаж"],
  "Сайт Xsize": ["Массаж"],
  "SMM_xs": ["Массаж"],
};

const escapeExpenseHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

function expenseDate(value) {
  if (!value) return "по настоящее время";
  const [year, month, day] = value.split("-");
  return `${day}.${month}.${year}`;
}

function setExpenseLoading(active) {
  document.querySelector("#loadingLine").classList.toggle("active", active);
}

function setExpenseFormSubmitting(form, active) {
  form.setAttribute("aria-busy", active ? "true" : "false");
  const submit = form.querySelector('[type="submit"]');
  if (submit) submit.disabled = active;
}

function showExpenseMessage(type, message = "") {
  const error = document.querySelector("#errorBanner");
  const success = document.querySelector("#successBanner");
  error.hidden = type !== "error" || !message;
  success.hidden = type !== "success" || !message;
  if (type === "error") error.textContent = message;
  if (type === "success") success.textContent = message;
}

async function expenseRequest(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    window.location.assign("/login?next=/expenses");
    return null;
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Не удалось выполнить действие");
  return data;
}

function jsonOptions(payload) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": expenseCsrf },
    body: JSON.stringify(payload),
  };
}

function currentMonthValue() {
  return document.body.dataset.today.slice(0, 7);
}

function rateFor(source, direction) {
  return expenseState.rates.find((rate) => (
    rate.source === source
    && rate.direction === direction
    && rate.valid_from <= expenseState.monthTo
    && (!rate.valid_to || rate.valid_to >= expenseState.monthFrom)
  ));
}

function displayRateSource(source) {
  return source === "Флоктори" ? "FL / Флоктори" : source;
}

function renderRateCards() {
  const combinations = [
    ["РИС", "Массаж"], ["РИС", "Лазер"],
    ["Флоктори", "Массаж"], ["Флоктори", "Лазер"],
    ["РИС_xs", "Массаж"], ["Сайт Xsize", "Массаж"],
    ["SMM_xs", "Массаж"], null,
  ];
  document.querySelector("#rateCards").innerHTML = combinations.map((combination) => {
    if (!combination) {
      return '<article class="rate-card rate-card-empty" aria-hidden="true"></article>';
    }
    const [source, direction] = combination;
    const rate = rateFor(source, direction);
    const displaySource = displayRateSource(source);
    return `<article class="rate-card ${rate ? "" : "missing"}">
      <span>${escapeExpenseHtml(displaySource)} · ${escapeExpenseHtml(direction)}</span>
      <strong>${rate ? moneyFormatter.format(rate.cost_per_lead) : "Не задан"}</strong>
      <small>${rate ? `с ${expenseDate(rate.valid_from)}` : "Добавьте первый тариф"}</small>
    </article>`;
  }).join("");
}

function updateRateDirections() {
  const source = document.querySelector('#rateForm [name="source"]').value;
  const select = document.querySelector('#rateForm [name="direction"]');
  const current = select.value;
  const directions = rateDirectionsBySource[source] || [];
  select.innerHTML = directions
    .map((direction) => `<option value="${escapeExpenseHtml(direction)}">${escapeExpenseHtml(direction)}</option>`)
    .join("");
  if (directions.includes(current)) select.value = current;
}

function renderRateHistory() {
  const body = document.querySelector("#rateHistoryBody");
  if (!expenseState.rates.length) {
    body.innerHTML = '<tr><td colspan="6" class="registry-empty">История тарифов пока пуста</td></tr>';
    return;
  }
  body.innerHTML = expenseState.rates.map((rate) => `<tr>
    <td><strong>${escapeExpenseHtml(displayRateSource(rate.source))}</strong></td>
    <td>${escapeExpenseHtml(rate.direction)}</td>
    <td class="number">${moneyFormatter.format(rate.cost_per_lead)}</td>
    <td class="date-cell">${expenseDate(rate.valid_from)}</td>
    <td class="date-cell">${expenseDate(rate.valid_to)}</td>
    <td>${escapeExpenseHtml(rate.comment || "—")}</td>
  </tr>`).join("");
}

function expenseScope(row) {
  if (row.source === "Яндекс.Карты") return "Вся сеть · Все направления";
  return `${row.branch} · ${row.direction}`;
}

function actionButtons(row) {
  if (row.status === "cancelled") return "—";
  const confirm = row.status === "draft"
    ? `<button type="button" data-expense-action="confirmed" data-expense-id="${row.id}">Подтвердить</button>`
    : "";
  const cancel = `<button class="danger" type="button" data-expense-action="cancelled" data-expense-id="${row.id}">Отменить</button>`;
  return `${confirm}${cancel}`;
}

function filteredPeriodExpenses() {
  const branch = document.querySelector("#expenseListBranch").value;
  const direction = document.querySelector("#expenseListDirection").value;
  const dateFrom = document.querySelector("#expenseListDateFrom").value;
  const dateTo = document.querySelector("#expenseListDateTo").value;
  return expenseState.expenses.filter((row) => (
    (!branch || !row.branch || row.branch === branch)
    && (!direction || !row.direction || row.direction === direction)
    && (!dateFrom || row.period_to >= dateFrom)
    && (!dateTo || row.period_from <= dateTo)
  ));
}

function renderPeriodExpenses() {
  const body = document.querySelector("#periodExpensesBody");
  const rows = filteredPeriodExpenses();
  document.querySelector("#expenseListCount").textContent = rows.length === expenseState.expenses.length
    ? `${rows.length} записей`
    : `${rows.length} из ${expenseState.expenses.length} записей`;
  if (!rows.length) {
    const message = expenseState.expenses.length
      ? "По выбранным фильтрам расходов нет"
      : "За выбранный месяц расходов ещё нет";
    body.innerHTML = `<tr><td colspan="8" class="registry-empty">${message}</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => {
    const preview = row.preview || {};
    const rawLeads = preview.raw_leads ?? preview.matched_leads;
    const leadDetails = preview.excluded_leads
      ? `<small>${preview.matched_leads} в аналитике · ${preview.excluded_leads} исключено</small>`
      : "";
    return `<tr class="expense-status-${escapeExpenseHtml(row.status)}">
      <td><strong>${escapeExpenseHtml(row.source)}</strong></td>
      <td>${escapeExpenseHtml(expenseScope(row))}</td>
      <td class="date-cell">${expenseDate(row.period_from)} — ${expenseDate(row.period_to)}</td>
      <td class="number">${moneyFormatter.format(row.amount)}</td>
      <td class="number reconciliation-number"><strong>${rawLeads ?? "—"}</strong>${leadDetails}</td>
      <td class="number">${preview.calculated_cpl == null ? "—" : moneyFormatter.format(preview.calculated_cpl)}</td>
      <td><span class="expense-status ${escapeExpenseHtml(row.status)}">${escapeExpenseHtml(row.status_label)}</span></td>
      <td><div class="expense-row-actions">${actionButtons(row)}</div></td>
    </tr>`;
  }).join("");
}

function fillExpenseBranches() {
  document.querySelector("#periodBranch").innerHTML = expenseState.branches
    .map((branch) => `<option value="${escapeExpenseHtml(branch)}">${escapeExpenseHtml(branch)}</option>`)
    .join("");
}

function fillExpenseListFilters(data) {
  const branchSelect = document.querySelector("#expenseListBranch");
  const directionSelect = document.querySelector("#expenseListDirection");
  const sameMonth = expenseState.filterMonth === data.month;
  const selectedBranch = sameMonth ? branchSelect.value : "";
  const selectedDirection = sameMonth ? directionSelect.value : "";
  branchSelect.innerHTML = ['<option value="">Все филиалы</option>']
    .concat(data.branches.map((branch) => `<option value="${escapeExpenseHtml(branch)}">${escapeExpenseHtml(branch)}</option>`))
    .join("");
  directionSelect.innerHTML = ['<option value="">Все направления</option>']
    .concat(data.directions.map((direction) => `<option value="${escapeExpenseHtml(direction)}">${escapeExpenseHtml(direction)}</option>`))
    .join("");
  if ([...branchSelect.options].some((option) => option.value === selectedBranch)) {
    branchSelect.value = selectedBranch;
  }
  if ([...directionSelect.options].some((option) => option.value === selectedDirection)) {
    directionSelect.value = selectedDirection;
  }

  const dateFrom = document.querySelector("#expenseListDateFrom");
  const dateTo = document.querySelector("#expenseListDateTo");
  dateFrom.min = data.month_from;
  dateFrom.max = data.month_to;
  dateTo.min = data.month_from;
  dateTo.max = data.month_to;
  if (!sameMonth) {
    dateFrom.value = data.month_from;
    dateTo.value = data.month_to;
  }
  expenseState.filterMonth = data.month;
  updateExpenseListDateLimits();
}

function updateExpenseListDateLimits(changedField = "") {
  const from = document.querySelector("#expenseListDateFrom");
  const to = document.querySelector("#expenseListDateTo");
  to.min = from.value || expenseState.monthFrom;
  if (from.value && to.value && to.value < from.value) {
    if (changedField === "to") from.value = to.value;
    else to.value = from.value;
  }
  renderPeriodExpenses();
}

function resetExpenseListFilters() {
  document.querySelector("#expenseListBranch").value = "";
  document.querySelector("#expenseListDirection").value = "";
  document.querySelector("#expenseListDateFrom").value = expenseState.monthFrom;
  document.querySelector("#expenseListDateTo").value = expenseState.monthTo;
  updateExpenseListDateLimits();
}

function renderExpenseData(data) {
  expenseState.month = data.month;
  expenseState.monthFrom = data.month_from;
  expenseState.monthTo = data.month_to;
  expenseState.branches = data.branches;
  expenseState.directions = data.directions;
  expenseState.rates = data.rates;
  expenseState.expenses = data.expenses;
  fillExpenseBranches();
  fillExpenseListFilters(data);
  renderRateCards();
  renderRateHistory();
  document.querySelector("#selectedPeriodLabel").textContent = `В списке: ${expenseDate(data.month_from)} — ${expenseDate(data.month_to)}`;
  document.querySelector("#syncStatus span:last-child").textContent = data.last_sync
    ? `Данные в БД: ${data.last_sync}`
    : "Подключено к базе amoCRM";
  document.querySelector('#rateForm [name="valid_from"]').value = data.month_from;
  document.querySelector("#periodFrom").value = data.month_from;
  document.querySelector("#periodTo").value = data.month_to;
  updatePeriodDateLimits();
  const reconciliationFrom = document.querySelector("#reconciliationDateFrom");
  const reconciliationTo = document.querySelector("#reconciliationDateTo");
  reconciliationFrom.min = data.month_from;
  reconciliationTo.min = data.month_from;
  if (!expenseState.reconciliationLoaded || reconciliationFrom.value.slice(0, 7) !== data.month) {
    reconciliationFrom.value = data.month_from;
    reconciliationTo.value = data.month_to;
    expenseState.reconciliationLoaded = false;
    document.querySelector("#reconciliationSummary").textContent = "Открыть таблицу";
    document.querySelector("#reconciliationCount").textContent = "Данные ещё не загружены";
  }
  if (data.schema_ready === false) {
    showExpenseMessage(
      "error",
      "Форма доступна для проверки, но сохранение ещё не включено: сначала нужно создать таблицы расходов в базе.",
    );
  }
}

async function loadExpenseData() {
  setExpenseLoading(true);
  showExpenseMessage("error");
  try {
    const data = await expenseRequest(`/api/expenses?month=${encodeURIComponent(document.querySelector("#expenseMonth").value)}`);
    if (data) renderExpenseData(data);
  } catch (error) {
    showExpenseMessage("error", error.message);
  } finally {
    setExpenseLoading(false);
  }
}

function periodPayload() {
  const form = new FormData(document.querySelector("#periodExpenseForm"));
  const source = form.get("source");
  return {
    source,
    branch: source === "VK" ? form.get("branch") : null,
    direction: source === "VK" ? form.get("direction") : null,
    amount: form.get("amount"),
    status: form.get("status"),
    comment: "",
    period_from: form.get("period_from"),
    period_to: form.get("period_to"),
  };
}

function updatePeriodDateLimits(changedField = "") {
  const from = document.querySelector("#periodFrom");
  const to = document.querySelector("#periodTo");
  to.min = from.value || document.body.dataset.minDate || "2026-07-01";
  if (from.value && to.value && to.value < from.value) {
    if (changedField === "to") from.value = to.value;
    else to.value = from.value;
  }
  document.querySelector("#expensePreview").innerHTML = "<span>Проверьте расчёт для выбранных дат</span>";
}

function updatePeriodScope() {
  const isVk = document.querySelector("#periodSource").value === "VK";
  document.querySelectorAll(".vk-scope").forEach((field) => { field.hidden = !isVk; });
  document.querySelector("#periodBranch").required = isVk;
  document.querySelector("#periodDirection").required = isVk;
  document.querySelector("#expensePreview").innerHTML = "<span>Проверьте расчёт после заполнения суммы</span>";
}

async function previewPeriodExpense() {
  setExpenseLoading(true);
  showExpenseMessage("error");
  try {
    const data = await expenseRequest("/api/expenses/preview", jsonOptions(periodPayload()));
    if (!data) return;
    document.querySelector("#expensePreview").innerHTML = data.unallocated
      ? `<strong>По тегам найдено: ${data.raw_leads || 0}</strong><span>${data.excluded_leads || 0} исключено · ${moneyFormatter.format(data.amount)} останутся нераспределёнными</span>`
      : `<strong>По тегам: ${data.raw_leads} · в аналитике: ${data.matched_leads} · ${moneyFormatter.format(data.calculated_cpl)} за лид</strong><span>${data.excluded_leads} исключено · филиал записи не используется</span>`;
  } catch (error) {
    showExpenseMessage("error", error.message);
  } finally {
    setExpenseLoading(false);
  }
}

function reconciliationParams() {
  const params = new URLSearchParams({
    date_from: document.querySelector("#reconciliationDateFrom").value,
    date_to: document.querySelector("#reconciliationDateTo").value,
  });
  ["Source", "Branch", "Direction"].forEach((suffix) => {
    const value = document.querySelector(`#reconciliation${suffix}`).value;
    if (value) params.set(suffix.toLowerCase(), value);
  });
  return params;
}

function fillReconciliationSelect(id, values, allLabel) {
  const select = document.querySelector(id);
  const current = select.value;
  select.innerHTML = [`<option value="">${allLabel}</option>`]
    .concat(values.map((value) => `<option value="${escapeExpenseHtml(value)}">${escapeExpenseHtml(value)}</option>`))
    .join("");
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function reconciliationDifference(value) {
  if (!value) return '<span class="difference-zero">0</span>';
  const sign = value > 0 ? "+" : "";
  const className = value > 0 ? "difference-positive" : "difference-negative";
  return `<span class="${className}">${sign}${value}</span>`;
}

function reconciliationDeals(deals) {
  if (!deals.length) return '<span class="tag-empty">—</span>';
  const links = deals.map((deal) => {
    const reason = deal.excluded_reason ? ` · ${escapeExpenseHtml(deal.excluded_reason)}` : "";
    const label = `${deal.lead_id}${reason}`;
    return deal.lead_url
      ? `<a href="${escapeExpenseHtml(deal.lead_url)}" target="_blank" rel="noopener">${label}</a>`
      : `<span>${label}</span>`;
  }).join("");
  return `<details class="reconciliation-deals"><summary>${deals.length} шт.</summary><div>${links}</div></details>`;
}

function renderReconciliation(data) {
  fillReconciliationSelect("#reconciliationSource", data.options.sources, "Все источники");
  fillReconciliationSelect("#reconciliationBranch", data.options.branches, "Все филиалы");
  fillReconciliationSelect("#reconciliationDirection", data.options.directions, "Все направления");
  const body = document.querySelector("#reconciliationRows");
  if (!data.rows.length) {
    body.innerHTML = '<tr><td colspan="9" class="registry-empty">По выбранным тегам лидов не найдено</td></tr>';
  } else {
    body.innerHTML = data.rows.map((row) => `<tr>
      <td><strong>${escapeExpenseHtml(row.source)}</strong></td>
      <td>${escapeExpenseHtml(row.branch)}</td>
      <td>${escapeExpenseHtml(row.direction)}</td>
      <td class="number">${row.tag_leads}</td>
      <td class="number excluded-number">${row.excluded_leads}</td>
      <td class="number">${row.analytics_leads}</td>
      <td class="number">${row.management_leads}</td>
      <td class="number">${reconciliationDifference(row.difference)}</td>
      <td>${reconciliationDeals(row.deals)}</td>
    </tr>`).join("");
  }
  const totals = data.totals;
  document.querySelector("#reconciliationTotals").innerHTML = `<tr>
    <th colspan="3">ИТОГО</th><th class="number">${totals.tag_leads}</th>
    <th class="number">${totals.excluded_leads}</th><th class="number">${totals.analytics_leads}</th>
    <th class="number">${totals.management_leads}</th><th class="number">${reconciliationDifference(totals.difference)}</th><th></th>
  </tr>`;
  document.querySelector("#reconciliationCount").textContent = `${data.rows.length} срезов · ${totals.tag_leads} лидов по тегам`;
  document.querySelector("#reconciliationSummary").textContent = `${totals.tag_leads} лидов по тегам`;
  document.querySelector("#reconciliationExport").href = `/api/expenses/reconciliation.csv?${reconciliationParams()}`;
  expenseState.reconciliationLoaded = true;
}

async function loadMarketingReconciliation() {
  const dateFrom = document.querySelector("#reconciliationDateFrom").value;
  const dateTo = document.querySelector("#reconciliationDateTo").value;
  if (!dateFrom || !dateTo) return;
  if (dateTo < dateFrom) {
    showExpenseMessage("error", "Дата сверки «по» не может быть раньше даты «с»");
    return;
  }
  setExpenseLoading(true);
  showExpenseMessage("error");
  try {
    const data = await expenseRequest(`/api/expenses/reconciliation?${reconciliationParams()}`);
    if (data) renderReconciliation(data);
  } catch (error) {
    showExpenseMessage("error", error.message);
  } finally {
    setExpenseLoading(false);
  }
}

async function submitRate(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  setExpenseFormSubmitting(formElement, true);
  setExpenseLoading(true);
  showExpenseMessage("error");
  try {
    const result = await expenseRequest("/api/expenses/rates", jsonOptions(Object.fromEntries(form.entries())));
    if (!result) return;
    formElement.reset();
    updateRateDirections();
    await loadExpenseData();
    showExpenseMessage("success", "Новый тариф сохранён. Предыдущая цена осталась в истории.");
  } catch (error) {
    showExpenseMessage("error", error.message);
  } finally {
    setExpenseFormSubmitting(formElement, false);
    setExpenseLoading(false);
  }
}

async function submitPeriodExpense(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const payload = periodPayload();
  setExpenseFormSubmitting(formElement, true);
  setExpenseLoading(true);
  showExpenseMessage("error");
  try {
    const result = await expenseRequest("/api/expenses/periods", jsonOptions(payload));
    if (!result) return;
    formElement.reset();
    updatePeriodScope();
    document.querySelector("#expenseMonth").value = payload.period_from.slice(0, 7);
    await loadExpenseData();
    document.querySelector("#periodFrom").value = payload.period_from;
    document.querySelector("#periodTo").value = payload.period_to;
    updatePeriodDateLimits();
    showExpenseMessage("success", "Расход сохранён и добавлен в историю.");
  } catch (error) {
    showExpenseMessage("error", error.message);
  } finally {
    setExpenseFormSubmitting(formElement, false);
    setExpenseLoading(false);
  }
}

async function changeExpenseStatus(button) {
  const action = button.dataset.expenseAction;
  if (action === "cancelled" && !window.confirm("Отменить этот расход? Запись останется в истории.")) return;
  button.disabled = true;
  setExpenseLoading(true);
  showExpenseMessage("error");
  try {
    const result = await expenseRequest(`/api/expenses/periods/${button.dataset.expenseId}/${action}`, jsonOptions({}));
    if (!result) return;
    await loadExpenseData();
    showExpenseMessage("success", action === "confirmed" ? "Расход подтверждён." : "Расход отменён, история сохранена.");
  } catch (error) {
    showExpenseMessage("error", error.message);
  } finally {
    button.disabled = false;
    setExpenseLoading(false);
  }
}

function initExpenses() {
  const monthInput = document.querySelector("#expenseMonth");
  monthInput.value = currentMonthValue() < monthInput.min ? monthInput.min : currentMonthValue();
  monthInput.addEventListener("change", loadExpenseData);
  document.querySelector("#periodSource").addEventListener("change", updatePeriodScope);
  document.querySelector("#periodFrom").addEventListener("change", () => updatePeriodDateLimits("from"));
  document.querySelector("#periodTo").addEventListener("change", () => updatePeriodDateLimits("to"));
  document.querySelector("#expenseListBranch").addEventListener("change", renderPeriodExpenses);
  document.querySelector("#expenseListDirection").addEventListener("change", renderPeriodExpenses);
  document.querySelector("#expenseListDateFrom").addEventListener("change", () => updateExpenseListDateLimits("from"));
  document.querySelector("#expenseListDateTo").addEventListener("change", () => updateExpenseListDateLimits("to"));
  document.querySelector("#expenseListReset").addEventListener("click", resetExpenseListFilters);
  document.querySelector('#rateForm [name="source"]').addEventListener("change", updateRateDirections);
  document.querySelector("#previewExpense").addEventListener("click", previewPeriodExpense);
  document.querySelector("#rateForm").addEventListener("submit", submitRate);
  document.querySelector("#periodExpenseForm").addEventListener("submit", submitPeriodExpense);
  document.querySelector("#periodExpensesBody").addEventListener("click", (event) => {
    const button = event.target.closest("[data-expense-action]");
    if (button) changeExpenseStatus(button);
  });
  document.querySelector("#marketingReconciliationTray").addEventListener("toggle", (event) => {
    if (event.currentTarget.open && !expenseState.reconciliationLoaded) loadMarketingReconciliation();
  });
  document.querySelector("#loadReconciliation").addEventListener("click", loadMarketingReconciliation);
  document.querySelector("#reconciliationDateFrom").addEventListener("change", () => {
    const from = document.querySelector("#reconciliationDateFrom");
    const to = document.querySelector("#reconciliationDateTo");
    to.min = from.value || document.body.dataset.minDate;
    if (from.value && to.value && to.value < from.value) to.value = from.value;
  });
  updatePeriodScope();
  updateRateDirections();
  loadExpenseData();
}

document.addEventListener("DOMContentLoaded", initExpenses);
