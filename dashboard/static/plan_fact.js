const pfMoney = new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 });
const pfNumber = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const pfEscape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");

function pfMonth() { return document.querySelector("#planFactMonth").value; }
function pfSetLoading(active) { document.querySelector("#planFactLoading").classList.toggle("active", active); }
function pfError(text = "") { const node = document.querySelector("#planFactError"); node.hidden = !text; node.textContent = text; }

function pfParams() {
  const params = new URLSearchParams({ month: pfMonth() });
  const branch = document.querySelector("#planFactBranch").value;
  const direction = document.querySelector("#planFactDirection").value;
  if (branch) params.append("branch", branch);
  if (direction) params.append("direction", direction);
  return params;
}

function pfFormat(row, value) {
  if (row.unit === "money") return pfMoney.format(Number(value || 0));
  if (row.unit === "percent") return `${pfNumber.format(Number(value || 0))}%`;
  return pfNumber.format(Number(value || 0));
}

function pfFillOptions(data) {
  const controls = [
    [document.querySelector("#planFactBranch"), data.options.branches, "Все филиалы"],
    [document.querySelector("#planFactDirection"), data.options.directions, "Все направления"],
  ];
  controls.forEach(([select, values, all]) => {
    const current = select.value;
    select.innerHTML = `<option value="">${all}</option>` + values.map((value) => `<option value="${pfEscape(value)}">${pfEscape(value)}</option>`).join("");
    if ([...select.options].some((option) => option.value === current)) select.value = current;
  });
}

function pfMetric(data, code) { return data.metrics.find((row) => row.code === code); }

function pfRenderKpis(data) {
  const codes = ["revenue", "leads", "bookings", "primary_visits", "primary_subscription_sum", "receivables"];
  document.querySelector("#planFactKpis").innerHTML = codes.map((code) => {
    const row = pfMetric(data, code);
    const attainment = row.attainment === null ? "План не задан" : `Выполнение ${pfNumber.format(row.attainment)}%`;
    const state = row.favorable_variance >= 0 ? "positive" : "negative";
    return `<article class="${code === "revenue" ? "featured" : ""}"><span>${pfEscape(row.label)}</span><strong>${pfFormat(row, row.actual)}</strong><small>План: ${pfFormat(row, row.plan)}</small><em class="${state}">${attainment}</em></article>`;
  }).join("");
}

function pfRenderMetrics(data) {
  let section = "";
  document.querySelector("#planFactMetricRows").innerHTML = data.metrics.map((row) => {
    const heading = row.section !== section ? `<tr class="plan-fact-section-row"><th colspan="5">${pfEscape(row.section)}</th></tr>` : "";
    section = row.section;
    const state = row.favorable_variance >= 0 ? "positive" : "negative";
    return `${heading}<tr><td><strong>${pfEscape(row.label)}</strong>${row.formula ? `<small>${pfEscape(row.formula)}</small>` : ""}</td><td class="number">${pfFormat(row, row.plan)}</td><td class="number">${pfFormat(row, row.actual)}</td><td class="number ${state}">${row.variance > 0 ? "+" : ""}${pfFormat(row, row.variance)}</td><td class="number">${row.attainment === null ? "—" : `${pfNumber.format(row.attainment)}%`}</td></tr>`;
  }).join("");
  document.querySelector("#planCoverageBadge").textContent = `${data.coverage.plans} планов · ${data.coverage.scopes_with_actual} срезов с фактом`;
}

function pfPair(plan, actual, type = "number") {
  const formatter = type === "money" ? pfMoney : pfNumber;
  return `<span>${formatter.format(Number(plan || 0))}</span><i>/</i><strong>${formatter.format(Number(actual || 0))}</strong>`;
}

function pfRenderBreakdown(data) {
  const body = document.querySelector("#planFactBreakdownRows");
  body.innerHTML = data.breakdown.length ? data.breakdown.map((row) => `<tr class="${row.plan_configured ? "" : "plan-missing"}"><td><strong>${pfEscape(row.branch)}</strong></td><td>${pfEscape(row.direction)}</td><td>${pfPair(row.revenue_plan, row.revenue_actual, "money")}</td><td>${pfPair(row.leads_plan, row.leads_actual)}</td><td>${pfPair(row.bookings_plan, row.bookings_actual)}</td><td>${pfPair(row.primary_visits_plan, row.primary_visits_actual)}</td></tr>`).join("") : '<tr><td colspan="6" class="registry-empty">Данных по выбранному срезу нет</td></tr>';
}

function pfRenderIssues(data) {
  document.querySelector("#issueBadge").textContent = `${data.issue_summary.active} активных · ${data.issue_summary.resolved} исправлено`;
  const body = document.querySelector("#planFactIssueRows");
  body.innerHTML = data.issues.length ? data.issues.map((row) => {
    const resolved = Boolean(row.resolved_at);
    const status = resolved ? '<span class="issue-status resolved">Исправлено</span>' : '<span class="issue-status active">Активно</span>';
    const seen = resolved ? `${pfEscape(row.first_seen_at)}<br><small>исправлено ${pfEscape(row.resolved_at)}</small>` : `${pfEscape(row.first_seen_at)}<br><small>проверено ${pfEscape(row.last_seen_at)}</small>`;
    return `<tr><td>${status}</td><td><span class="issue-severity ${row.severity}">${row.severity === "critical" ? "Критическая" : "Проверить"}</span><strong>${pfEscape(row.details)}</strong></td><td>${row.lead_url ? `<a href="${pfEscape(row.lead_url)}" target="_blank" rel="noopener">${pfEscape(row.amo_lead_id)}</a>` : "—"}</td><td>${pfEscape(row.source || "—")}</td><td>${pfEscape(row.branch || "—")}</td><td>${pfEscape(row.direction || "—")}</td><td>${pfEscape(row.yclients_record_id || "—")}</td><td>${seen}</td></tr>`;
  }).join("") : '<tr><td colspan="8" class="registry-empty">Ошибок нет</td></tr>';
}

async function loadPlanFact() {
  pfSetLoading(true); pfError();
  try {
    const response = await fetch(`/api/yclients/plan-fact?${pfParams().toString()}`);
    if (response.status === 401) { window.location.assign("/login?next=/yclients/plan-fact"); return; }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не удалось загрузить план–факт");
    pfFillOptions(data); pfRenderKpis(data); pfRenderMetrics(data); pfRenderBreakdown(data); pfRenderIssues(data);
    document.querySelector("#planFactPeriod").textContent = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric" }).format(new Date(`${data.month}-01T12:00:00`));
    document.querySelector("#syncStatus span:last-child").textContent = data.last_sync ? `Данные в БД: ${data.last_sync}` : "Данные загружены";
  } catch (error) { pfError(error.message); }
  finally { pfSetLoading(false); }
}

document.addEventListener("DOMContentLoaded", () => {
  const today = document.body.dataset.today;
  document.querySelector("#planFactMonth").value = today < "2026-08-01" ? "2026-08" : today.slice(0, 7);
  ["#planFactMonth", "#planFactBranch", "#planFactDirection"].forEach((selector) => document.querySelector(selector).addEventListener("change", loadPlanFact));
  document.querySelector("#resetPlanFact").addEventListener("click", () => { document.querySelector("#planFactBranch").value = ""; document.querySelector("#planFactDirection").value = ""; loadPlanFact(); });
  loadPlanFact();
});
