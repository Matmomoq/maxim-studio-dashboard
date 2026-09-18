const funnelState = {
  groups: { source: [], branch: [], direction: [], source_branch: [] },
  kpi: null,
  group: "source",
  sortKey: "revenue",
  sortDirection: "desc",
  expandedSources: new Set(),
  controller: null,
};

const funnelNumber = new Intl.NumberFormat("ru-RU");
const funnelMoney = new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 0 });
const funnelPercent = (value) => value === null || value === undefined ? "—" : `${Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%`;
const funnelMoneyOrDash = (value) => value === null || value === undefined ? "—" : funnelMoney.format(value);
const funnelEscape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");

function funnelLocalIso(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function funnelToday() {
  const [year, month, day] = document.body.dataset.today.split("-").map(Number);
  return new Date(year, month - 1, day, 12);
}

function funnelSetDefaultPeriod() {
  const today = funnelToday();
  const start = new Date(today.getFullYear(), today.getMonth(), 1);
  const minDate = document.body.dataset.minDate;
  document.querySelector("#dateFrom").value = funnelLocalIso(start) < minDate ? minDate : funnelLocalIso(start);
  document.querySelector("#dateTo").value = funnelLocalIso(today);
}

function funnelSelected(component) {
  return [...component.querySelectorAll('input:not([value="Все"]):checked')].map((input) => input.value);
}

function funnelUpdateMultiLabel(component) {
  const selected = funnelSelected(component);
  component.querySelector(".multi-trigger span").textContent = !selected.length
    ? component.dataset.allLabel : selected.length === 1 ? selected[0] : `${selected.length} выбрано`;
}

function funnelCloseMenus(except = null) {
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    if (component === except) return;
    component.querySelector(".multi-menu").hidden = true;
    component.querySelector(".multi-trigger").setAttribute("aria-expanded", "false");
  });
}

function funnelFillFilter(name, values) {
  const component = document.querySelector(`[data-multi-filter="${name}"]`);
  const selectedBefore = new Set(funnelSelected(component));
  const options = component.querySelector(".multi-options");
  options.innerHTML = values.map((value) => `<label class="multi-option"><input type="checkbox" value="${funnelEscape(value)}" ${selectedBefore.has(value) ? "checked" : ""}><span>${funnelEscape(value)}</span></label>`).join("");
  component.querySelector('input[value="Все"]').checked = !funnelSelected(component).length;
  funnelUpdateMultiLabel(component);
}

function funnelParams() {
  const params = new URLSearchParams({
    date_from: document.querySelector("#dateFrom").value,
    date_to: document.querySelector("#dateTo").value,
    include_drafts: document.querySelector("#includeDrafts").checked ? "true" : "false",
  });
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    const selected = funnelSelected(component);
    if (!selected.length) params.append(component.dataset.multiFilter, "Все");
    else selected.forEach((value) => params.append(component.dataset.multiFilter, value));
  });
  return params;
}

function funnelShowError(message = "") {
  const banner = document.querySelector("#errorBanner");
  banner.hidden = !message;
  banner.textContent = message;
}

function funnelSetLoading(active) {
  document.querySelector("#loadingLine").classList.toggle("active", active);
}

function renderFunnelKpi(kpi) {
  document.querySelector("#kpiSpend").textContent = funnelMoneyOrDash(kpi.spend);
  document.querySelector("#kpiRevenue").textContent = funnelMoneyOrDash(kpi.revenue);
  document.querySelector("#kpiRoi").textContent = funnelPercent(kpi.roi);
  document.querySelector("#kpiRoi").classList.toggle("negative", Number(kpi.roi) < 0);
  document.querySelector("#kpiAverageCheck").textContent = funnelMoneyOrDash(kpi.average_check);
  document.querySelector("#kpiLeads").textContent = funnelNumber.format(kpi.leads);
  document.querySelector("#kpiBookings").textContent = funnelNumber.format(kpi.bookings);
  document.querySelector("#kpiVisits").textContent = funnelNumber.format(kpi.visits);
  document.querySelector("#kpiPaying").textContent = funnelNumber.format(kpi.paying_leads);
  document.querySelector("#kpiCostPerLead").textContent = funnelMoneyOrDash(kpi.cost_per_lead);
  document.querySelector("#kpiCostPerBooking").textContent = funnelMoneyOrDash(kpi.cost_per_booking);
  document.querySelector("#kpiCostPerVisit").textContent = funnelMoneyOrDash(kpi.cost_per_visit);
  document.querySelector("#roiProfit").textContent = funnelMoneyOrDash(kpi.marketing_profit);
  document.querySelector("#roiSpend").textContent = funnelMoneyOrDash(kpi.spend);
  document.querySelector("#roiResult").textContent = funnelPercent(kpi.roi);
  document.querySelector("#roiResult").classList.toggle("negative", Number(kpi.roi) < 0);
}

function renderFunnelPath(rows) {
  const maxValue = Math.max(...rows.map((row) => row.value), 1);
  document.querySelector("#funnelPath").innerHTML = rows.map((row, index) => `
    <article class="funnel-step" style="--step-width:${Math.max((row.value / maxValue) * 100, row.value ? 16 : 5)}%">
      <div><span>${index + 1}</span><strong>${funnelEscape(row.label)}</strong><small>${funnelPercent(row.conversion)} от лидов</small></div>
      <b>${funnelNumber.format(row.value)}</b>
    </article>`).join("");
}

function renderRevenueMix(rows) {
  const maxRevenue = Math.max(...rows.map((row) => row.revenue), 1);
  document.querySelector("#revenueMix").innerHTML = rows.map((row) => `
    <div class="revenue-mix-row">
      <div><strong>${funnelEscape(row.label)}</strong><small>${funnelNumber.format(row.sales)} продаж</small></div>
      <span class="revenue-mix-track"><i style="width:${Math.max((row.revenue / maxRevenue) * 100, row.revenue ? 2 : 0)}%"></i></span>
      <b>${funnelMoney.format(row.revenue)}</b>
    </div>`).join("");
}

function funnelSortRows(rows) {
  const key = funnelState.sortKey;
  const direction = funnelState.sortDirection === "asc" ? 1 : -1;
  return [...rows].sort((left, right) => {
    const a = left[key]; const b = right[key];
    if (a === null || a === undefined) return 1;
    if (b === null || b === undefined) return -1;
    if (typeof a === "string") return a.localeCompare(b, "ru") * direction;
    return (Number(a) - Number(b)) * direction;
  });
}

function funnelRowHtml(row, { child = false, expandable = false } = {}) {
  const expanded = funnelState.expandedSources.has(row.label);
  const label = expandable
    ? `<button class="funnel-expand" type="button" data-expand-source="${funnelEscape(row.label)}" aria-expanded="${expanded}"><i>${expanded ? "⌄" : "›"}</i><span>${funnelEscape(row.label)}</span></button>`
    : `<span>${child ? "↳ " : ""}${funnelEscape(row.label)}</span>`;
  return `<tr class="${child ? "funnel-child-row" : ""}">
    <td>${label}</td><td>${funnelMoneyOrDash(row.spend)}</td><td>${funnelPercent(row.expense_coverage)}</td>
    <td>${funnelNumber.format(row.leads)}</td><td>${funnelMoneyOrDash(row.cost_per_lead)}</td>
    <td>${funnelNumber.format(row.bookings)}</td><td>${funnelMoneyOrDash(row.cost_per_booking)}</td>
    <td>${funnelNumber.format(row.visits)}</td><td>${funnelMoneyOrDash(row.cost_per_visit)}</td>
    <td>${funnelNumber.format(row.paying_leads)}</td>
    <td class="revenue-cell">${funnelMoney.format(row.revenue)}</td><td>${funnelMoneyOrDash(row.average_check)}</td>
    <td class="${Number(row.roi) < 0 ? "negative" : "positive"}">${funnelPercent(row.roi)}</td></tr>`;
}

function renderFunnelTable() {
  const rows = funnelSortRows(funnelState.groups[funnelState.group] || []);
  const children = funnelState.groups.source_branch || [];
  const body = [];
  rows.forEach((row) => {
    const sourceChildren = children.filter((child) => child.source === row.label);
    body.push(funnelRowHtml(row, { expandable: funnelState.group === "source" && sourceChildren.length > 0 }));
    if (funnelState.group === "source" && sourceChildren.length && funnelState.expandedSources.has(row.label)) {
      sourceChildren.forEach((child) => body.push(funnelRowHtml(child, { child: true })));
    }
  });
  document.querySelector("#funnelRows").innerHTML = body.join("") || `<tr><td colspan="13" class="empty-table">Нет данных по выбранным фильтрам</td></tr>`;
  const total = funnelState.kpi;
  document.querySelector("#funnelTotal").innerHTML = total ? `<tr><th>ИТОГО</th><th>${funnelMoneyOrDash(total.spend)}</th><th>${funnelPercent(total.expense_coverage)}</th><th>${funnelNumber.format(total.leads)}</th><th>${funnelMoneyOrDash(total.cost_per_lead)}</th><th>${funnelNumber.format(total.bookings)}</th><th>${funnelMoneyOrDash(total.cost_per_booking)}</th><th>${funnelNumber.format(total.visits)}</th><th>${funnelMoneyOrDash(total.cost_per_visit)}</th><th>${funnelNumber.format(total.paying_leads)}</th><th>${funnelMoney.format(total.revenue)}</th><th>${funnelMoneyOrDash(total.average_check)}</th><th>${funnelPercent(total.roi)}</th></tr>` : "";
  const labels = { source: "Источник", branch: "Филиал", direction: "Направление" };
  document.querySelector("#funnelGroupLabel").textContent = labels[funnelState.group];
  document.querySelector("#funnelTableNote").textContent = funnelState.group === "source" ? "Источники с надёжной привязкой можно раскрыть по филиалам. Скорозвон показывается только по всей сети." : `Показатели сгруппированы по полю «${labels[funnelState.group]}».`;
}

function renderCoverage(coverage) {
  const good = coverage.booked_leads === 0 || coverage.unlinked_bookings === 0;
  document.querySelector("#coveragePanel").dataset.status = good ? "complete" : "partial";
  document.querySelector("#coverageTitle").textContent = `${funnelNumber.format(coverage.linked_leads)} записей связано с Yclients`;
  document.querySelector("#coverageText").textContent = coverage.booked_leads
    ? `${funnelPercent(coverage.link_rate)} от записанных лидов · расходы покрывают ${funnelPercent(coverage.expense_rate)} лидов с настроенной моделью расходов`
    : "В выбранном срезе пока нет записанных лидов";
}

async function loadFunnelFilters() {
  const response = await fetch("/api/filters");
  if (!response.ok) throw new Error("Не удалось загрузить фильтры");
  const data = await response.json();
  ["branch", "source", "direction", "offer"].forEach((name) => funnelFillFilter(name, data.options[name]));
  document.querySelector("#syncStatus span:last-child").textContent = data.last_sync ? `Данные в БД: ${data.last_sync}` : "Подключено к базе";
}

async function loadFunnel() {
  if (funnelState.controller) funnelState.controller.abort();
  funnelState.controller = new AbortController();
  funnelShowError(); funnelSetLoading(true);
  try {
    const response = await fetch(`/api/funnel?${funnelParams()}`, { signal: funnelState.controller.signal });
    if (response.status === 401) { window.location.assign("/login?next=/funnel"); return; }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не удалось получить сквозную аналитику");
    funnelState.groups = data.groups;
    funnelState.kpi = data.kpi;
    renderFunnelKpi(data.kpi);
    renderFunnelPath(data.funnel);
    renderRevenueMix(data.revenue_mix);
    renderCoverage(data.coverage);
    renderFunnelTable();
    document.querySelector("#methodText").textContent = [
      `${data.method.period_basis}.`,
      `${data.method.revenue_basis}.`,
      `ROI: ${data.method.roi_formula}.`,
      `${data.method.roi_scope}.`,
      `${data.method.roi_note}`,
      data.method.administrator_note ? `${data.method.administrator_note}.` : "",
    ].filter(Boolean).join(" ");
    if (data.last_sync) document.querySelector("#syncStatus span:last-child").textContent = `Данные в БД: ${data.last_sync}`;
  } catch (error) {
    if (error.name !== "AbortError") funnelShowError(error.message);
  } finally { funnelSetLoading(false); }
}

function setupFunnelFilters() {
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    const trigger = component.querySelector(".multi-trigger");
    trigger.addEventListener("click", () => {
      const menu = component.querySelector(".multi-menu"); const open = menu.hidden;
      funnelCloseMenus(component); menu.hidden = !open; trigger.setAttribute("aria-expanded", String(open));
    });
    component.addEventListener("change", (event) => {
      if (!(event.target instanceof HTMLInputElement)) return;
      const all = component.querySelector('input[value="Все"]');
      const specific = [...component.querySelectorAll('input:not([value="Все"])')];
      if (event.target.value === "Все" && event.target.checked) specific.forEach((input) => { input.checked = false; });
      else if (event.target.value !== "Все" && event.target.checked) all.checked = false;
      if (!specific.some((input) => input.checked)) all.checked = true;
      funnelUpdateMultiLabel(component); loadFunnel();
    });
  });
  document.addEventListener("click", (event) => { if (!event.target.closest("[data-multi-filter]")) funnelCloseMenus(); });
}

function applyFunnelPreset(name) {
  const today = funnelToday(); let start = new Date(today);
  if (name === "week") start.setDate(today.getDate() - 6);
  else if (name === "month") start = new Date(today.getFullYear(), today.getMonth(), 1);
  else if (name === "quarter") start = new Date(today.getFullYear(), Math.floor(today.getMonth() / 3) * 3, 1);
  const minDate = document.body.dataset.minDate;
  document.querySelector("#dateFrom").value = funnelLocalIso(start) < minDate ? minDate : funnelLocalIso(start);
  document.querySelector("#dateTo").value = funnelLocalIso(today);
  document.querySelectorAll("[data-preset]").forEach((button) => button.classList.toggle("active", button.dataset.preset === name));
  loadFunnel();
}

funnelSetDefaultPeriod();
setupFunnelFilters();
document.querySelectorAll("[data-preset]").forEach((button) => button.addEventListener("click", () => applyFunnelPreset(button.dataset.preset)));
document.querySelectorAll("#dateFrom,#dateTo,#includeDrafts").forEach((input) => input.addEventListener("change", loadFunnel));
document.querySelector("#resetFilters").addEventListener("click", () => {
  funnelSetDefaultPeriod(); document.querySelector("#includeDrafts").checked = false;
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    component.querySelectorAll('input:not([value="Все"])').forEach((input) => { input.checked = false; });
    component.querySelector('input[value="Все"]').checked = true; funnelUpdateMultiLabel(component);
  });
  loadFunnel();
});
document.querySelectorAll("[data-funnel-group]").forEach((button) => button.addEventListener("click", () => {
  funnelState.group = button.dataset.funnelGroup;
  document.querySelectorAll("[data-funnel-group]").forEach((item) => item.classList.toggle("active", item === button));
  renderFunnelTable();
}));
document.querySelectorAll("[data-funnel-sort]").forEach((button) => button.addEventListener("click", () => {
  const key = button.dataset.funnelSort;
  if (funnelState.sortKey === key) funnelState.sortDirection = funnelState.sortDirection === "desc" ? "asc" : "desc";
  else { funnelState.sortKey = key; funnelState.sortDirection = key === "label" ? "asc" : "desc"; }
  renderFunnelTable();
}));
document.querySelector("#funnelRows").addEventListener("click", (event) => {
  const button = event.target.closest("[data-expand-source]"); if (!button) return;
  const source = button.dataset.expandSource;
  if (funnelState.expandedSources.has(source)) funnelState.expandedSources.delete(source); else funnelState.expandedSources.add(source);
  renderFunnelTable();
});

Promise.all([loadFunnelFilters(), loadFunnel()]).catch((error) => funnelShowError(error.message));
