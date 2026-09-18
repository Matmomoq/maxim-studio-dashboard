const planState = { metrics: [], plans: [], editingId: null, schemaReady: true };
const planCsrf = document.body.dataset.csrfToken;
const planMoney = new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 });
const planNumber = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });

const planEscape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");

function planMessage(type, value = "") {
  const error = document.querySelector("#planError");
  const success = document.querySelector("#planSuccess");
  error.hidden = type !== "error" || !value;
  success.hidden = type !== "success" || !value;
  if (type === "error") error.textContent = value;
  if (type === "success") success.textContent = value;
}

function planSetLoading(active) {
  document.querySelector("#planLoading").classList.toggle("active", active);
}

async function planRequest(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    window.location.assign("/login?next=/plans");
    return null;
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Не удалось получить план");
  return data;
}

function currentPlanMonth() {
  const today = document.body.dataset.today;
  return today < "2026-08-01" ? "2026-08" : today.slice(0, 7);
}

function fillPlanSelect(select, values) {
  select.innerHTML = values.map((value) => `<option value="${planEscape(value)}">${planEscape(value)}</option>`).join("");
}

function inputStep(metric) {
  if (metric.unit === "count") return "1";
  if (metric.unit === "percent") return "0.01";
  return "0.01";
}

function metricSuffix(metric) {
  if (metric.unit === "money") return "₽";
  if (metric.unit === "percent") return "%";
  return "";
}

function renderPlanFields() {
  const sections = new Map();
  planState.metrics.forEach((metric) => {
    if (!sections.has(metric.section)) sections.set(metric.section, []);
    sections.get(metric.section).push(metric);
  });
  document.querySelector("#planMetricSections").innerHTML = [...sections.entries()].map(([section, metrics]) => `
    <fieldset class="plan-metric-section">
      <legend>${planEscape(section)}</legend>
      <div class="plan-metric-grid">
        ${metrics.map((metric) => `
          <label class="plan-metric-field ${metric.source === "calculated" ? "calculated" : "manual"}">
            <span>${planEscape(metric.label)}</span>
            <span class="plan-value-control"><input data-plan-metric="${metric.code}" type="number" min="0" ${metric.unit === "percent" ? 'max="100"' : ""} step="${inputStep(metric)}" inputmode="decimal" ${metric.source === "manual" ? "required" : "readonly"}><i>${metricSuffix(metric)}</i></span>
            <small>${metric.source === "calculated" ? planEscape(metric.formula) : "Вводится вручную"}</small>
          </label>`).join("")}
      </div>
    </fieldset>`).join("");
  document.querySelectorAll('[data-plan-metric]').forEach((input) => input.addEventListener("input", calculatePlanPreview));
}

function planValue(code) {
  const input = document.querySelector(`[data-plan-metric="${code}"]`);
  return Number(String(input?.value || "0").replace(",", ".")) || 0;
}

function setCalculated(code, value) {
  const input = document.querySelector(`[data-plan-metric="${code}"]`);
  const metric = planState.metrics.find((item) => item.code === code);
  if (input) input.value = formatPlanInputValue(metric, value);
}

function formatPlanInputValue(metric, value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  if (metric?.unit === "count") return String(Math.round(number));
  return String(Math.round((number + Number.EPSILON) * 100) / 100);
}

function roundCount(value) {
  return Math.floor(Number(value || 0) + 0.5);
}

function calculatePlanPreview() {
  const bookings = roundCount(planValue("leads") * planValue("lead_to_booking_cr") / 100);
  const primaryVisits = roundCount(bookings * planValue("booking_to_visit_cr") / 100);
  setCalculated("bookings", bookings);
  setCalculated("primary_visits", primaryVisits);
  setCalculated("lead_to_visit_cr", planValue("leads") ? primaryVisits / planValue("leads") * 100 : 0);
  setCalculated("primary_visit_sum", primaryVisits * planValue("primary_avg_check"));
  const primarySales = roundCount(primaryVisits * planValue("primary_subscription_cr") / 100);
  setCalculated("primary_subscription_count", primarySales);
  setCalculated("primary_subscription_sum", primarySales * planValue("primary_subscription_initial_avg"));
  const repeatSales = roundCount(planValue("repeat_visits") * planValue("repeat_subscription_cr") / 100);
  setCalculated("repeat_subscription_count", repeatSales);
  setCalculated("repeat_subscription_sum", repeatSales * planValue("repeat_subscription_initial_avg"));
  setCalculated("one_off_sum", planValue("one_off_count") * planValue("one_off_avg_check"));
}

function payloadFromPlanForm() {
  const form = document.querySelector("#planForm");
  const values = {};
  planState.metrics.filter((metric) => metric.source === "manual").forEach((metric) => {
    values[metric.code] = form.querySelector(`[data-plan-metric="${metric.code}"]`).value;
  });
  return {
    plan_month: form.elements.plan_month.value,
    branch: form.elements.branch.value,
    direction: form.elements.direction.value,
    values,
  };
}

function formatPlanMetric(code, value) {
  const metric = planState.metrics.find((item) => item.code === code);
  if (metric?.unit === "money") return planMoney.format(Number(value || 0));
  if (metric?.unit === "percent") return `${planNumber.format(Number(value || 0))}%`;
  return planNumber.format(Number(value || 0));
}

function renderPlanHistory() {
  document.querySelector("#planCount").textContent = `${planState.plans.length} записей`;
  const body = document.querySelector("#planHistoryRows");
  if (!planState.plans.length) {
    body.innerHTML = '<tr><td colspan="7" class="registry-empty">За выбранный месяц планов ещё нет</td></tr>';
    return;
  }
  body.innerHTML = planState.plans.map((plan) => `<tr>
    <td><strong>${planEscape(plan.branch)}</strong></td><td>${planEscape(plan.direction)}</td>
    <td class="number">${formatPlanMetric("revenue", plan.values.revenue)}</td>
    <td class="number">${formatPlanMetric("leads", plan.values.leads)}</td>
    <td class="number">${formatPlanMetric("bookings", plan.values.bookings)}</td>
    <td>${planEscape(plan.updated_at)}</td>
    <td><div class="budget-history-actions"><button type="button" data-plan-edit="${plan.id}" title="Редактировать">✎</button><button type="button" data-plan-audit="${plan.id}" title="История">⌕</button><button class="danger" type="button" data-plan-delete="${plan.id}" title="Удалить">×</button></div></td>
  </tr>`).join("");
}

async function loadPlans() {
  planSetLoading(true); planMessage("error");
  try {
    const month = document.querySelector('#planForm [name="plan_month"]').value || currentPlanMonth();
    const data = await planRequest(`/api/plans?month=${encodeURIComponent(month)}`);
    if (!data) return;
    planState.metrics = data.metrics;
    planState.plans = data.plans;
    planState.schemaReady = data.schema_ready;
    fillPlanSelect(document.querySelector('#planForm [name="branch"]'), data.options.branches);
    fillPlanSelect(document.querySelector('#planForm [name="direction"]'), data.options.directions);
    renderPlanFields(); renderPlanHistory();
    if (!data.schema_ready) planMessage("error", "Хранилище план–факта ещё не создано. До миграции форма доступна только для просмотра.");
  } catch (error) { planMessage("error", error.message); }
  finally { planSetLoading(false); }
}

function startPlanEdit(id) {
  const plan = planState.plans.find((item) => Number(item.id) === Number(id));
  if (!plan) return;
  const form = document.querySelector("#planForm");
  planState.editingId = Number(id);
  form.elements.plan_month.value = plan.plan_month;
  form.elements.branch.value = plan.branch;
  form.elements.direction.value = plan.direction;
  planState.metrics.forEach((metric) => {
    const input = form.querySelector(`[data-plan-metric="${metric.code}"]`);
    if (input) input.value = formatPlanInputValue(metric, plan.values[metric.code]);
  });
  document.querySelector("#cancelPlanEdit").hidden = false;
  form.querySelector('[type="submit"]').textContent = "Сохранить изменения";
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

function cancelPlanEdit() {
  planState.editingId = null;
  const form = document.querySelector("#planForm");
  form.reset();
  form.elements.plan_month.value = document.querySelector('#planForm [name="plan_month"]').value || currentPlanMonth();
  document.querySelectorAll('[data-plan-metric]').forEach((input) => { input.value = input.readOnly ? "0" : ""; });
  document.querySelector("#cancelPlanEdit").hidden = true;
  form.querySelector('[type="submit"]').textContent = "Сохранить план";
}

async function savePlan(event) {
  event.preventDefault();
  if (!planState.schemaReady) return;
  const form = event.currentTarget;
  const button = form.querySelector('[type="submit"]');
  button.disabled = true; planSetLoading(true); planMessage("error");
  const payload = payloadFromPlanForm();
  try {
    const editing = planState.editingId !== null;
    const data = await planRequest(editing ? `/api/plans/${planState.editingId}` : "/api/plans", {
      method: editing ? "PUT" : "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": planCsrf },
      body: JSON.stringify(payload),
    });
    if (!data) return;
    const month = payload.plan_month;
    cancelPlanEdit();
    form.elements.plan_month.value = month;
    await loadPlans();
    planMessage("success", editing ? "Изменения сохранены. Предыдущий снимок остался в истории." : "План сохранён.");
  } catch (error) { planMessage("error", error.message); }
  finally { button.disabled = false; planSetLoading(false); }
}

async function deletePlan(id) {
  const plan = planState.plans.find((item) => Number(item.id) === Number(id));
  if (!plan || !window.confirm(`Удалить план «${plan.branch} · ${plan.direction}»? Снимок записи останется в истории.`)) return;
  planSetLoading(true);
  try {
    await planRequest(`/api/plans/${id}`, { method: "DELETE", headers: { "X-CSRF-Token": planCsrf } });
    if (planState.editingId === Number(id)) cancelPlanEdit();
    await loadPlans(); planMessage("success", "План удалён из расчёта. История сохранена.");
  } catch (error) { planMessage("error", error.message); }
  finally { planSetLoading(false); }
}

async function showPlanAudit(id) {
  try {
    const data = await planRequest(`/api/plans/${id}/history`);
    if (!data) return;
    document.querySelector("#planAuditRows").innerHTML = data.history.length ? data.history.map((row) => `<article><strong>${row.action === "created" ? "Создано" : row.action === "updated" ? "Изменено" : "Удалено"}</strong><span>${planEscape(row.changed_at)} · ${planEscape(row.changed_by)}</span></article>`).join("") : "<p>История пуста.</p>";
    document.querySelector("#planAuditDialog").showModal();
  } catch (error) { planMessage("error", error.message); }
}

document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("#planForm");
  form.elements.plan_month.value = currentPlanMonth();
  form.addEventListener("submit", savePlan);
  form.elements.plan_month.addEventListener("change", (event) => {
    const month = event.target.value;
    cancelPlanEdit();
    form.elements.plan_month.value = month;
    loadPlans();
  });
  document.querySelector("#cancelPlanEdit").addEventListener("click", cancelPlanEdit);
  document.querySelector("#planHistoryRows").addEventListener("click", (event) => {
    const edit = event.target.closest("[data-plan-edit]");
    const audit = event.target.closest("[data-plan-audit]");
    const remove = event.target.closest("[data-plan-delete]");
    if (edit) startPlanEdit(edit.dataset.planEdit);
    if (audit) showPlanAudit(audit.dataset.planAudit);
    if (remove) deletePlan(remove.dataset.planDelete);
  });
  document.querySelector("[data-close-audit]").addEventListener("click", () => document.querySelector("#planAuditDialog").close());
  loadPlans();
});
