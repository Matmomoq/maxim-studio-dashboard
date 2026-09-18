const state = {
  branches: [],
  branchKpi: null,
  branchSortKey: "leads",
  branchSortDirection: "desc",
  sourceMetric: "conversion",
  marketingGroups: { source: [], branch: [], direction: [], source_branch: [] },
  marketingKpi: null,
  marketingGroup: "source",
  marketingSortKey: "spend",
  marketingSortDirection: "desc",
  expandedSources: new Set(),
  trendMetric: "leads_bookings",
  trendGranularity: "day",
  trendBreakdown: "none",
  trendCompare: false,
  trendData: null,
  trendController: null,
  requestController: null,
};

const number = new Intl.NumberFormat("ru-RU");
const money = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 0,
});
const percent = (value) => `${Number(value || 0).toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%`;
const moneyOrDash = (value) => (value === null || value === undefined ? "—" : money.format(value));
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const localIso = (date) => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const moscowToday = () => {
  const [year, month, day] = document.body.dataset.today.split("-").map(Number);
  return new Date(year, month - 1, day, 12);
};

function currentParams() {
  const params = new URLSearchParams({
    date_from: document.querySelector("#dateFrom").value,
    date_to: document.querySelector("#dateTo").value,
  });
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    const filterName = component.dataset.multiFilter;
    const selected = [...component.querySelectorAll('input:not([value="Все"]):checked')]
      .map((input) => input.value);
    if (!selected.length) params.append(filterName, "Все");
    else selected.forEach((value) => params.append(filterName, value));
  });
  params.set("include_drafts", document.querySelector("#includeDrafts").checked ? "true" : "false");
  return params;
}

function setLoading(active) {
  document.querySelector("#loadingLine").classList.toggle("active", active);
}

function showError(message = "") {
  const banner = document.querySelector("#errorBanner");
  banner.hidden = !message;
  banner.textContent = message;
}

function setDefaultPeriod() {
  const today = moscowToday();
  const start = new Date(today.getFullYear(), today.getMonth(), 1);
  const minDate = document.body.dataset.minDate;
  document.querySelector("#dateFrom").value = localIso(start) < minDate ? minDate : localIso(start);
  document.querySelector("#dateTo").value = localIso(today);
}

function applyPreset(name) {
  const today = moscowToday();
  let start = new Date(today);
  if (name === "week") {
    start.setDate(today.getDate() - 6);
  } else if (name === "month") {
    start = new Date(today.getFullYear(), today.getMonth(), 1);
  } else if (name === "quarter") {
    const quarterMonth = Math.floor(today.getMonth() / 3) * 3;
    start = new Date(today.getFullYear(), quarterMonth, 1);
  }
  const minDate = document.body.dataset.minDate;
  document.querySelector("#dateFrom").value = localIso(start) < minDate ? minDate : localIso(start);
  document.querySelector("#dateTo").value = localIso(today);
  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.classList.toggle("active", button.dataset.preset === name);
  });
  loadDashboard();
}

function selectedMultiValues(component) {
  return [...component.querySelectorAll('input:not([value="Все"]):checked')]
    .map((input) => input.value);
}

function updateMultiLabel(component) {
  const selected = selectedMultiValues(component);
  const label = component.querySelector(".multi-trigger span");
  if (!selected.length) label.textContent = component.dataset.allLabel;
  else if (selected.length === 1) label.textContent = selected[0];
  else label.textContent = `${selected.length} выбрано`;
}

function closeMultiSelect(component) {
  component.querySelector(".multi-menu").hidden = true;
  component.querySelector(".multi-trigger").setAttribute("aria-expanded", "false");
}

function closeAllMultiSelects(except = null) {
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    if (component !== except) closeMultiSelect(component);
  });
}

function fillMultiFilter(filterName, values) {
  const component = document.querySelector(`[data-multi-filter="${filterName}"]`);
  const selectedBefore = new Set(selectedMultiValues(component));
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
  const hasSpecificSelection = selectedMultiValues(component).length > 0;
  component.querySelector('input[value="Все"]').checked = !hasSpecificSelection;
  updateMultiLabel(component);
}

function resetMultiFilters() {
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    component.querySelectorAll('input:not([value="Все"])').forEach((input) => { input.checked = false; });
    component.querySelector('input[value="Все"]').checked = true;
    updateMultiLabel(component);
    closeMultiSelect(component);
  });
}

function setupMultiFilters() {
  document.querySelectorAll("[data-multi-filter]").forEach((component) => {
    const trigger = component.querySelector(".multi-trigger");
    const menu = component.querySelector(".multi-menu");
    trigger.addEventListener("click", () => {
      const willOpen = menu.hidden;
      closeAllMultiSelects(component);
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
      updateMultiLabel(component);
      loadDashboard();
    });
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-multi-filter]")) closeAllMultiSelects();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAllMultiSelects();
  });
}

async function loadFilters() {
  const response = await fetch("/api/filters");
  if (!response.ok) throw new Error("Не удалось загрузить значения фильтров");
  const data = await response.json();
  fillMultiFilter("branch", data.options.branch);
  fillMultiFilter("source", data.options.source);
  fillMultiFilter("direction", data.options.direction);
  fillMultiFilter("offer", data.options.offer);
  document.querySelector("#dateFrom").min = data.min_date;
  document.querySelector("#dateTo").min = data.min_date;
  const syncText = data.last_sync
    ? `Данные в БД: ${data.last_sync}`
    : "Подключено к базе amoCRM";
  document.querySelector("#syncStatus span:last-child").textContent = syncText;
}

function renderKpi(kpi, marketing) {
  const economics = marketing?.kpi || {};
  document.querySelector("#kpiSpend").textContent = moneyOrDash(economics.spend);
  document.querySelector("#kpiLeads").textContent = number.format(kpi.leads);
  document.querySelector("#kpiBookings").textContent = number.format(kpi.bookings);
  document.querySelector("#kpiConversion").textContent = percent(kpi.conversion);
  document.querySelector("#kpiCpl").textContent = moneyOrDash(economics.cpl);
  document.querySelector("#kpiCostBooking").textContent = moneyOrDash(economics.cost_per_booking);

  const offerLeads = kpi.top_offer_leads;
  const offerLeadsElement = document.querySelector("#topOfferLeads");
  offerLeadsElement.textContent = offerLeads?.offer || "Нет данных";
  offerLeadsElement.title = offerLeads
    ? `${number.format(offerLeads.leads)} лидов · ${number.format(offerLeads.bookings)} записей`
    : "";

  const offerConversion = kpi.top_offer_conversion;
  const offerConversionElement = document.querySelector("#topOfferConversion");
  offerConversionElement.textContent = offerConversion?.offer || "Нет данных";
  offerConversionElement.title = offerConversion
    ? `${percent(offerConversion.conversion)} · ${number.format(offerConversion.leads)} лидов`
    : "";
}

function renderVolumeChart(rows, options) {
  const known = rows
    .filter((row) => row[options.key] !== "Не определено")
    .sort((left, right) => right.leads - left.leads || String(left[options.key]).localeCompare(String(right[options.key]), "ru"))
    .slice(0, 10);
  const chart = document.querySelector(options.chart);
  const empty = document.querySelector(options.empty);
  empty.hidden = known.length > 0;
  chart.innerHTML = "";
  if (!known.length) return;
  const maxLeads = Math.max(...known.map((row) => row.leads), 1);
  known.forEach((row) => {
    const tooltip = `${options.label}: ${row[options.key]}\nЛидов: ${row.leads}\nЗаписей: ${row.bookings}\nКонверсия: ${percent(row.conversion)}`;
    const element = document.createElement("div");
    element.className = "offer-row";
    element.title = tooltip;
    element.innerHTML = `
      <span class="offer-name">${escapeHtml(row[options.key])}</span>
      <span class="bar-track">
        <span class="bar-leads" style="width:${(row.leads / maxLeads) * 100}%"></span>
        <span class="bar-bookings" style="width:${(row.bookings / maxLeads) * 100}%"></span>
      </span>
      <span class="offer-value">${number.format(row.leads)} / ${number.format(row.bookings)}</span>`;
    chart.append(element);
  });
}

function renderOffers(rows) {
  renderVolumeChart(rows, {
    key: "offer",
    label: "Оффер",
    chart: "#offersChart",
    empty: "#offersEmpty",
  });
}

function renderSourceVolume(rows) {
  renderVolumeChart(rows, {
    key: "source",
    label: "Источник",
    chart: "#sourceVolumeChart",
    empty: "#sourceVolumeEmpty",
  });
}

function metricValue(row, key) {
  if (key === "conversion") return percent(row[key]);
  return moneyOrDash(row[key]);
}

function renderSources(rows) {
  const metric = state.sourceMetric;
  const known = rows
    .filter((row) => row.label !== "Не определено" && row[metric] !== null && row[metric] !== undefined)
    .sort((left, right) => Number(right[metric]) - Number(left[metric]))
    .slice(0, 9);
  const chart = document.querySelector("#sourcesChart");
  const empty = document.querySelector("#sourcesEmpty");
  empty.hidden = known.length > 0;
  chart.innerHTML = "";
  const maxValue = Math.max(...known.map((row) => Number(row[metric])), 1);
  known.forEach((row) => {
    const element = document.createElement("div");
    element.className = "economics-row";
    element.title = `Источник: ${row.label}\nРасход: ${moneyOrDash(row.spend)}\nЛидов: ${row.leads}\nЗаписей: ${row.bookings}\nКонверсия: ${percent(row.conversion)}\nЦена лида: ${moneyOrDash(row.cpl)}\nЦена записи: ${moneyOrDash(row.cost_per_booking)}`;
    element.innerHTML = `
      <span class="offer-name">${escapeHtml(row.label)}</span>
      <span class="bar-track"><span class="economics-bar" style="width:${(Number(row[metric]) / maxValue) * 100}%"></span></span>
      <span class="offer-value">${metricValue(row, metric)}</span>`;
    chart.append(element);
  });
}

const trendPalette = ["#1f765f", "#f07b62", "#6e8fc7", "#b28752", "#8b6bb1", "#70867d"];

function trendMetricValue(point, key) {
  if (key === "conversion") return percent(point[key]);
  if (["spend", "cpl", "cost_per_booking"].includes(key)) return moneyOrDash(point[key]);
  return number.format(point[key] || 0);
}

function trendAxisValue(value) {
  if (state.trendMetric === "conversion") return `${Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
  if (["spend", "cpl", "cost_per_booking"].includes(state.trendMetric)) {
    return new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 }).format(value) + " ₽";
  }
  return number.format(Math.round(value));
}

function trendLineSpecs(data) {
  const specs = [];
  const currentSeries = data?.current?.series || [];
  currentSeries.forEach((series, index) => {
    const color = trendPalette[index % trendPalette.length];
    if (state.trendMetric === "leads_bookings") {
      specs.push({ label: `${series.label} · лиды`, key: "leads", color, points: series.points, secondary: false });
      specs.push({ label: `${series.label} · записи`, key: "bookings", color: state.trendBreakdown === "none" ? "#f07b62" : color, points: series.points, secondary: true });
    } else {
      specs.push({ label: series.label, key: state.trendMetric, color, points: series.points, secondary: false });
    }
  });
  if (state.trendCompare && state.trendBreakdown === "none" && data?.previous?.series?.length) {
    const previous = data.previous.series[0];
    if (state.trendMetric === "leads_bookings") {
      specs.push({ label: "Предыдущий период · лиды", key: "leads", color: "#66746f", points: previous.points, secondary: true, previous: true });
      specs.push({ label: "Предыдущий период · записи", key: "bookings", color: "#b48b81", points: previous.points, secondary: true, previous: true });
    } else {
      specs.push({ label: "Предыдущий период", key: state.trendMetric, color: "#66746f", points: previous.points, secondary: true, previous: true });
    }
  }
  return specs;
}

function trendSegments(spec, chartWidth, chartHeight, left, top, maxValue) {
  const parts = [];
  let current = [];
  spec.points.forEach((point, index) => {
    const value = point[spec.key];
    if (value === null || value === undefined) {
      if (current.length) parts.push(current);
      current = [];
      return;
    }
    const x = left + (spec.points.length <= 1 ? chartWidth / 2 : (index / (spec.points.length - 1)) * chartWidth);
    const y = top + chartHeight - (Number(value) / maxValue) * chartHeight;
    current.push({ x, y, point, value });
  });
  if (current.length) parts.push(current);
  return parts;
}

function trendPointTitle(point, spec) {
  return `${point.label}\n${spec.label}\nЗначение: ${trendMetricValue(point, spec.key)}\nЛидов: ${number.format(point.leads)}\nЗаписей: ${number.format(point.bookings)}\nКонверсия: ${percent(point.conversion)}\nРасход: ${moneyOrDash(point.spend)}\nЦена лида: ${moneyOrDash(point.cpl)}\nЦена записи: ${moneyOrDash(point.cost_per_booking)}`;
}

function renderTrend(data) {
  state.trendData = data;
  const current = data?.current;
  if (!current) return;
  const summary = current.summary || {};
  document.querySelector("#trendSummaryLeads").textContent = number.format(summary.leads || 0);
  document.querySelector("#trendSummaryBookings").textContent = number.format(summary.bookings || 0);
  document.querySelector("#trendSummaryConversion").textContent = percent(summary.conversion);
  document.querySelector("#trendSummarySpend").textContent = moneyOrDash(summary.spend);
  document.querySelector("#trendPeriodLabel").textContent = `${current.period.from.split("-").reverse().join(".")} — ${current.period.to.split("-").reverse().join(".")}`;

  const specs = trendLineSpecs(data);
  const values = specs.flatMap((spec) => spec.points.map((point) => point[spec.key]).filter((value) => value !== null && value !== undefined).map(Number));
  const empty = document.querySelector("#trendEmpty");
  empty.hidden = values.length > 0;
  const svg = document.querySelector("#trendChart");
  if (!values.length) {
    svg.innerHTML = "";
    document.querySelector("#trendLegend").innerHTML = "";
    return;
  }

  const width = 1080;
  const height = 390;
  const left = 68;
  const top = 24;
  const right = 24;
  const bottom = 66;
  const chartWidth = width - left - right;
  const chartHeight = height - top - bottom;
  const maxValue = Math.max(...values, 1);
  const grid = [];
  for (let index = 0; index <= 4; index += 1) {
    const ratio = index / 4;
    const y = top + chartHeight - ratio * chartHeight;
    grid.push(`<line class="trend-grid-line" x1="${left}" y1="${y}" x2="${left + chartWidth}" y2="${y}"></line>`);
    grid.push(`<text class="trend-axis-label" x="${left - 10}" y="${y + 4}" text-anchor="end">${escapeHtml(trendAxisValue(maxValue * ratio))}</text>`);
  }

  const buckets = current.buckets || [];
  const labelStep = Math.max(1, Math.ceil(buckets.length / 8));
  buckets.forEach((bucket, index) => {
    if (index % labelStep !== 0 && index !== buckets.length - 1) return;
    const x = left + (buckets.length <= 1 ? chartWidth / 2 : (index / (buckets.length - 1)) * chartWidth);
    grid.push(`<text class="trend-axis-label" x="${x}" y="${top + chartHeight + 28}" text-anchor="middle">${escapeHtml(bucket.label)}</text>`);
  });

  const lines = [];
  specs.forEach((spec) => {
    const segments = trendSegments(spec, chartWidth, chartHeight, left, top, maxValue);
    segments.forEach((segment) => {
      const points = segment.map((item) => `${item.x.toFixed(1)},${item.y.toFixed(1)}`).join(" ");
      lines.push(`<polyline class="trend-line ${spec.secondary ? "secondary" : ""}" stroke="${spec.color}" points="${points}"></polyline>`);
      if (spec.points.length <= 62 && !spec.previous) {
        segment.forEach((item) => {
          lines.push(`<circle class="trend-point" fill="${spec.color}" cx="${item.x.toFixed(1)}" cy="${item.y.toFixed(1)}" r="4"><title>${escapeHtml(trendPointTitle(item.point, spec))}</title></circle>`);
        });
      }
    });
  });
  svg.innerHTML = `${grid.join("")}${lines.join("")}`;
  document.querySelector("#trendLegend").innerHTML = specs.map((spec) => `<span class="${spec.secondary ? "secondary" : ""}" style="--trend-color:${spec.color}"><i></i>${escapeHtml(spec.label)}</span>`).join("");
  const note = document.querySelector("#trendNote");
  if (state.trendBreakdown !== "none") {
    note.textContent = "Показаны пять крупнейших групп; остальные объединены в «Прочие». Сравнение периодов доступно без разбивки.";
  } else if (state.trendCompare && !data.comparison_available) {
    note.textContent = "Предыдущий сопоставимый период недоступен: в базе ещё недостаточно ранних данных.";
  } else {
    note.textContent = "Лиды объединены по дате создания сделки. Записи относятся к периоду создания соответствующего лида.";
  }
}

async function loadTrend() {
  if (state.trendController) state.trendController.abort();
  const controller = new AbortController();
  state.trendController = controller;
  document.querySelector("#trendPanel")?.classList.add("loading");
  const params = currentParams();
  params.set("granularity", state.trendGranularity);
  params.set("breakdown", state.trendBreakdown);
  params.set("compare", state.trendCompare ? "true" : "false");
  try {
    const response = await fetch(`/api/trends?${params.toString()}`, { signal: controller.signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не удалось построить динамику");
    renderTrend(data);
  } catch (error) {
    if (error.name === "AbortError") return;
    const empty = document.querySelector("#trendEmpty");
    empty.hidden = false;
    empty.textContent = error.message;
  } finally {
    if (state.trendController === controller) {
      state.trendController = null;
      document.querySelector("#trendPanel")?.classList.remove("loading");
    }
  }
}

function renderCoverage(coverage) {
  const badge = document.querySelector("#coverageBadge");
  badge.dataset.status = coverage?.status || "unavailable";
  badge.textContent = coverage?.label || "Данные о расходах недоступны";
  const details = [];
  if (coverage?.missing_units?.length) {
    details.push(`Не заполнено: ${coverage.missing_units.join(", ")}`);
  }
  if (coverage?.unconfigured_sources?.length) {
    details.push(`Без модели расхода: ${coverage.unconfigured_sources.join(", ")}`);
  }
  if (coverage?.unallocated_spend) {
    details.push(`Не распределено: ${money.format(coverage.unallocated_spend)}`);
  }
  badge.title = details.join("\n");
}

function compareMarketing(left, right) {
  const direction = state.marketingSortDirection === "asc" ? 1 : -1;
  const leftValue = left[state.marketingSortKey];
  const rightValue = right[state.marketingSortKey];
  if ((leftValue === null || leftValue === undefined) && (rightValue === null || rightValue === undefined)) return 0;
  if (leftValue === null || leftValue === undefined) return 1;
  if (rightValue === null || rightValue === undefined) return -1;
  if (typeof leftValue === "number" && typeof rightValue === "number") {
    return (leftValue - rightValue) * direction;
  }
  return String(leftValue).localeCompare(String(rightValue), "ru") * direction;
}

function coverageTitle(row) {
  if (row.coverage === "complete") return "Расход заполнен";
  if (String(row.key || "").startsWith("Скорозвон ")) return "Расходы для этого источника не рассчитываются";
  if (row.coverage === "not_configured") return "Для источника не задана модель расхода";
  return "Расход заполнен не полностью";
}

function marketingRowHtml(row, options = {}) {
  const child = Boolean(options.child);
  const expandable = Boolean(options.expandable);
  const expanded = Boolean(options.expanded);
  let label = `<span class="coverage-dot ${escapeHtml(row.coverage)}" title="${escapeHtml(coverageTitle(row))}"></span><strong>${escapeHtml(row.label)}</strong>`;
  if (child) {
    label = `<span class="source-child-guide" aria-hidden="true"></span>${label}`;
  } else if (expandable) {
    const action = expanded ? "Скрыть" : "Показать";
    label = `<button class="source-expand" type="button" data-expand-source="${escapeHtml(row.key)}" aria-expanded="${expanded}" aria-label="${action} филиалы источника ${escapeHtml(row.label)}"><i>${expanded ? "⌄" : "›"}</i></button>${label}`;
  }
  return `
    <tr${child ? ' class="economics-child-row"' : ""}>
      <td>${label}</td>
      <td class="money-cell">${moneyOrDash(row.spend)}</td>
      <td class="number">${number.format(row.leads)}</td>
      <td class="number">${number.format(row.bookings)}</td>
      <td class="conversion-cell">${percent(row.conversion)}</td>
      <td class="money-cell">${moneyOrDash(row.cpl)}</td>
      <td class="money-cell">${moneyOrDash(row.cost_per_booking)}</td>
    </tr>`;
}

function renderMarketingTable() {
  const rows = [...(state.marketingGroups[state.marketingGroup] || [])].sort(compareMarketing);
  const body = document.querySelector("#economicsBody");
  const total = document.querySelector("#economicsTotal");
  const labels = { source: "Источник", branch: "Филиал", direction: "Направление" };
  document.querySelector("#marketingGroupLabel").textContent = labels[state.marketingGroup];
  document.querySelector("#economicsNote").textContent = state.marketingGroup === "source"
    ? "Источники с надёжной привязкой можно раскрыть по филиалам. Значение «—» означает, что расход не рассчитывается или ещё не заполнен."
    : "Значение «—» означает, что расход для этого среза ещё не заполнен.";
  if (rows.length && state.marketingGroup === "source") {
    const sourceBranches = state.marketingGroups.source_branch || [];
    body.innerHTML = rows.map((row) => {
      const children = sourceBranches
        .filter((child) => child.source === row.key)
        .sort(compareMarketing);
      const expanded = state.expandedSources.has(row.key);
      const childRows = expanded
        ? children.map((child) => marketingRowHtml(child, { child: true })).join("")
        : "";
      return marketingRowHtml(row, { expandable: children.length > 0, expanded }) + childRows;
    }).join("");
  } else if (rows.length) {
    body.innerHTML = rows.map((row) => marketingRowHtml(row)).join("");
  } else {
    body.innerHTML = `<tr><td class="empty-state" colspan="7">Нет данных по выбранному срезу</td></tr>`;
  }
  const kpi = state.marketingKpi || {};
  total.innerHTML = `
    <tr><td>ИТОГО</td><td>${moneyOrDash(kpi.spend)}</td><td>${number.format(kpi.leads || 0)}</td><td>${number.format(kpi.bookings || 0)}</td><td>${percent(kpi.conversion)}</td><td>${moneyOrDash(kpi.cpl)}</td><td>${moneyOrDash(kpi.cost_per_booking)}</td></tr>`;
  document.querySelectorAll("[data-marketing-sort]").forEach((button) => {
    const active = button.dataset.marketingSort === state.marketingSortKey;
    button.classList.toggle("active", active);
    button.querySelector("i").textContent = active
      ? (state.marketingSortDirection === "asc" ? "↑" : "↓")
      : "↕";
  });
}

function renderMarketing(marketing) {
  const safe = marketing || { kpi: {}, coverage: {}, groups: {} };
  state.marketingKpi = safe.kpi;
  state.marketingGroups = safe.groups || { source: [], branch: [], direction: [], source_branch: [] };
  renderCoverage(safe.coverage);
  renderSources(state.marketingGroups.source || []);
  renderMarketingTable();
}

function renderBranches(rows, kpi) {
  const body = document.querySelector("#branchesBody");
  const total = document.querySelector("#branchesTotal");
  const direction = state.branchSortDirection === "asc" ? 1 : -1;
  const sorted = [...rows].sort((left, right) => {
    const leftValue = left[state.branchSortKey];
    const rightValue = right[state.branchSortKey];
    if (typeof leftValue === "number" && typeof rightValue === "number") {
      return (leftValue - rightValue) * direction;
    }
    return String(leftValue).localeCompare(String(rightValue), "ru") * direction;
  });
  body.innerHTML = sorted.map((row) => `
    <tr>
      <td><strong>${escapeHtml(row.branch)}</strong></td>
      <td class="number">${number.format(row.leads)}</td>
      <td class="number">${number.format(row.bookings)}</td>
      <td class="conversion-cell">${percent(row.conversion)}</td>
    </tr>`).join("");
  total.innerHTML = `
    <tr><td>ИТОГО</td><td>${number.format(kpi.leads)}</td><td>${number.format(kpi.bookings)}</td><td>${percent(kpi.conversion)}</td></tr>`;
  document.querySelectorAll("[data-branch-sort]").forEach((button) => {
    const active = button.dataset.branchSort === state.branchSortKey;
    button.classList.toggle("active", active);
    button.querySelector("span").textContent = active
      ? (state.branchSortDirection === "asc" ? "↑" : "↓")
      : "↕";
    button.closest("th").setAttribute(
      "aria-sort",
      active ? (state.branchSortDirection === "asc" ? "ascending" : "descending") : "none",
    );
  });
}

async function loadDashboard() {
  if (state.requestController) state.requestController.abort();
  const controller = new AbortController();
  state.requestController = controller;
  setLoading(true);
  showError();
  loadTrend();
  try {
    const response = await fetch(`/api/dashboard?${currentParams().toString()}`, { signal: controller.signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не удалось получить данные");
    renderKpi(data.kpi, data.marketing);
    renderSourceVolume(data.sources);
    renderOffers(data.offers);
    renderMarketing(data.marketing);
    state.branches = data.branches;
    state.branchKpi = data.kpi;
    renderBranches(state.branches, state.branchKpi);
  } catch (error) {
    if (error.name === "AbortError") return;
    showError(error.message);
  } finally {
    if (state.requestController === controller) {
      state.requestController = null;
      setLoading(false);
    }
  }
}

async function init() {
  setDefaultPeriod();
  setupMultiFilters();
  setLoading(true);
  try {
    await loadFilters();
    await loadDashboard();
  } catch (error) {
    showError(error.message);
    setLoading(false);
  }

  document.querySelectorAll("#dateFrom, #dateTo").forEach((element) => {
    element.addEventListener("change", () => {
      document.querySelectorAll("[data-preset]").forEach((button) => button.classList.remove("active"));
      loadDashboard();
    });
  });
  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.addEventListener("click", () => applyPreset(button.dataset.preset));
  });
  document.querySelector("#resetFilters").addEventListener("click", () => {
    resetMultiFilters();
    setDefaultPeriod();
    document.querySelectorAll("[data-preset]").forEach((button) => button.classList.toggle("active", button.dataset.preset === "month"));
    loadDashboard();
  });
  document.querySelectorAll("[data-branch-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.branchSort;
      if (state.branchSortKey === key) {
        state.branchSortDirection = state.branchSortDirection === "asc" ? "desc" : "asc";
      } else {
        state.branchSortKey = key;
        state.branchSortDirection = ["leads", "bookings", "conversion"].includes(key) ? "desc" : "asc";
      }
      renderBranches(state.branches, state.branchKpi);
    });
  });
  document.querySelectorAll("[data-source-metric]").forEach((button) => {
    button.addEventListener("click", () => {
      state.sourceMetric = button.dataset.sourceMetric;
      document.querySelectorAll("[data-source-metric]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      renderSources(state.marketingGroups.source || []);
    });
  });
  document.querySelectorAll("[data-trend-metric]").forEach((button) => {
    button.addEventListener("click", () => {
      state.trendMetric = button.dataset.trendMetric;
      document.querySelectorAll("[data-trend-metric]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      if (state.trendData) renderTrend(state.trendData);
    });
  });
  document.querySelectorAll("[data-trend-granularity]").forEach((button) => {
    button.addEventListener("click", () => {
      state.trendGranularity = button.dataset.trendGranularity;
      document.querySelectorAll("[data-trend-granularity]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      loadTrend();
    });
  });
  document.querySelector("#trendBreakdown").addEventListener("change", (event) => {
    state.trendBreakdown = event.target.value;
    const compare = document.querySelector("#trendCompare");
    const disabled = state.trendBreakdown !== "none";
    if (disabled) {
      state.trendCompare = false;
      compare.checked = false;
    }
    compare.disabled = disabled;
    compare.closest("label").classList.toggle("disabled", disabled);
    loadTrend();
  });
  document.querySelector("#trendCompare").addEventListener("change", (event) => {
    state.trendCompare = event.target.checked;
    loadTrend();
  });
  document.querySelectorAll("[data-marketing-group]").forEach((button) => {
    button.addEventListener("click", () => {
      state.marketingGroup = button.dataset.marketingGroup;
      document.querySelectorAll("[data-marketing-group]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      renderMarketingTable();
    });
  });
  document.querySelectorAll("[data-marketing-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.marketingSort;
      if (state.marketingSortKey === key) {
        state.marketingSortDirection = state.marketingSortDirection === "asc" ? "desc" : "asc";
      } else {
        state.marketingSortKey = key;
        state.marketingSortDirection = key === "label" ? "asc" : "desc";
      }
      renderMarketingTable();
    });
  });
  document.querySelector("#economicsBody").addEventListener("click", (event) => {
    const button = event.target.closest("[data-expand-source]");
    if (!button) return;
    const source = button.dataset.expandSource;
    if (state.expandedSources.has(source)) state.expandedSources.delete(source);
    else state.expandedSources.add(source);
    renderMarketingTable();
  });
  document.querySelector("#includeDrafts").addEventListener("change", loadDashboard);
}

document.addEventListener("DOMContentLoaded", init);
