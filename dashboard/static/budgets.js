const budgetState = {
  sources: [], branches: [], directions: [], budgets: [], editingId: null,
};
const budgetEntryBranchesBySource = {
  "РИС_xs": ["Академическая", "Невский", "Комендантский"],
};
const budgetEntryDirectionsBySource = {
  "РИС_xs": ["Массаж"],
};
const budgetCsrf = document.body.dataset.csrfToken;
const budgetMoney = new Intl.NumberFormat("ru-RU", {
  style: "currency", currency: "RUB", maximumFractionDigits: 2,
});

const escapeBudgetHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

function budgetSourceLabel(source) {
  return source === "Флоктори" ? "FL / Флоктори" : source;
}

function budgetMessage(type, text = "") {
  const error = document.querySelector("#budgetError");
  const success = document.querySelector("#budgetSuccess");
  error.hidden = type !== "error" || !text;
  success.hidden = type !== "success" || !text;
  if (type === "error") error.textContent = text;
  if (type === "success") success.textContent = text;
}

function budgetLoading(active) {
  document.querySelector("#budgetLoading").classList.toggle("active", active);
}

function setBudgetFormSubmitting(form, active) {
  form.setAttribute("aria-busy", active ? "true" : "false");
  const submit = form.querySelector('[type="submit"]');
  if (submit) submit.disabled = active;
}

async function budgetRequest(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    window.location.assign("/login?next=/budgets");
    return null;
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Не удалось получить данные бюджета");
  return data;
}

function currentBudgetMonth() {
  return document.body.dataset.today.slice(0, 7);
}

function fillBudgetSelect(select, values, allLabel = "") {
  const current = select.value;
  select.innerHTML = `${allLabel ? `<option value="Все">${allLabel}</option>` : ""}${values
    .map((value) => `<option value="${escapeBudgetHtml(value)}">${escapeBudgetHtml(budgetSourceLabel(value))}</option>`)
    .join("")}`;
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function updateBudgetEntryOptions() {
  const source = document.querySelector('#budgetForm [name="source"]').value;
  const branches = budgetEntryBranchesBySource[source] || budgetState.branches;
  const directions = budgetEntryDirectionsBySource[source] || budgetState.directions;
  fillBudgetSelect(document.querySelector('#budgetForm [name="branch"]'), branches);
  fillBudgetSelect(document.querySelector('#budgetForm [name="direction"]'), directions);
}

function fillBudgetOptions(data) {
  budgetState.sources = data.options.sources;
  budgetState.branches = data.options.branches;
  budgetState.directions = data.options.directions;
  fillBudgetSelect(document.querySelector("#budgetFilterSource"), budgetState.sources, "Все источники");
  fillBudgetSelect(document.querySelector("#budgetFilterBranch"), budgetState.branches, "Все филиалы");
  fillBudgetSelect(document.querySelector("#budgetFilterDirection"), budgetState.directions, "Все направления");
  fillBudgetSelect(document.querySelector('#budgetForm [name="source"]'), budgetState.sources);
  updateBudgetEntryOptions();
}

function budgetParams() {
  const params = new URLSearchParams({ month: document.querySelector("#budgetViewMonth").value });
  const mapping = {
    source: "#budgetFilterSource",
    branch: "#budgetFilterBranch",
    direction: "#budgetFilterDirection",
  };
  Object.entries(mapping).forEach(([key, selector]) => {
    const value = document.querySelector(selector).value;
    if (value && value !== "Все") params.set(key, value);
  });
  return params;
}

function setBudgetTotals(totals) {
  const remainingClass = totals.remaining < 0 ? "negative" : "";
  document.querySelector("#budgetTotalPlan").textContent = budgetMoney.format(totals.budget);
  document.querySelector("#budgetTotalSpent").textContent = budgetMoney.format(totals.spent);
  document.querySelector("#budgetTotalRemaining").textContent = budgetMoney.format(totals.remaining);
  document.querySelector("#budgetTotalRemaining").className = remainingClass;
  document.querySelector("#budgetFootPlan").textContent = budgetMoney.format(totals.budget);
  document.querySelector("#budgetFootSpent").textContent = budgetMoney.format(totals.spent);
  document.querySelector("#budgetFootRemaining").textContent = budgetMoney.format(totals.remaining);
  document.querySelector("#budgetFootRemaining").className = remainingClass;
}

function renderBudgetRows(rows) {
  const body = document.querySelector("#budgetRows");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="4" class="registry-empty">Для выбранного среза пока нет бюджета или расходов</td></tr>';
    return;
  }
  body.innerHTML = rows.map((row) => `<tr>
    <td><div class="budget-scope"><strong>${escapeBudgetHtml(budgetSourceLabel(row.source))}</strong><span>${escapeBudgetHtml(row.branch)}</span><small>${escapeBudgetHtml(row.direction)}</small></div></td>
    <td class="number ${row.budget_configured ? "" : "budget-missing"}">${row.budget_configured ? budgetMoney.format(row.budget) : "Не задан"}</td>
    <td class="number">${budgetMoney.format(row.spent)}</td>
    <td class="number ${row.remaining < 0 ? "negative" : "positive"}">${budgetMoney.format(row.remaining)}</td>
  </tr>`).join("");
}

function budgetMonthLabel(value) {
  const [year, month] = String(value || "").split("-");
  return year && month ? `${month}.${year}` : "—";
}

function budgetUpdatedLabel(value) {
  if (!value) return "—";
  const [datePart, timePart = ""] = String(value).split(" ");
  const [year, month, day] = datePart.split("-");
  return `${day}.${month}.${year}${timePart ? `, ${timePart}` : ""}`;
}

function budgetActionIcon(kind) {
  if (kind === "edit") {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4l11-11-4-4L4 16v4Zm12.2-16.2 4 4 1.1-1.1a1.4 1.4 0 0 0 0-2l-2-2a1.4 1.4 0 0 0-2 0l-1.1 1.1Z"/></svg>';
  }
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-2 6h10l-1 12H8L7 9Zm3 2v7h2v-7h-2Zm4 0v7h2v-7h-2Z"/></svg>';
}

function renderBudgetHistory(rows) {
  const body = document.querySelector("#budgetHistoryRows");
  document.querySelector("#budgetHistoryCount").textContent = `${rows.length} записей`;
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="registry-empty">За выбранный месяц бюджеты ещё не внесены</td></tr>';
    return;
  }
  body.innerHTML = rows.map((row) => `<tr>
    <td><strong>${escapeBudgetHtml(budgetSourceLabel(row.source))}</strong></td>
    <td>${escapeBudgetHtml(row.branch)}</td>
    <td>${escapeBudgetHtml(row.direction)}</td>
    <td class="date-cell">${budgetMonthLabel(row.budget_month)}</td>
    <td class="number"><strong>${budgetMoney.format(row.amount)}</strong></td>
    <td class="date-cell">${budgetUpdatedLabel(row.updated_at)}</td>
    <td><div class="budget-history-actions">
      <button type="button" data-budget-edit="${row.id}" title="Редактировать" aria-label="Редактировать бюджет">${budgetActionIcon("edit")}</button>
      <button class="danger" type="button" data-budget-delete="${row.id}" title="Удалить" aria-label="Удалить бюджет">${budgetActionIcon("delete")}</button>
    </div></td>
  </tr>`).join("");
}

async function loadBudgets() {
  budgetLoading(true);
  budgetMessage("error");
  try {
    const data = await budgetRequest(`/api/budgets?${budgetParams().toString()}`);
    if (!data) return;
    fillBudgetOptions(data);
    budgetState.budgets = data.budgets || [];
    renderBudgetRows(data.rows);
    renderBudgetHistory(budgetState.budgets);
    setBudgetTotals(data.totals);
    document.querySelector("#budgetMonthLabel").textContent = data.month_label;
    document.querySelector("#syncStatus span:last-child").textContent = data.last_sync
      ? `Данные в БД: ${data.last_sync}` : "Подключено к базе amoCRM";
    if (data.schema_ready === false) {
      budgetMessage("error", "Хранилище бюджетов ещё не создано. Ввод будет доступен после инициализации таблицы.");
    }
  } catch (error) {
    budgetMessage("error", error.message);
  } finally {
    budgetLoading(false);
  }
}

async function saveBudget(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setBudgetFormSubmitting(form, true);
  budgetLoading(true);
  budgetMessage("error");
  const payload = Object.fromEntries(new FormData(form).entries());
  try {
    const editing = budgetState.editingId !== null;
    const endpoint = editing ? `/api/budgets/${budgetState.editingId}` : "/api/budgets";
    const result = await budgetRequest(endpoint, {
      method: editing ? "PUT" : "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": budgetCsrf },
      body: JSON.stringify(payload),
    });
    if (!result) return;
    document.querySelector("#budgetViewMonth").value = payload.budget_month;
    cancelBudgetEdit();
    await loadBudgets();
    budgetMessage("success", editing
      ? "Изменения бюджета сохранены. Предыдущее значение осталось в журнале."
      : "Бюджет сохранён. Если план уже существовал, его сумма обновлена.");
  } catch (error) {
    budgetMessage("error", error.message);
  } finally {
    setBudgetFormSubmitting(form, false);
    budgetLoading(false);
  }
}

function startBudgetEdit(budgetId) {
  const row = budgetState.budgets.find((item) => Number(item.id) === Number(budgetId));
  if (!row) return;
  const form = document.querySelector("#budgetForm");
  budgetState.editingId = Number(row.id);
  form.querySelector('[name="source"]').value = row.source;
  updateBudgetEntryOptions();
  form.querySelector('[name="branch"]').value = row.branch;
  form.querySelector('[name="direction"]').value = row.direction;
  form.querySelector('[name="budget_month"]').value = row.budget_month;
  form.querySelector('[name="amount"]').value = row.amount;
  document.querySelector("#budgetEntryTitle").textContent = "Редактировать бюджет";
  document.querySelector("#budgetEntryHelp").textContent = "Измените нужные поля и сохраните. Предыдущее значение останется в журнале аудита.";
  document.querySelector("#budgetSubmitButton").textContent = "Сохранить изменения";
  document.querySelector("#budgetCancelEdit").hidden = false;
  form.classList.add("editing");
  form.scrollIntoView({ behavior: "smooth", block: "center" });
}

function cancelBudgetEdit() {
  const form = document.querySelector("#budgetForm");
  budgetState.editingId = null;
  form.reset();
  updateBudgetEntryOptions();
  form.querySelector('[name="budget_month"]').value = document.querySelector("#budgetViewMonth").value;
  document.querySelector("#budgetEntryTitle").textContent = "Задать бюджет на месяц";
  document.querySelector("#budgetEntryHelp").textContent = "Если сохранить ту же связку повторно, сумма бюджета обновится, а изменение останется в истории.";
  document.querySelector("#budgetSubmitButton").textContent = "Сохранить бюджет";
  document.querySelector("#budgetCancelEdit").hidden = true;
  form.classList.remove("editing");
}

async function deleteBudget(budgetId) {
  const row = budgetState.budgets.find((item) => Number(item.id) === Number(budgetId));
  if (!row) return;
  const description = `${budgetSourceLabel(row.source)} · ${row.branch} · ${row.direction} · ${budgetMonthLabel(row.budget_month)}`;
  if (!window.confirm(`Удалить бюджет «${description}»? Запись исчезнет из расчёта, но останется в журнале аудита.`)) return;
  budgetLoading(true);
  budgetMessage("error");
  try {
    const result = await budgetRequest(`/api/budgets/${row.id}`, {
      method: "DELETE",
      headers: { "X-CSRF-Token": budgetCsrf },
    });
    if (!result) return;
    if (budgetState.editingId === Number(row.id)) cancelBudgetEdit();
    await loadBudgets();
    budgetMessage("success", "Бюджет удалён из расчёта. Снимок записи сохранён в журнале аудита.");
  } catch (error) {
    budgetMessage("error", error.message);
  } finally {
    budgetLoading(false);
  }
}

function resetBudgetFilters() {
  document.querySelector("#budgetViewMonth").value = currentBudgetMonth();
  document.querySelector("#budgetFilterSource").value = "Все";
  document.querySelector("#budgetFilterBranch").value = "Все";
  document.querySelector("#budgetFilterDirection").value = "Все";
  document.querySelector('#budgetForm [name="budget_month"]').value = currentBudgetMonth();
  loadBudgets();
}

function initBudgets() {
  const month = currentBudgetMonth() < "2026-07" ? "2026-07" : currentBudgetMonth();
  document.querySelector("#budgetViewMonth").value = month;
  document.querySelector('#budgetForm [name="budget_month"]').value = month;
  document.querySelectorAll("#budgetViewMonth, #budgetFilterSource, #budgetFilterBranch, #budgetFilterDirection")
    .forEach((field) => field.addEventListener("change", () => {
      if (field.id === "budgetViewMonth") {
        document.querySelector('#budgetForm [name="budget_month"]').value = field.value;
      }
      loadBudgets();
    }));
  document.querySelector("#resetBudgetFilters").addEventListener("click", resetBudgetFilters);
  document.querySelector("#budgetCancelEdit").addEventListener("click", cancelBudgetEdit);
  document.querySelector('#budgetForm [name="source"]').addEventListener("change", updateBudgetEntryOptions);
  document.querySelector("#budgetForm").addEventListener("submit", saveBudget);
  document.querySelector("#budgetHistoryRows").addEventListener("click", (event) => {
    const edit = event.target.closest("[data-budget-edit]");
    const remove = event.target.closest("[data-budget-delete]");
    if (edit) startBudgetEdit(edit.dataset.budgetEdit);
    if (remove) deleteBudget(remove.dataset.budgetDelete);
  });
  loadBudgets();
}

document.addEventListener("DOMContentLoaded", initBudgets);
