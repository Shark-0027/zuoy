/* ==========================================================================
   FinGuard — live local application frontend
   Reads real analysis state from the FastAPI backend. No mock execution timers.
   ========================================================================== */

(function () {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const delay = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
  const escapeHTML = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");

  const FIELD_LABELS = {
    revenue: "营业收入",
    cogs: "营业成本",
    selling_expenses: "销售费用",
    admin_expenses: "管理费用",
    rd_expenses: "研发费用",
    finance_expenses: "财务费用",
    other_income_net: "其他净收益",
    income_tax: "所得税费用",
    net_profit: "净利润",
    accounts_receivable: "应收账款",
    other_receivables: "其他应收款",
    inventory: "存货",
    accounts_payable: "应付账款",
    cash: "货币资金",
    total_assets: "总资产",
    total_liabilities: "总负债",
    equity: "所有者权益",
    operating_cash_flow: "经营活动现金流量净额",
    investing_cash_flow: "投资活动现金流量净额",
    financing_cash_flow: "筹资活动现金流量净额",
    net_cash_change: "现金及现金等价物净增加额",
    cash_end: "期末现金及现金等价物余额",
    expense_promotion: "推广服务费",
    expense_staff: "销售职工薪酬",
    expense_travel: "差旅费",
    expense_transport: "运输费",
    expense_other: "其他销售费用",
  };

  const VIEW_META = {
    dashboard: { breadcrumb: "财务智能分析工作台", title: "工作台" },
    import: { breadcrumb: "数据导入与校验", title: "数据导入" },
    agent: { breadcrumb: "Agent 任务分析工作区", title: "Agent 工作区" },
    metrics: { breadcrumb: "财务指标分析", title: "指标分析" },
    anomalies: { breadcrumb: "财务异常诊断", title: "异常诊断" },
    scenario: { breadcrumb: "经营情景模拟", title: "情景模拟" },
    report: { breadcrumb: "财务诊断报告", title: "诊断报告" },
    settings: { breadcrumb: "设置与系统状态", title: "设置与系统状态" },
  };

  const state = {
    online: false,
    booted: false,
    busy: false,
    config: null,
    dataset: null,
    analysis: null,
    analyses: [],
    importDataset: null,
    selectedFiles: [],
    rawPreviewIndex: 0,
    currentView: "dashboard",
    anomalyFilter: "all",
    anomalySearch: "",
    drawerFindingId: null,
    lastFocused: null,
    report: null,
    scenario: {
      result: null,
      sensitivity: null,
      analysisId: null,
      params: { revenue_change_pct: 4, cost_rate_change_pp: -1, selling_expense_change_pct: 6 },
      timer: null,
      busy: false,
      error: "",
      seq: 0,
    },
    presentation: { open: false, index: 0, timer: null, lastFocused: null },
    selectedAnalysisId: null,
  };

  let toastTimer = null;
  const toast = $("#toast");

  function showToast(message, type = "info") {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.toggle("is-error", type === "error");
    toast.classList.add("is-visible");
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 4200);
  }

  class ApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.payload = payload;
    }
  }

  async function api(path, options = {}) {
    const opts = {
      cache: "no-store",
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    };
    if (opts.body && !(opts.body instanceof FormData)) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const response = await fetch(path, opts);
    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (_error) {
        payload = text;
      }
    }
    if (!response.ok) {
      const detail =
        payload && typeof payload === "object" && payload.detail
          ? payload.detail
          : typeof payload === "string"
            ? payload
            : `请求失败（HTTP ${response.status}）`;
      throw new ApiError(detail, response.status, payload);
    }
    return payload;
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatNumber(value, digits = 1) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return new Intl.NumberFormat("zh-CN", {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits,
    }).format(Number(value));
  }

  function formatPct(value, digits = 1, signed = false) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    const number = Number(value);
    const sign = signed && number > 0 ? "+" : "";
    return `${sign}${formatNumber(number, digits)}%`;
  }

  function formatPP(value, digits = 1) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    const number = Number(value);
    return `${number > 0 ? "+" : ""}${formatNumber(number, digits)}pp`;
  }

  function metricById(id) {
    return (state.analysis?.metrics || []).find((metric) => metric.id === id) || null;
  }

  function metricValueParts(metric) {
    if (!metric || metric.value === null || metric.value === undefined || metric.status !== "computed") {
      return { value: "—", unit: "" };
    }
    const value = Number(metric.value);
    if (metric.unit === "CNY") return { value: formatNumber(value / 10000, 1), unit: "万" };
    if (metric.unit === "%") return { value: formatNumber(value, 1), unit: "%" };
    if (metric.unit === "天") return { value: formatNumber(value, 1), unit: "天" };
    return { value: formatNumber(value, 2), unit: "x" };
  }

  const BAD_WHEN_UP = new Set([
    "cogs",
    "selling_expenses",
    "accounts_receivable",
    "other_receivables",
    "other_receivables_ratio",
    "ar_turnover_days",
    "working_capital_drag",
    "september_revenue_share",
  ]);
  const GOOD_WHEN_UP = new Set([
    "revenue",
    "gross_profit",
    "gross_margin",
    "net_profit",
    "net_margin",
    "operating_cash_flow",
    "ocf_to_net_profit",
    "inventory",
    "inventory_turnover_days",
  ]);

  function deltaTone(metric) {
    if (!metric || metric.delta === null || metric.delta === undefined || Number(metric.delta) === 0) return "neutral";
    const positive = Number(metric.delta) > 0;
    if (BAD_WHEN_UP.has(metric.id)) return positive ? "bad" : "good";
    if (GOOD_WHEN_UP.has(metric.id)) return positive ? "good" : "bad";
    return "neutral";
  }

  function statusBadge(tone, symbol, text, extraClass = "") {
    return `<span class="status-badge status-${tone} ${extraClass}"><span class="status-symbol">${symbol}</span>${escapeHTML(text)}</span>`;
  }

  function statusTone(status) {
    return {
      created: "neutral",
      running: "info",
      paused: "warning",
      awaiting_review: "warning",
      completed: "good",
      failed: "danger",
      cancelled: "neutral",
      blocked: "danger",
    }[status] || "neutral";
  }

  function statusSymbol(status) {
    return {
      created: "○",
      running: "◐",
      paused: "▲",
      awaiting_review: "▲",
      completed: "✓",
      failed: "■",
      cancelled: "×",
      blocked: "■",
    }[status] || "○";
  }

  function riskTone(level) {
    return level === "high" ? "danger" : level === "medium" ? "warning" : "info";
  }

  function riskLabel(level) {
    return level === "high" ? "高风险" : level === "medium" ? "中风险" : "低风险 / 待补证";
  }

  function riskSymbol(level) {
    return level === "high" ? "■" : level === "medium" ? "▲" : "◇";
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_error) {
      /* fall through to the legacy path */
    }
    try {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.top = "-9999px";
      document.body.appendChild(textarea);
      textarea.select();
      const copied = document.execCommand("copy");
      textarea.remove();
      return copied;
    } catch (_error) {
      return false;
    }
  }

  function downloadBlob(content, filename, type = "text/plain;charset=utf-8") {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  /* ------------------------------------------------------------------------
     Routing / chrome
     ------------------------------------------------------------------------ */

  const mobileQuery = window.matchMedia("(max-width: 1180px)");
  const sidebar = $("#sidebar");
  const sidebarToggle = $("#sidebarToggle");
  const sidebarCollapse = $("#sidebarCollapse");
  const sidebarScrim = $("#sidebarScrim");
  const mainContent = $("#mainContent");

  function routeFromHash() {
    const raw = location.hash.replace(/^#/, "").trim();
    return Object.prototype.hasOwnProperty.call(VIEW_META, raw) ? raw : "dashboard";
  }

  function navigate(route) {
    const next = Object.prototype.hasOwnProperty.call(VIEW_META, route) ? route : "dashboard";
    if (routeFromHash() !== next) location.hash = next;
    renderView(next, { scroll: true, focus: true });
  }

  function setNavigation(route) {
    $$(".nav-item[data-route]").forEach((item) => {
      const active = item.dataset.route === route;
      item.classList.toggle("is-active", active);
      if (active) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    const breadcrumb = $("#currentBreadcrumb");
    if (breadcrumb) breadcrumb.textContent = VIEW_META[route].breadcrumb;
    document.title = `FinGuard · ${VIEW_META[route].title}`;
  }

  function renderView(route, options = {}) {
    state.currentView = route;
    $$(".view").forEach((view) => {
      view.hidden = view.dataset.view !== route;
    });
    setNavigation(route);
    closeMobileSidebar();
    if (options.scroll) window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    if (options.focus && mainContent) mainContent.focus({ preventScroll: true });

    if (!state.online) {
      renderChartsForCurrentView();
      return;
    }

    if (route === "dashboard") renderDashboard();
    if (route === "import") renderImport();
    if (route === "agent") renderAgent();
    if (route === "metrics") renderMetrics();
    if (route === "anomalies") renderAnomalies();
    if (route === "scenario") loadScenarioView();
    if (route === "report") loadReportView();
    if (route === "settings") renderSettings();
    renderChartsForCurrentView();
  }

  function renderCurrentView() {
    renderView(state.currentView, { scroll: false, focus: false });
  }

  function openMobileSidebar() {
    document.body.classList.add("sidebar-open");
    if (sidebarScrim) sidebarScrim.hidden = false;
    if (sidebarToggle) sidebarToggle.setAttribute("aria-expanded", "true");
  }

  function closeMobileSidebar() {
    document.body.classList.remove("sidebar-open");
    if (sidebarScrim) sidebarScrim.hidden = true;
    if (sidebarToggle) sidebarToggle.setAttribute("aria-expanded", "false");
  }

  function toggleSidebar() {
    if (mobileQuery.matches) {
      if (document.body.classList.contains("sidebar-open")) closeMobileSidebar();
      else openMobileSidebar();
      return;
    }
    document.body.classList.toggle("sidebar-collapsed");
    const collapsed = document.body.classList.contains("sidebar-collapsed");
    if (sidebarCollapse) {
      sidebarCollapse.setAttribute("aria-label", collapsed ? "展开侧边栏" : "收起侧边栏");
      const label = $(".nav-label", sidebarCollapse);
      if (label) label.textContent = collapsed ? "展开侧边栏" : "收起侧边栏";
    }
    try {
      localStorage.setItem("finguard-sidebar-collapsed", collapsed ? "1" : "0");
    } catch (_error) {
      /* local file privacy modes may block storage */
    }
    window.setTimeout(() => renderChartsForCurrentView(), 210);
  }

  function updateGlobalChrome() {
    const analysis = state.analysis;
    const config = state.config;
    const badge = $("#runtimeBadge");
    const badgeText = $("#runtimeBadgeText");
    if (state.online) {
      const modelFailed =
        analysis?.model_status?.startsWith("remote_") && analysis.model_status !== "remote_success";
      if (badge) {
        badge.classList.remove("is-offline", "is-live", "is-warn");
        badge.classList.add(modelFailed ? "is-warn" : "is-live");
      }
      if (badgeText) {
        badgeText.textContent = !config?.llm.configured
          ? "本地真实运行 · 无远程模型"
          : modelFailed
            ? "本地计算 · 模型已回退"
            : "本地计算 · 远程叙述";
      }
      if (badge) {
        badge.title = config?.llm?.configured
          ? `${config.llm.data_scope || ""}`
          : "所有计算、规则、报告均由本机 Python 完成。";
      }
    } else {
      if (badge) {
        badge.classList.remove("is-live");
        badge.classList.add("is-offline");
      }
      if (badgeText) badgeText.textContent = "静态预览 · 后端未连接";
    }
    const period = analysis?.period_label || "未加载";
    const topbarPeriod = $("#topbarPeriod");
    if (topbarPeriod) topbarPeriod.textContent = period;
    const workspace = $(".workspace-copy strong");
    if (workspace && analysis) workspace.textContent = `${analysis.company} · 本地演示`;
    const sidebarTitle = $("#sidebarRuntimeTitle");
    const sidebarText = $("#sidebarRuntimeText");
    const sidebarDot = $("#sidebarRuntimeDot");
    if (sidebarTitle && sidebarText && sidebarDot) {
      if (state.online) {
        sidebarTitle.textContent = "本地真实运行";
        sidebarText.textContent = config?.llm?.configured ? "后端计算 · 远程叙述" : "后端计算 · 无远程模型";
        sidebarDot.className = "status-dot";
        sidebarDot.style.background = "var(--success)";
      } else {
        sidebarTitle.textContent = "后端未连接";
        sidebarText.textContent = "静态页面 · 无真实计算";
        sidebarDot.className = "status-dot status-dot-warning";
        sidebarDot.style.background = "";
      }
    }
  }

  function syncAnalysisSelect() {
    const select = $("#analysisSelect");
    if (!select) return;
    const currentId = state.analysis?.id;
    if (!state.analyses.length) {
      select.innerHTML = `<option value="">暂无分析任务</option>`;
      select.disabled = true;
      return;
    }
    select.disabled = !state.online;
    select.innerHTML = state.analyses
      .map(
        (analysis) =>
          `<option value="${escapeHTML(analysis.id)}"${analysis.id === currentId ? " selected" : ""}>${escapeHTML(
            analysis.company
          )} · ${escapeHTML(analysis.period_label || "期间未识别")} · ${escapeHTML(analysis.status_label)}</option>`
      )
      .join("");
  }

  function updateAnalysisSummaryInList(payload) {
    if (!payload?.id) return;
    const summary = {
      id: payload.id,
      company: payload.company,
      dataset_id: payload.dataset_id,
      goal: payload.goal,
      status: payload.status,
      status_label: payload.status_label,
      period_label: payload.period_label,
      updated_at: payload.updated_at,
      progress_done: payload.progress_done,
      progress_total: payload.progress_total,
      finding_count: (payload.findings || []).length,
      pending_count: (payload.pending_questions || []).length,
      report_available: Boolean(payload.report_available),
    };
    const index = state.analyses.findIndex((item) => item.id === payload.id);
    if (index >= 0) state.analyses[index] = summary;
    else state.analyses.unshift(summary);
    state.analyses.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
  }

  function applyAnalysisPayload(payload, options = {}) {
    if (!payload?.id) return;
    const previousId = state.analysis?.id;
    state.analysis = payload;
    state.selectedAnalysisId = payload.id;
    if (payload.dataset) state.dataset = payload.dataset;
    if (previousId && previousId !== payload.id) {
      state.report = null;
      state.scenario.result = null;
      state.scenario.sensitivity = null;
      state.scenario.analysisId = null;
      state.scenario.error = "";
    }
    if (payload.status === "completed") state.report = null;
    updateAnalysisSummaryInList(payload);
    updateGlobalChrome();
    syncAnalysisSelect();
    if (options.render !== false) renderCurrentView();
  }

  async function refreshAnalysesList() {
    try {
      const data = await api("/api/analyses");
      state.analyses = data.items || [];
      syncAnalysisSelect();
      if (state.currentView === "dashboard") renderDashboard();
    } catch (_error) {
      /* keep the last known list */
    }
  }

  /* ------------------------------------------------------------------------
     Charts
     ------------------------------------------------------------------------ */

  function niceMax(value) {
    if (!Number.isFinite(value) || value <= 0) return 10;
    const step = value > 1000 ? 1000 : value > 100 ? 100 : value > 10 ? 10 : 1;
    return Math.ceil(value / step) * step;
  }

  function roundedTopBar(x, y, width, height, radius = 4) {
    const r = Math.max(0, Math.min(radius, width / 2, height));
    if (height <= 0.5) return "";
    return [
      `M${x},${y + height}`,
      `L${x},${y + r}`,
      `Q${x},${y} ${x + r},${y}`,
      `L${x + width - r},${y}`,
      `Q${x + width},${y} ${x + width},${y + r}`,
      `L${x + width},${y + height}`,
      "Z",
    ].join(" ");
  }

  function miniTable(headers, rows) {
    return `<table class="mini-table"><thead><tr>${headers
      .map((header) => `<th scope="col">${escapeHTML(header)}</th>`)
      .join("")}</tr></thead><tbody>${rows
      .map(
        (row) =>
          `<tr>${row
            .map((cell, index) => `<td${index > 0 ? ' class="num"' : ""}>${escapeHTML(cell)}</td>`)
            .join("")}</tr>`
      )
      .join("")}</tbody></table>`;
  }

  function attachChartTooltip(container, tooltip, records) {
    const hits = $$(".chart-hit", container);
    hits.forEach((hit) => {
      const index = Number(hit.dataset.index);
      const record = records[index];
      const show = (event, target) => {
        tooltip.innerHTML = record.tooltip;
        tooltip.classList.add("is-visible");
        const rect = container.getBoundingClientRect();
        let x;
        let y;
        if (event && typeof event.clientX === "number") {
          x = event.clientX - rect.left;
          y = event.clientY - rect.top;
        } else {
          const targetRect = target.getBoundingClientRect();
          x = targetRect.left - rect.left + targetRect.width / 2;
          y = targetRect.top - rect.top;
        }
        const minX = Math.min(100, rect.width / 2);
        tooltip.style.left = `${Math.max(minX, Math.min(rect.width - minX, x))}px`;
        tooltip.style.top = `${Math.max(10, y - 5)}px`;
      };
      hit.addEventListener("mouseenter", (event) => show(event, hit));
      hit.addEventListener("mousemove", (event) => show(event, hit));
      hit.addEventListener("mouseleave", () => tooltip.classList.remove("is-visible"));
      hit.addEventListener("focus", () => show(null, hit));
      hit.addEventListener("blur", () => tooltip.classList.remove("is-visible"));
    });
  }

  function chartEmpty(container, message = "暂无可绘制数据") {
    if (!container) return;
    container.innerHTML = `<div class="chart-empty">${escapeHTML(message)}</div>`;
  }

  function renderBarChart(containerId, tableId, data, options = {}) {
    const container = document.getElementById(containerId);
    const table = document.getElementById(tableId);
    if (!container) return;
    if (!Array.isArray(data) || data.length === 0) {
      chartEmpty(container, options.emptyMessage || "暂无可绘制的收入数据");
      if (table) table.innerHTML = "";
      return;
    }
    const width = Math.max(250, Math.round(container.clientWidth || 620));
    const height = options.height || 200;
    const margin = { top: 20, right: 12, bottom: 30, left: 46 };
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;
    const values = data.map((item) => Number(item.value || 0));
    const max = niceMax(Math.max(...values, 0));
    const band = innerW / data.length;
    const barWidth = Math.min(24, band * 0.45);
    const baseline = margin.top + innerH;
    const yFor = (value) => margin.top + innerH - (value / max) * innerH;
    const grid = [];
    for (let tick = 0; tick <= 4; tick += 1) {
      const y = margin.top + (innerH / 4) * tick;
      const value = max - (max / 4) * tick;
      grid.push(`<line class="grid-line" x1="${margin.left}" y1="${y}" x2="${width - margin.right}" y2="${y}"></line>`);
      grid.push(
        `<text x="${margin.left - 7}" y="${y + 3.5}" text-anchor="end">${formatNumber(
          options.scale ? value / options.scale : value,
          0
        )}</text>`
      );
    }
    const bars = data
      .map((item, index) => {
        const center = margin.left + band * index + band / 2;
        const x = center - barWidth / 2;
        const y = yFor(Number(item.value || 0));
        return `<path class="series-1" d="${roundedTopBar(x, y, barWidth, baseline - y)}"></path>`;
      })
      .join("");
    const labels = data
      .map((item, index) => {
        const center = margin.left + band * index + band / 2;
        return `<text x="${center}" y="${height - 10}" text-anchor="middle">${escapeHTML(item.label)}</text>`;
      })
      .join("");
    const last = data[data.length - 1];
    const lastCenter = margin.left + band * (data.length - 1) + band / 2;
    const lastY = yFor(Number(last.value || 0));
    const endLabel = `<text class="data-label" x="${lastCenter}" y="${lastY - 7}" text-anchor="middle">${escapeHTML(
      options.formatLabel ? options.formatLabel(last.value, true) : String(last.value)
    )}</text>`;
    const hits = data
      .map((item, index) => {
        const x = margin.left + band * index;
        return `<rect class="chart-hit" data-index="${index}" x="${x}" y="${margin.top}" width="${band}" height="${innerH}" tabindex="0" role="img" aria-label="${escapeHTML(
          `${item.label}，${options.seriesName || "数值"} ${options.formatLabel ? options.formatLabel(item.value) : item.value}`
        )}"></rect>`;
      })
      .join("");
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHTML(
      options.ariaLabel || "财务数据柱状图"
    )}">
      <text x="${margin.left}" y="13" class="axis-label">${escapeHTML(options.unitLabel || "单位：万元")}</text>
      ${grid.join("")}${bars}${endLabel}
      <line class="baseline" x1="${margin.left}" y1="${baseline}" x2="${width - margin.right}" y2="${baseline}"></line>
      ${labels}${hits}
    </svg><div class="viz-tooltip" role="status"></div>`;
    const tooltip = $(".viz-tooltip", container);
    attachChartTooltip(
      container,
      tooltip,
      data.map((item) => ({
        tooltip: `<div class="viz-tooltip-title">${escapeHTML(item.label)}</div><div class="viz-tooltip-row"><span class="viz-tooltip-key"><i class="viz-tooltip-swatch swatch-1"></i>${escapeHTML(
          options.seriesName || "数值"
        )}</span><span class="viz-tooltip-value">${escapeHTML(
          options.formatLabel ? options.formatLabel(item.value) : String(item.value)
        )}</span></div>`,
      }))
    );
    if (table) {
      table.innerHTML = miniTable(
        ["期间", options.tableHeader || "数值"],
        data.map((item) => [item.label, options.formatLabel ? options.formatLabel(item.value) : String(item.value)])
      );
    }
  }

  function renderGroupedBarChart(containerId, tableId, data, options = {}) {
    const container = document.getElementById(containerId);
    const table = document.getElementById(tableId);
    if (!container) return;
    if (!Array.isArray(data) || data.length === 0) {
      chartEmpty(container, options.emptyMessage || "暂无可绘制的收入与成本数据");
      if (table) table.innerHTML = "";
      return;
    }
    const width = Math.max(300, Math.round(container.clientWidth || 620));
    const height = options.height || 240;
    const margin = { top: 22, right: 14, bottom: 32, left: 48 };
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;
    const max = niceMax(Math.max(...data.flatMap((item) => [Number(item.revenue || 0), Number(item.cost || 0)])));
    const band = innerW / data.length;
    const barWidth = Math.min(22, (band - 18) / 2);
    const gap = 2;
    const baseline = margin.top + innerH;
    const yFor = (value) => margin.top + innerH - (value / max) * innerH;
    const grid = [];
    for (let tick = 0; tick <= 4; tick += 1) {
      const y = margin.top + (innerH / 4) * tick;
      const value = max - (max / 4) * tick;
      grid.push(`<line class="grid-line" x1="${margin.left}" y1="${y}" x2="${width - margin.right}" y2="${y}"></line>`);
      grid.push(`<text x="${margin.left - 7}" y="${y + 3.5}" text-anchor="end">${formatNumber(value / 10000, 0)}</text>`);
    }
    let bars = "";
    let labels = "";
    const hits = [];
    data.forEach((item, index) => {
      const center = margin.left + band * index + band / 2;
      const x1 = center - barWidth - gap / 2;
      const x2 = center + gap / 2;
      const y1 = yFor(Number(item.revenue || 0));
      const y2 = yFor(Number(item.cost || 0));
      bars += `<path class="series-1" d="${roundedTopBar(x1, y1, barWidth, baseline - y1)}"></path>`;
      bars += `<path class="series-2" d="${roundedTopBar(x2, y2, barWidth, baseline - y2)}"></path>`;
      if (index === data.length - 1) {
        labels += `<text class="data-label" x="${x1 + barWidth / 2}" y="${y1 - 7}" text-anchor="middle">${formatNumber(
          Number(item.revenue || 0) / 10000,
          0
        )}</text>`;
      }
      hits.push(
        `<rect class="chart-hit" data-index="${index}" x="${center - band / 2}" y="${margin.top}" width="${band}" height="${innerH}" tabindex="0" role="img" aria-label="${escapeHTML(
          `${item.label}，营业收入 ${formatNumber(Number(item.revenue || 0) / 10000, 0)} 万元，营业成本 ${formatNumber(
            Number(item.cost || 0) / 10000,
            0
          )} 万元`
        )}"></rect>`
      );
    });
    const axisLabels = data
      .map((item, index) => `<text x="${margin.left + band * index + band / 2}" y="${height - 10}" text-anchor="middle">${escapeHTML(item.label)}</text>`)
      .join("");
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHTML(
      options.ariaLabel || "营业收入与营业成本分组柱状图"
    )}">
      <text x="${margin.left}" y="14" class="axis-label">单位：万元</text>
      ${grid.join("")}${bars}${labels}
      <line class="baseline" x1="${margin.left}" y1="${baseline}" x2="${width - margin.right}" y2="${baseline}"></line>
      ${axisLabels}${hits.join("")}
    </svg><div class="viz-tooltip" role="status"></div>`;
    const tooltip = $(".viz-tooltip", container);
    attachChartTooltip(
      container,
      tooltip,
      data.map((item) => ({
        tooltip: `<div class="viz-tooltip-title">${escapeHTML(item.label)}</div>
          <div class="viz-tooltip-row"><span class="viz-tooltip-key"><i class="viz-tooltip-swatch swatch-1"></i>营业收入</span><span class="viz-tooltip-value">${formatNumber(Number(item.revenue) / 10000, 1)} 万</span></div>
          <div class="viz-tooltip-row"><span class="viz-tooltip-key"><i class="viz-tooltip-swatch swatch-2"></i>营业成本</span><span class="viz-tooltip-value">${formatNumber(Number(item.cost) / 10000, 1)} 万</span></div>`,
      }))
    );
    if (table) {
      table.innerHTML = miniTable(
        ["期间", "营业收入（万元）", "营业成本（万元）"],
        data.map((item) => [
          item.label,
          formatNumber(Number(item.revenue) / 10000, 1),
          formatNumber(Number(item.cost) / 10000, 1),
        ])
      );
    }
  }

  function renderLineChart(containerId, tableId, data, options = {}) {
    const container = document.getElementById(containerId);
    const table = document.getElementById(tableId);
    if (!container) return;
    if (!Array.isArray(data) || data.length === 0) {
      chartEmpty(container, options.emptyMessage || "暂无可绘制的比率数据");
      if (table) table.innerHTML = "";
      return;
    }
    const values = data.map((item) => Number(item.value || 0));
    const width = Math.max(250, Math.round(container.clientWidth || 620));
    const height = options.height || 200;
    const margin = { top: 24, right: 46, bottom: 30, left: 44 };
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;
    const yMin = Math.floor(Math.min(...values) - 1);
    const yMax = Math.ceil(Math.max(...values) + 1);
    const span = yMax - yMin || 2;
    const baseline = margin.top + innerH;
    const xFor = (index) => (data.length === 1 ? margin.left + innerW / 2 : margin.left + (innerW / (data.length - 1)) * index);
    const yFor = (value) => margin.top + ((yMax - value) / span) * innerH;
    const grid = [];
    for (let tick = 0; tick <= 4; tick += 1) {
      const value = yMax - (span / 4) * tick;
      const y = yFor(value);
      grid.push(`<line class="grid-line" x1="${margin.left}" y1="${y}" x2="${width - margin.right}" y2="${y}"></line>`);
      grid.push(`<text x="${margin.left - 7}" y="${y + 3.5}" text-anchor="end">${formatNumber(value, 1)}%</text>`);
    }
    const points = data.map((item, index) => [xFor(index), yFor(Number(item.value || 0))]);
    const linePath = points.map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
    const areaPath =
      points.length > 1
        ? `${linePath} L${points[points.length - 1][0].toFixed(1)},${baseline} L${points[0][0].toFixed(1)},${baseline} Z`
        : "";
    const dots = points
      .map(([x, y]) => `<circle class="data-dot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4"></circle>`)
      .join("");
    const last = data[data.length - 1];
    const [lastX, lastY] = points[points.length - 1];
    const endLabel = `<text class="data-label" x="${lastX}" y="${lastY - 9}" text-anchor="end">${formatNumber(
      Number(last.value || 0),
      1
    )}%</text>`;
    const xLabels = data
      .map((item, index) => `<text x="${xFor(index)}" y="${height - 10}" text-anchor="middle">${escapeHTML(item.label)}</text>`)
      .join("");
    const hits = data
      .map((item, index) => {
        const [x, y] = points[index];
        return `<circle class="chart-hit" data-index="${index}" cx="${x}" cy="${y}" r="13" tabindex="0" role="img" aria-label="${escapeHTML(
          `${item.label}，毛利率 ${formatNumber(item.value, 1)}%`
        )}"></circle>`;
      })
      .join("");
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHTML(
      options.ariaLabel || "毛利率折线图"
    )}">
      ${grid.join("")}${areaPath ? `<path class="area-1" d="${areaPath}"></path>` : ""}
      <path class="line-1" d="${linePath}"></path>${dots}${endLabel}
      <line class="baseline" x1="${margin.left}" y1="${baseline}" x2="${width - margin.right}" y2="${baseline}"></line>
      ${xLabels}${hits}
    </svg><div class="viz-tooltip" role="status"></div>`;
    const tooltip = $(".viz-tooltip", container);
    attachChartTooltip(
      container,
      tooltip,
      data.map((item) => ({
        tooltip: `<div class="viz-tooltip-title">${escapeHTML(item.label)}</div><div class="viz-tooltip-row"><span class="viz-tooltip-key"><i class="viz-tooltip-swatch swatch-1"></i>${escapeHTML(
          options.seriesName || "毛利率"
        )}</span><span class="viz-tooltip-value">${formatNumber(item.value, 1)}%</span></div>`,
      }))
    );
    if (table) {
      table.innerHTML = miniTable(
        ["期间", options.tableHeader || "毛利率"],
        data.map((item) => [item.label, `${formatNumber(item.value, 1)}%`])
      );
    }
  }

  function renderDashboardCharts() {
    const analysis = state.analysis;
    if (!analysis) return;
    const charts = analysis.charts || {};
    const shortPeriod = (item) => {
      const raw = item.period || item.label || "";
      const month = /^(\d{4})-(\d{2})$/.exec(raw);
      if (month) return `${month[1].slice(2)}-${month[2]}`;
      return item.label || raw;
    };
    const monthly = (charts.monthly_revenue || []).map((item) => ({ ...item, label: shortPeriod(item) }));
    const margin = charts.margin || [];
    renderBarChart("dashboardRevenueChart", "dashboardRevenueTable", monthly, {
      height: 188,
      scale: 10000,
      unitLabel: "单位：万元",
      seriesName: "营业收入",
      tableHeader: "营业收入（万元）",
      formatLabel: (value) => `${formatNumber(Number(value) / 10000, 0)} 万`,
      ariaLabel: "最近可比季度各月营业收入柱状图",
      emptyMessage: "当前数据未提供月度收入，无法绘制月度趋势。",
    });
    renderLineChart("dashboardMarginChart", "dashboardMarginTable", margin, {
      height: 188,
      seriesName: "毛利率",
      tableHeader: "毛利率",
      ariaLabel: "本期与上年同期毛利率对比",
      emptyMessage: "当前数据不足以计算毛利率。",
    });
    const revenue = metricById("revenue");
    const gross = metricById("gross_margin");
    const revenueHead = $("#dashboardChartCard .mini-chart:nth-child(1) .mini-chart-head strong");
    const marginHead = $("#dashboardChartCard .mini-chart:nth-child(2) .mini-chart-head strong");
    if (revenueHead) revenueHead.textContent = revenue?.display?.value || "—";
    if (marginHead) marginHead.textContent = gross?.display?.value || "—";
    const kicker = $("#dashboardChartKicker");
    const title = $("#dashboardChartTitle");
    if (kicker) kicker.textContent = analysis.dataset?.is_demo ? "真实计算 · 演示数据" : "真实计算 · 本地导入数据";
    if (title) title.textContent = "收入与毛利率：两期对比";
    const note = $("#dashboardChartNote");
    if (note) {
      const share = metricById("september_revenue_share");
      const gm = metricById("gross_margin");
      note.textContent = share?.value != null
        ? `${share.label}：${formatNumber(share.value, 1)}%，较上年同期 ${formatPP(share.delta)}；月度收入来自原始数据。`
        : gm?.value != null
          ? `毛利率 ${formatNumber(gm.value, 1)}%，同比 ${formatPP(gm.delta)}；趋势图仅使用数据中实际存在的期间。`
          : "当前数据不足以生成趋势解释。";
    }
  }

  function renderMetricsCharts() {
    const analysis = state.analysis;
    if (!analysis) return;
    const charts = analysis.charts || {};
    renderGroupedBarChart("metricRevenueCostChart", "metricRevenueCostTable", charts.revenue_cost || [], {
      height: 258,
      ariaLabel: "本期与上年同期营业收入和营业成本分组柱状图",
      emptyMessage: "缺少收入或成本，无法绘制对比图。",
    });
    renderLineChart("metricMarginChart", "metricMarginTable", charts.margin || [], {
      height: 258,
      seriesName: "毛利率",
      tableHeader: "毛利率",
      ariaLabel: "本期与上年同期毛利率对比",
      emptyMessage: "缺少收入或成本，无法计算毛利率。",
    });
    const cards = $$("#view-metrics .chart-card");
    if (cards[0]) {
      const title = $(".card-title", cards[0]);
      const note = $(".chart-note", cards[0]);
      if (title) title.textContent = "营业收入与营业成本：两期对比";
      if (note) note.textContent = "所有金额以原始报表为准；仅展示数据中存在的两个可比期间，不补造季度数据。";
    }
    if (cards[1]) {
      const title = $(".card-title", cards[1]);
      const note = $(".chart-note", cards[1]);
      if (title) title.textContent = "毛利率同比变化";
      if (note) note.textContent = "毛利率由同一套程序计算；当收入或成本缺失时显示不可计算。";
    }
  }

  function renderChartsForCurrentView() {
    if (!state.online) return;
    if (state.currentView === "dashboard") renderDashboardCharts();
    if (state.currentView === "metrics") renderMetricsCharts();
  }

  /* ------------------------------------------------------------------------
     Dashboard, metrics, anomalies, settings
     ------------------------------------------------------------------------ */

  function kpiCard(metric, noteOverride = "") {
    if (!metric) {
      return `<article class="kpi-card"><div class="kpi-head"><span>指标不可用</span></div><div class="kpi-value">—</div><p class="kpi-note">等待分析运行</p></article>`;
    }
    const parts = metricValueParts(metric);
    const tone = deltaTone(metric);
    const delta = metric.display?.delta || "—";
    const arrow = metric.delta == null ? "" : Number(metric.delta) > 0 ? "↑ " : Number(metric.delta) < 0 ? "↓ " : "";
    const status =
      metric.status === "computed"
        ? `<span class="delta delta-${tone === "bad" ? "bad" : tone === "good" ? "good" : "neutral"}">${arrow}${escapeHTML(delta)}</span>`
        : statusBadge("neutral", "○", "不可计算");
    const note =
      noteOverride ||
      (metric.status === "computed"
        ? `上年同期 ${escapeHTML(metric.display?.prior || "—")} · ${escapeHTML(metric.note || "后端计算")}`
        : escapeHTML(metric.limitation || "缺少可追溯数据，未补 0"));
    return `<article class="kpi-card" data-kpi="${escapeHTML(metric.id)}">
      <div class="kpi-head"><span>${escapeHTML(metric.label)}</span><span class="kpi-period">${escapeHTML(metric.period || "")}</span></div>
      <div class="kpi-value">${escapeHTML(parts.value)}${parts.unit ? `<span class="unit">${escapeHTML(parts.unit)}</span>` : ""}</div>
      <div class="kpi-foot">${status}<span class="kpi-compare">${escapeHTML(metric.display?.prior || "")}</span></div>
      <p class="kpi-note">${note}</p>
    </article>`;
  }

  function renderDashboard() {
    const analysis = state.analysis;
    const dataset = state.dataset;
    if (!analysis) return;
    updateGlobalChrome();
    const company = analysis.company || dataset?.company || "未命名企业";
    const mark = $("#dashboardEntityMark");
    const name = $("#dashboardEntityName");
    const sub = $("#dashboardEntitySub");
    const period = $("#dashboardPeriod");
    const files = $("#dashboardFiles");
    const updated = $("#dashboardUpdated");
    const status = $("#dashboardStatusBadge");
    if (mark) mark.textContent = company.slice(0, 1);
    if (name) name.textContent = company;
    if (sub) sub.textContent = dataset?.is_demo ? "虚构演示主体 · 本地真实计算" : "本地导入数据 · 严格按来源计算";
    if (period) period.textContent = analysis.period_label || "期间未识别";
    if (files) files.textContent = `${(dataset?.files || []).length} 份 · ${(dataset?.facts_count || 0)} 条记录`;
    if (updated) updated.textContent = formatDate(analysis.updated_at);
    if (status) {
      status.className = `status-badge status-${statusTone(analysis.status)}`;
      status.innerHTML = `<span class="status-symbol">${statusSymbol(analysis.status)}</span>${escapeHTML(analysis.status_label)}`;
    }
    const subtitle = $("#dashboardSubtitle");
    if (subtitle) {
      subtitle.textContent = dataset?.is_demo
        ? "演示公司为虚构主体；数据来自本地演示 CSV，所有指标、规则与证据由后端真实计算。"
        : "数据来自本地导入文件；所有指标、规则与证据由后端真实计算，并保留来源与限制。";
    }
    const recentFooter = $("#recentFooter");
    if (recentFooter) recentFooter.textContent = "点击任务可切换并读取真实后端执行状态；演示分析与上传分析分开保存。";

    const kpiGrid = $("#dashboardKpiGrid");
    if (kpiGrid) {
      kpiGrid.innerHTML = ["revenue", "cogs", "gross_margin", "net_profit", "operating_cash_flow"]
        .map((id) => kpiCard(metricById(id)))
        .join("");
    }

    renderDashboardAnalysisCard();
    renderDashboardRisk();
    renderDashboardRecent();
    renderDashboardCharts();
    const reportBtn = $("#dashboardReportBtn");
    if (reportBtn) {
      reportBtn.disabled = !analysis.report_available;
      reportBtn.title = analysis.report_available ? "查看由当前分析生成的报告" : "报告尚未生成，请先完成证据核验与报告步骤";
    }
  }

  function renderDashboardAnalysisCard() {
    const card = $("#dashboardAnalysisCard");
    const analysis = state.analysis;
    if (!card || !analysis) return;
    const steps = analysis.steps || [];
    const done = analysis.progress_done || 0;
    const total = analysis.progress_total || steps.length || 1;
    const percent = Math.round((done / total) * 100);
    const riskCounts = analysis.risk_counts || { high: 0, medium: 0, low: 0 };
    const metricsComputed = (analysis.metrics || []).filter((metric) => metric.status === "computed").length;
    const pauseText =
      analysis.pause_reason ||
      (analysis.status === "completed"
        ? "报告草稿已生成，仍待人工复核；导出前请阅读分析限制。"
        : analysis.status === "blocked"
          ? "数据存在阻塞问题，请补充或修正源数据后创建新分析。"
          : "分析尚未完成，打开工作区可查看真实执行步骤。"
      );
    const progressRows = steps
      .map((step) => {
        const tone =
          step.status === "completed" ? "is-complete" : step.status === "waiting_review" || step.status === "failed" ? "is-warning" : "";
        const marker =
          step.status === "completed"
            ? '<svg class="icon icon-xs" aria-hidden="true"><use href="#i-check"></use></svg>'
            : step.status === "waiting_review" || step.status === "failed"
              ? '<span class="status-symbol">▲</span>'
              : '<span class="status-symbol">○</span>';
        return `<li class="${tone}"><span class="progress-marker">${marker}</span><div><strong>${escapeHTML(step.title)}</strong><span>${escapeHTML(step.tool)} · ${escapeHTML(step.status_label)}</span></div><em>${escapeHTML(step.status_label)}</em></li>`;
      })
      .join("");
    card.innerHTML = `<div class="card-header">
      <div>
        <div class="card-kicker">当前分析 · ${escapeHTML(analysis.id)}</div>
        <h2 class="card-title">利润背离与现金流质量诊断</h2>
      </div>
      ${statusBadge(statusTone(analysis.status), statusSymbol(analysis.status), analysis.status_label)}
    </div>
    <div class="current-analysis-body">
      <div class="analysis-summary">
        <p class="label-small">分析目标</p>
        <p class="analysis-goal">“${escapeHTML(analysis.goal)}”</p>
        <div class="analysis-facts">
          <div><strong>${riskCounts.total ?? (analysis.findings || []).length}</strong><span>规则结果</span></div>
          <div><strong>${analysis.evidence_count ?? (analysis.evidence || []).length}</strong><span>可追溯证据</span></div>
          <div><strong>${metricsComputed}</strong><span>已计算指标</span></div>
        </div>
        <p class="analysis-note"><span class="inline-warning">${analysis.status === "paused" ? "暂停点" : analysis.status === "completed" ? "复核点" : "状态"}</span> ${escapeHTML(pauseText)}</p>
        <div class="inline-actions">
          <button class="button button-primary button-sm" type="button" data-route="agent">打开 Agent 工作区 <svg class="icon icon-sm" aria-hidden="true"><use href="#i-arrow-right"></use></svg></button>
          ${analysis.report_available ? '<button class="button button-ghost button-sm" type="button" data-route="report">查看报告</button>' : '<button class="button button-ghost button-sm" type="button" data-route="anomalies">查看异常</button>'}
        </div>
      </div>
      <div class="progression">
        <div class="progression-head"><span>真实执行进度</span><strong>${done} / ${total} 步</strong></div>
        <div class="progress-track" role="progressbar" aria-label="分析执行进度" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${done}">
          <span class="progress-fill" style="width: ${percent}%"></span>
        </div>
        <ol class="progress-list">${progressRows}</ol>
      </div>
    </div>`;
  }

  function renderDashboardRisk() {
    const analysis = state.analysis;
    if (!analysis) return;
    const counts = analysis.risk_counts || { high: 0, medium: 0, low: 0 };
    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };
    set("dashboardRiskHigh", counts.high || 0);
    set("dashboardRiskMedium", counts.medium || 0);
    set("dashboardRiskLow", counts.low || 0);
    const list = $("#dashboardAnomalyList");
    if (!list) return;
    const findings = (analysis.findings || []).slice(0, 3);
    if (!findings.length) {
      list.innerHTML = `<div class="empty-state compact">当前分析尚未生成异常结果。打开 Agent 工作区执行规则检查。</div>`;
      return;
    }
    list.innerHTML = findings
      .map(
        (finding) => `<button class="anomaly-mini" type="button" data-anomaly-open="${escapeHTML(finding.id)}">
          <span class="risk-pill risk-${escapeHTML(finding.risk_level)}"><span class="status-symbol">${riskSymbol(finding.risk_level)}</span>${finding.risk_level === "high" ? "高" : finding.risk_level === "medium" ? "中" : "待补证"}</span>
          <span class="anomaly-mini-copy"><strong>${escapeHTML(finding.title)}</strong><span>${escapeHTML(finding.rule_id)} · ${escapeHTML(finding.metric_value)}</span></span>
          <svg class="icon icon-sm" aria-hidden="true"><use href="#i-chevron-right"></use></svg>
        </button>`
      )
      .join("");
  }

  function renderDashboardRecent() {
    const list = $("#recentAnalysisList");
    if (!list) return;
    if (!state.analyses.length) {
      list.innerHTML = `<div class="empty-state compact">暂无分析任务。</div>`;
      return;
    }
    list.innerHTML = state.analyses
      .slice(0, 5)
      .map(
        (analysis) => `<button class="recent-row" type="button" data-analysis-id="${escapeHTML(analysis.id)}">
          <span class="recent-status ${analysis.status === "completed" ? "status-good" : analysis.status === "failed" || analysis.status === "blocked" ? "status-danger" : "status-warning"}"><span class="status-symbol">${statusSymbol(analysis.status)}</span></span>
          <span class="recent-copy"><strong>${escapeHTML(analysis.company)}</strong><span>${escapeHTML(analysis.id)} · ${formatDate(analysis.updated_at)}</span></span>
          <span class="status-badge status-${statusTone(analysis.status)}">${escapeHTML(analysis.status_label)}</span>
        </button>`
      )
      .join("");
  }

  function renderMetrics() {
    const analysis = state.analysis;
    if (!analysis) return;
    const priorLabel = analysis.prior_period || "上年同期";
    const currentLabel = analysis.period_label || "本期";
    const computed = (analysis.metrics || []).filter((metric) => metric.status === "computed").length;
    const missing = (analysis.metrics || []).length - computed;
    const dataset = state.dataset;
    const subtitle = $("#metricsSubtitle");
    if (subtitle) subtitle.textContent = "统一收入、成本、利润与现金流的口径。图表只使用当前数据集中实际存在的期间，不补造期间。";
    const context = $("#metricsContext");
    if (context) {
      context.innerHTML = `<span><strong>口径：</strong>${escapeHTML(dataset?.source_label || "本地数据")}</span>
        <span><strong>本期：</strong>${escapeHTML(currentLabel)}</span>
        <span><strong>对比：</strong>${escapeHTML(priorLabel)}</span>
        <span><strong>期间天数：</strong>${escapeHTML(analysis.period_days ? `${analysis.period_days} 天` : "—")}</span>
        ${missing ? statusBadge("warning", "▲", `${missing} 项不可计算`) : statusBadge("good", "✓", "指标可计算")}`;
    }
    const kpiGrid = $("#metricsKpiGrid");
    if (kpiGrid) {
      kpiGrid.innerHTML = ["revenue", "cogs", "gross_margin", "net_profit", "operating_cash_flow", "inventory_turnover_days"]
        .map((id) => kpiCard(metricById(id)))
        .join("");
    }
    const thead = $("#view-metrics .metrics-table thead");
    if (thead) {
      thead.innerHTML = `<tr><th scope="col">指标</th><th scope="col">${escapeHTML(priorLabel)}</th><th scope="col">${escapeHTML(
        currentLabel
      )}</th><th scope="col">变化</th><th scope="col">计算依据 / 口径</th><th scope="col">状态</th></tr>`;
    }
    const tbody = $("#metricsTableBody");
    if (tbody) {
      tbody.innerHTML = (analysis.metrics || [])
        .map((metric) => {
          const display = metric.display || {};
          const badge = metric.status === "computed"
            ? statusBadge("good", "✓", "可计算")
            : statusBadge("neutral", "○", metric.status_label || "不可计算");
          const secondary = metric.note ? `<span class="cell-secondary">${escapeHTML(metric.note)}</span>` : "";
          return `<tr>
            <td><span class="cell-primary">${escapeHTML(metric.label)}</span>${secondary}</td>
            <td class="num">${escapeHTML(display.prior || "—")}</td>
            <td class="num">${escapeHTML(display.value || "—")}</td>
            <td>${escapeHTML(display.delta || "—")}</td>
            <td class="formula">${escapeHTML(metric.formula || "—")}</td>
            <td>${badge}</td>
          </tr>`;
        })
        .join("");
    }
    renderMetricsCharts();
  }

  function renderAnomalies() {
    const analysis = state.analysis;
    if (!analysis) return;
    const findings = analysis.findings || [];
    const subtitle = $("#anomaliesSubtitle");
    if (subtitle) subtitle.textContent = "每条发现都保留触发规则、指标、期间、证据与限制；单期信号和证据不足不会被写成确定结论。";
    const counts = analysis.risk_counts || { high: 0, medium: 0, low: 0, total: findings.length };
    const set = (id, value) => {
      const element = document.getElementById(id);
      if (element) element.textContent = value;
    };
    set("riskCountHigh", counts.high || 0);
    set("riskCountMedium", counts.medium || 0);
    set("riskCountLow", counts.low || 0);
    set("riskCountTotal", counts.total ?? findings.length);
    $$(".filter-chip[data-risk-filter]").forEach((button) => {
      const filter = button.dataset.riskFilter;
      const count = filter === "all" ? findings.length : findings.filter((finding) => finding.risk_level === filter).length;
      const span = $("span", button);
      if (span) span.textContent = count;
      button.classList.toggle("is-active", state.anomalyFilter === filter);
    });
    const query = state.anomalySearch.toLowerCase().trim();
    const filtered = findings.filter((finding) => {
      const matchesFilter = state.anomalyFilter === "all" || finding.risk_level === state.anomalyFilter;
      const text = [finding.id, finding.title, finding.rule_id, finding.rule_name, finding.metric_value, finding.evidence_summary]
        .join(" ")
        .toLowerCase();
      return matchesFilter && (!query || text.includes(query));
    });
    const tbody = $("#anomalyRows");
    if (tbody) {
      if (!filtered.length) {
        tbody.innerHTML = `<tr class="empty-row"><td colspan="7">没有匹配当前筛选条件的异常。尝试清除搜索或选择“全部”。</td></tr>`;
      } else {
        tbody.innerHTML = filtered
          .map(
            (finding) => `<tr>
              <td><div class="anomaly-title-cell"><strong>${escapeHTML(finding.title)}</strong><span>${escapeHTML(finding.id)} · ${escapeHTML(finding.status_label)}</span></div></td>
              <td><span class="risk-pill risk-${escapeHTML(finding.risk_level)}"><span class="status-symbol">${riskSymbol(finding.risk_level)}</span>${escapeHTML(riskLabel(finding.risk_level))}</span></td>
              <td><div class="rule-cell"><span class="mono">${escapeHTML(finding.rule_id)}</span><span>${escapeHTML(finding.rule_name)}</span></div></td>
              <td><div class="anomaly-metric"><strong>${escapeHTML(finding.metric_value)}</strong><span>${escapeHTML(finding.metric_sub)}</span></div></td>
              <td class="period-cell">${escapeHTML(finding.period)}</td>
              <td><div class="evidence-summary">${escapeHTML(finding.evidence_summary)}<span class="recommend-short">建议：${escapeHTML(finding.recommendation)}</span></div></td>
              <td><button class="row-open-button" type="button" data-anomaly-open="${escapeHTML(finding.id)}" aria-label="查看 ${escapeHTML(finding.title)} 详情">查看详情 <svg class="icon icon-xs" aria-hidden="true"><use href="#i-chevron-right"></use></svg></button></td>
            </tr>`
          )
          .join("");
      }
    }
    set("anomalyResultCount", `显示 ${filtered.length} 条`);
  }

  function renderSettings() {
    const config = state.config;
    const analysis = state.analysis;
    if (!config) return;
    const subtitle = $("#settingsSubtitle");
    if (subtitle) subtitle.textContent = "这里展示本地后端、Agent 与模型配置的真实状态。未配置的服务不会伪装成已连接。";
    const statusBody = $("#systemStatusBody");
    if (statusBody) {
      const agentStatus = analysis
        ? `<span class="status-badge status-${statusTone(analysis.status)}"><span class="status-symbol">${statusSymbol(analysis.status)}</span>${escapeHTML(analysis.status_label)}</span>`
        : statusBadge("neutral", "○", "无分析任务");
      const llmStatus = config.llm?.configured
        ? statusBadge("info", "◇", "已配置 · 未验证")
        : statusBadge("neutral", "○", "未配置");
      statusBody.innerHTML = `<tr><td><span class="cell-primary">运行模式</span></td><td>${statusBadge("info", "●", "本地真实计算")}</td><td>FastAPI 后端运行中；不是静态原型</td></tr>
        <tr><td><span class="cell-primary">Agent 运行时</span></td><td>${agentStatus}</td><td>分析任务 ${escapeHTML(analysis?.id || "—")}</td></tr>
        <tr><td><span class="cell-primary">模型连接</span></td><td>${llmStatus}</td><td>${escapeHTML(config.llm?.note || "")}</td></tr>
        <tr><td><span class="cell-primary">登录状态</span></td><td>${statusBadge("neutral", "○", "本地演示")}</td><td>未接入 Supabase Auth，不是生产级身份认证</td></tr>
        <tr><td><span class="cell-primary">数据存储</span></td><td>${statusBadge("info", "●", "本机文件系统")}</td><td>上传文件与运行状态保存在本机；未使用云数据库</td></tr>
        <tr><td><span class="cell-primary">PDF 支持</span></td><td>${statusBadge("warning", "▲", "基础文本型")}</td><td>扫描件与复杂版面需 OCR 或人工录入</td></tr>
        <tr><td><span class="cell-primary">远程数据范围</span></td><td>${config.llm?.configured ? statusBadge("warning", "▲", "按需发送摘要") : statusBadge("neutral", "○", "未发送")}</td><td>${escapeHTML(config.llm?.data_scope || "")}</td></tr>`;
    }
    const cfgBase = $("#cfgBase");
    const cfgModel = $("#cfgModel");
    const cfgKey = $("#cfgKey");
    if (cfgBase) cfgBase.value = config.llm?.configured ? config.llm.base_host || "服务端已配置" : "未配置（本地模式）";
    if (cfgModel) cfgModel.value = config.llm?.configured ? config.llm.model || "已配置" : "未配置";
    if (cfgKey) cfgKey.value = config.llm?.api_key_configured ? "server-side-secret" : "未配置";
    const configNote = $(".config-note");
    if (configNote) configNote.textContent = config.llm?.configured
      ? `远程模型已配置。${config.llm.data_scope} 密钥只在服务端，前端不可见。`
      : "未配置远程模型。所有指标计算、规则检查、报告和叙述均在本地完成。";
    const testLlmBtn = $("#testLlmBtn");
    if (testLlmBtn) {
      testLlmBtn.disabled = !state.online || !config.llm?.configured;
      testLlmBtn.title = config.llm?.configured
        ? "向配置的模型服务发送一个不含财务数据的 OK 测试请求"
        : "请先在 .env 中配置 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL";
    }
    const phaseList = $(".phase-list");
    if (phaseList) {
      phaseList.innerHTML = `<div class="phase-row is-done"><span></span><strong>数据接入与确定性分析</strong><em>已完成</em></div>
        <div class="phase-row is-done"><span></span><strong>Agent 运行时与证据链</strong><em>已完成</em></div>
        <div class="phase-row is-done"><span></span><strong>报告、投影与讲解模式</strong><em>已完成</em></div>
        <div class="phase-row is-current"><span></span><strong>本地演示验证</strong><em>进行中</em></div>
        <div class="phase-row"><span></span><strong>Supabase Auth 与云部署</strong><em>未启用</em></div>`;
    }
    const phaseBadge = $("#view-settings .page-actions .status-badge");
    if (phaseBadge) {
      phaseBadge.className = "status-badge status-info";
      phaseBadge.textContent = "本地真实运行";
    }
  }

  /* ------------------------------------------------------------------------
     Agent workspace
     ------------------------------------------------------------------------ */

  function planTone(status) {
    if (status === "completed" || status === "skipped") return "complete";
    if (status === "waiting_review" || status === "failed" || status === "running") return "warning";
    return "waiting";
  }

  function renderAgent() {
    const analysis = state.analysis;
    if (!analysis) return;
    updateGlobalChrome();
    syncAnalysisSelect();
    const steps = analysis.steps || [];
    const dataset = state.dataset;
    const currentStep = analysis.current_step || {};
    const counts = analysis.risk_counts || { high: 0, medium: 0, low: 0 };
    const config = state.config;

    const title = $("#agentPageTitle");
    if (title) title.textContent = "利润背离与现金流质量";
    const subtitle = $("#agentPageSubtitle");
    if (subtitle) subtitle.textContent = `当前企业：${analysis.company}。页面数据来自本地后端；计划、工具参数、证据和状态均可追溯。`;
    const runLabel = $("#agentRunLabel");
    const runMeta = $("#agentRunMeta");
    if (runLabel) runLabel.textContent = analysis.id;
    if (runMeta) runMeta.textContent = analysis.id;
    const goal = $("#agentGoal");
    if (goal) goal.textContent = `“${analysis.goal}”`;
    const companyMeta = $("#agentCompanyMeta");
    if (companyMeta) companyMeta.textContent = analysis.company;
    const periodMeta = $("#agentPeriodMeta");
    if (periodMeta) periodMeta.textContent = `${analysis.period_label || "—"} / 对比 ${analysis.prior_period || "—"}`;

    const previewTitle = $("#agentPreviewTitle");
    const previewText = $("#agentPreviewText");
    const previewBadge = $("#agentPreviewBadge");
    if (previewTitle) previewTitle.textContent = state.online ? "以下内容来自真实后端执行记录" : "后端未连接，当前不是可执行分析";
    if (previewText) {
      previewText.textContent = state.online
        ? `所有指标由 Python 计算；当前最后事件：${currentStep.title || "分析已载入"}。工具状态、耗时、错误和证据均来自当前分析。`
        : "请先运行 uv run finguard，页面才能读取真实分析状态。";
    }
    if (previewBadge) previewBadge.textContent = state.online ? "LIVE" : "OFFLINE";

    const statusText = $("#agentStatusText");
    if (statusText) statusText.textContent = analysis.status_label;
    const statusBadgeEl = $("#agentStatusBadge");
    if (statusBadgeEl) {
      statusBadgeEl.className = `status-badge status-${statusTone(analysis.status)}`;
      statusBadgeEl.innerHTML = `<span class="status-symbol">${statusSymbol(analysis.status)}</span>${escapeHTML(analysis.status_label)}`;
    }
    const periodText = $("#agentPeriodText");
    const periodNote = $("#agentPeriodNote");
    if (periodText) periodText.textContent = analysis.period_label || "—";
    if (periodNote) periodNote.textContent = `${(dataset?.files || []).length} 份文件 · ${(analysis.metrics || []).length} 项指标`;
    const evidenceCoverage = $("#agentEvidenceCoverage");
    const evidenceNote = $("#agentEvidenceNote");
    if (evidenceCoverage) evidenceCoverage.textContent = String(analysis.evidence_count ?? (analysis.evidence || []).length);
    if (evidenceNote) evidenceNote.textContent = `${analysis.pending_count ?? (analysis.pending_questions || []).length} 项待核验 · ${(analysis.findings || []).length} 条规则结果`;
    const modeText = $("#agentModeText");
    const modeNote = $("#agentModeNote");
    if (modeText) modeText.textContent = config?.llm?.configured ? "本地计算 + 远程叙述" : "本地确定性运行";
    if (modeNote) {
      modeNote.textContent = analysis.model_status === "remote_success"
        ? "远程叙述已校验"
        : analysis.model_status?.startsWith("remote_")
          ? "模型调用失败，已回退"
          : config?.llm?.configured
            ? "远程模型已配置"
            : "无远程模型 · 无外部请求";
    }

    const progressText = $("#agentProgressText");
    const progressBar = $("#agentProgressBar");
    if (progressText) progressText.textContent = `${analysis.progress_done} / ${analysis.progress_total} 步完成`;
    if (progressBar) {
      progressBar.setAttribute("aria-valuemax", String(analysis.progress_total || 8));
      progressBar.setAttribute("aria-valuenow", String(analysis.progress_done || 0));
      const fill = $(".progress-fill", progressBar);
      if (fill) fill.style.width = `${analysis.progress_total ? (analysis.progress_done / analysis.progress_total) * 100 : 0}%`;
    }
    const findingsCount = $("#agentFindingCount");
    if (findingsCount) findingsCount.textContent = `${(analysis.findings || []).length} 条规则结果`;
    const planBadge = $("#agentPlanBadge");
    if (planBadge) planBadge.textContent = "真实计划";
    const toolsBadge = $("#agentToolsBadge");
    if (toolsBadge) toolsBadge.textContent = "真实调用";
    const timelineBadge = $("#timelineStatusBadge");
    if (timelineBadge) {
      let tone = "neutral";
      let symbol = "○";
      let text = "等待执行";
      if (analysis.status === "paused") {
        tone = "warning";
        symbol = "▲";
        text = `暂停于第 ${String((analysis.current_step_index || 0) + 1).padStart(2, "0")} 步`;
      } else if (analysis.status === "completed") {
        tone = "good";
        symbol = "✓";
        text = "全部步骤已完成";
      } else if (analysis.status === "running") {
        tone = "info";
        symbol = "◐";
        text = "执行中";
      } else if (analysis.status === "blocked" || analysis.status === "failed") {
        tone = "danger";
        symbol = "■";
        text = analysis.status === "blocked" ? "数据阻塞" : "执行失败";
      }
      timelineBadge.className = `status-badge status-${tone}`;
      timelineBadge.innerHTML = `<span class="status-symbol">${symbol}</span>${escapeHTML(text)}`;
    }
    const pauseNote = $("#agentPauseNote");
    if (pauseNote) {
      const message =
        analysis.pause_reason ||
        (analysis.status === "completed"
          ? "报告已生成并通过程序核验，仍等待人工复核。"
          : analysis.status === "blocked"
            ? "数据存在阻塞问题，需补充或修正源数据。"
            : "Agent 尚未到达需要人工确认的步骤。");
      pauseNote.innerHTML = `<svg class="icon icon-sm" aria-hidden="true"><use href="#i-${analysis.status === "completed" ? "info" : "alert"}"></use></svg><span>${escapeHTML(message)}</span>`;
    }

    renderAgentPlan();
    renderAgentTools();
    renderAgentTimeline();
    renderAgentPending();
    renderAgentConclusion();
    renderLlmInsight();
    updateAgentActions();
  }

  function renderLlmInsight() {
    const analysis = state.analysis;
    const config = state.config;
    if (!analysis) return;
    const badge = $("#llmInsightBadge");
    const modeBadge = $("#llmModeBadge");
    const scope = $("#llmScopeText");
    const button = $("#llmInsightBtn");
    const insight = analysis.llm_insight;

    if (badge) {
      if (insight?.available) {
        badge.className = "status-badge status-good";
        badge.innerHTML = `<span class="status-symbol">✓</span>已生成`;
      } else if (insight && insight.status !== "local") {
        badge.className = "status-badge status-warning";
        badge.innerHTML = `<span class="status-symbol">▲</span>调用失败`;
      } else if (config?.llm?.configured) {
        badge.className = "status-badge status-info";
        badge.innerHTML = `<span class="status-symbol">◇</span>可生成`;
      } else {
        badge.className = "status-badge status-neutral";
        badge.innerHTML = `<span class="status-symbol">○</span>未配置`;
      }
    }
    if (modeBadge) {
      const failed = analysis.model_status?.startsWith("remote_") && analysis.model_status !== "remote_success";
      modeBadge.className = `status-badge status-${config?.llm?.configured ? (failed ? "warning" : "info") : "neutral"}`;
      modeBadge.innerHTML = `<span class="status-symbol">${config?.llm?.configured ? (failed ? "▲" : "◇") : "○"}</span>${
        config?.llm?.configured ? (failed ? "模型回退" : "远程模型") : "本地模式"
      }`;
    }
    if (scope) {
      scope.textContent = config?.llm?.configured
        ? `${config.llm.data_scope} 当前模型：${config.llm.model}。`
        : "未配置远程模型；AI 解读不可用。";
    }
    if (button) {
      button.disabled = state.busy || !state.online || !config?.llm?.configured || !(analysis.metrics || []).length;
      button.textContent = state.busy ? "正在调用模型…" : "生成 AI 解读";
    }
    const content = $("#llmInsightContent");
    if (!content) return;
    if (insight?.available) {
      content.innerHTML = `<div class="llm-summary">${escapeHTML(insight.summary)}</div>
        <div class="llm-section-grid">
          <div><h3>关键信号</h3><ul>${(insight.signals || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul></div>
          <div><h3>待核验方向</h3><ul>${(insight.questions || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul></div>
          <div><h3>限制</h3><ul>${(insight.limitations || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul></div>
        </div>
        <div class="llm-meta">模型：${escapeHTML(insight.model)} · 状态：${escapeHTML(insight.status_label)} · ${escapeHTML(formatDate(insight.generated_at))}</div>`;
    } else if (insight && insight.error) {
      content.innerHTML = `<div class="limit-box"><strong>模型调用未成功</strong>${escapeHTML(insight.error)}<br>财务计算与报告不受影响。</div>`;
    } else {
      const ready = Boolean((analysis.metrics || []).length);
      content.innerHTML = `<div class="empty-state compact">${
        !config?.llm?.configured
          ? "未配置远程模型；AI 解读不可用。"
          : !ready
            ? "请先运行指标计算，再生成 AI 解读。"
            : "点击“生成 AI 解读”，模型只接收指标方向、规则状态与限制；不发送原始文件。"
      }</div>`;
    }
  }

  async function generateLlmInsight() {
    if (state.busy || !state.online || !state.analysis) return;
    if (!state.config?.llm?.configured) {
      showToast("未配置远程模型；请检查 .env 后重启服务。", "error");
      return;
    }
    state.busy = true;
    renderLlmInsight();
    updateAgentActions();
    try {
      const payload = await api(`/api/analyses/${state.analysis.id}/llm-insight`, { method: "POST" });
      applyAnalysisPayload(payload.analysis, { render: false });
      if (state.currentView === "agent") renderAgent();
      if (payload.insight?.available) showToast("AI 解读已生成并通过数字/定性校验。");
      else showToast(payload.insight?.error || "AI 解读调用失败；已保留本地计算结果。", "error");
    } catch (error) {
      showToast(error.message || "AI 解读失败", "error");
    } finally {
      state.busy = false;
      renderLlmInsight();
      updateAgentActions();
    }
  }

  function renderAgentPlan() {
    const list = $("#agentPlan");
    if (!list) return;
    list.innerHTML = (state.analysis?.steps || [])
      .map((step) => {
        const tone = planTone(step.status);
        const badgeTone = step.status === "completed" ? "status-good" : step.status === "waiting_review" || step.status === "failed" ? "status-warning" : "status-neutral";
        return `<li class="plan-item is-${tone}">
          <span class="plan-index">${String(step.seq).padStart(2, "0")}</span>
          <span class="plan-copy"><strong>${escapeHTML(step.title)}</strong><span>${escapeHTML(step.tool)}</span></span>
          <span class="status-badge ${badgeTone}">${escapeHTML(step.status_label)}</span>
        </li>`;
      })
      .join("");
  }

  function renderAgentTools() {
    const list = $("#agentTools");
    if (!list) return;
    const steps = (state.analysis?.steps || []).filter((step) => step.status !== "pending");
    if (!steps.length) {
      list.innerHTML = `<div class="empty-state compact">尚未调用工具。点击“运行到暂停 / 完成”开始真实执行。</div>`;
      return;
    }
    list.innerHTML = steps
      .map((step) => `<div class="tool-item">
        <span class="tool-icon"><svg class="icon icon-xs" aria-hidden="true"><use href="#i-tool"></use></svg></span>
        <span class="tool-copy"><strong>${escapeHTML(step.tool)}</strong><span>${escapeHTML(step.result_summary || step.purpose)}</span></span>
        <span class="tool-state">${escapeHTML(step.status_label)}</span>
      </div>`)
      .join("");
  }

  function evidenceForStep(step) {
    const evidence = state.analysis?.evidence || [];
    const ids = new Set(step.evidence_ids || []);
    return evidence.filter((item) => ids.has(item.id)).slice(0, 8);
  }

  function renderStepResult(step) {
    const pieces = [];
    if (step.error) pieces.push(`<div class="limit-box"><strong>执行失败</strong>${escapeHTML(step.error)}</div>`);
    if (step.id === "anomalies") {
      const findings = state.analysis?.findings || [];
      pieces.push(`<div class="result-list">${findings
        .map(
          (finding) => `<div><span>${escapeHTML(finding.id)} ${escapeHTML(finding.title)}</span><strong>${escapeHTML(
            finding.risk_level === "high" ? "高风险" : finding.risk_level === "medium" ? "中风险" : "低风险"
          )}</strong><em>${escapeHTML(finding.status_label)}</em></div>`
        )
        .join("")}</div>`);
    }
    if (step.id === "evidence" && (state.analysis?.pending_questions || []).length) {
      pieces.push(`<div class="result-list">${state.analysis.pending_questions
        .slice(0, 4)
        .map(
          (question) => `<div><span>${escapeHTML(question.title)}</span><strong>${escapeHTML(question.severity)}</strong><em>${escapeHTML(
            question.missing
          )}</em></div>`
        )
        .join("")}</div>`);
    }
    if (step.id === "report" && state.analysis?.report_available) {
      pieces.push(`<p><a class="text-link" href="#report" data-route="report">打开生成的报告草稿 <svg class="icon icon-xs" aria-hidden="true"><use href="#i-arrow-right"></use></svg></a></p>`);
    }
    if (step.id === "verify_report" && step.result?.checks) {
      pieces.push(`<div class="evidence-chips">${step.result.checks
        .map((check) => `<span class="evidence-chip">${escapeHTML(check)}</span>`)
        .join("")}</div>`);
    }
    const evidence = evidenceForStep(step);
    if (evidence.length) {
      pieces.push(`<div class="evidence-chips">${evidence
        .map(
          (item) => `<span class="evidence-chip" title="${escapeHTML(item.source_file)}:${item.source_row}">${escapeHTML(
            item.id
          )} · ${escapeHTML(item.period)}</span>`
        )
        .join("")}</div>`);
      pieces.push(`<table class="timeline-table"><thead><tr><th>证据</th><th>来源</th><th>原始值</th></tr></thead><tbody>${evidence
        .map(
          (item) => `<tr><td class="mono">${escapeHTML(item.id)}</td><td>${escapeHTML(item.source_file)} · ${escapeHTML(
            item.source_sheet
          )} 第 ${escapeHTML(item.source_row)} 行</td><td>${escapeHTML(item.raw_value)}</td></tr>`
        )
        .join("")}</tbody></table>`);
    }
    pieces.push(`<details class="viz-data"><summary>工具输入参数</summary><pre class="code-block">${escapeHTML(
      JSON.stringify(step.input_params || {}, null, 2)
    )}</pre></details>`);
    if (step.result && Object.keys(step.result).length) {
      const raw = JSON.stringify(step.result, null, 2);
      pieces.push(`<details class="viz-data"><summary>程序返回的原始结果</summary><pre class="code-block">${escapeHTML(
        raw.length > 6000 ? `${raw.slice(0, 6000)}\n…（已截断显示）` : raw
      )}</pre></details>`);
    }
    return pieces.join("");
  }

  function renderAgentTimeline() {
    const timeline = $("#agentTimeline");
    if (!timeline) return;
    const steps = state.analysis?.steps || [];
    if (!steps.length) {
      timeline.innerHTML = `<div class="empty-state">没有任务步骤。</div>`;
      return;
    }
    let openIndex = steps.findIndex((step) => step.status === "waiting_review" || step.status === "failed");
    if (openIndex < 0) {
      const completedIndices = steps.map((step, index) => (step.status === "completed" ? index : -1)).filter((index) => index >= 0);
      openIndex = completedIndices.length ? completedIndices[completedIndices.length - 1] : 0;
    }
    timeline.innerHTML = steps
      .map((step, index) => {
        const tone = step.status === "completed" ? "is-complete" : step.status === "waiting_review" || step.status === "failed" ? "is-warning" : "is-waiting";
        const marker =
          step.status === "completed"
            ? '<svg class="icon icon-xs" aria-hidden="true"><use href="#i-check"></use></svg>'
            : step.status === "waiting_review" || step.status === "failed"
              ? "▲"
              : "○";
        const badgeTone = step.status === "completed" ? "status-good" : step.status === "waiting_review" || step.status === "failed" ? "status-warning" : "status-neutral";
        const open = index === openIndex;
        return `<article class="timeline-step ${tone}${index === openIndex ? " is-current" : ""}">
          <span class="timeline-marker">${marker}</span>
          <div class="timeline-content">
            <button class="timeline-toggle" type="button" aria-expanded="${open ? "true" : "false"}" aria-controls="timeline-body-${step.seq}">
              <span class="timeline-index">${String(step.seq).padStart(2, "0")}</span>
              <span class="timeline-heading"><span class="timeline-title">${escapeHTML(step.title)}</span><span class="timeline-subtitle">${escapeHTML(step.purpose)}</span></span>
              <span class="status-badge ${badgeTone}">${escapeHTML(step.status_label)}</span>
              <svg class="icon icon-sm timeline-chevron" aria-hidden="true"><use href="#i-chevron-down"></use></svg>
            </button>
            <div class="timeline-body" id="timeline-body-${step.seq}"${open ? "" : " hidden"}>
              <p>${escapeHTML(step.tool)} · 实际耗时 ${step.duration_ms == null ? "—" : `${step.duration_ms} ms`} · ${escapeHTML(step.status_label)}</p>
              <div class="timeline-meta-row"><span class="meta-chip">步骤 <strong>${step.seq}/${steps.length}</strong></span><span class="meta-chip">工具 <strong>${escapeHTML(step.tool)}</strong></span><span class="meta-chip">证据 <strong>${(step.evidence_ids || []).length}</strong></span></div>
              <p>${escapeHTML(step.result_summary || "尚未执行。")}</p>
              ${renderStepResult(step)}
            </div>
          </div>
        </article>`;
      })
      .join("");
    updateTimelineToggleLabel();
  }

  function renderAgentPending() {
    const list = $("#agentPending");
    const badge = $("#agentPendingCount");
    const title = $("#agentPendingTitle");
    const pending = state.analysis?.pending_questions || [];
    const acknowledged = state.analysis?.status === "completed";
    if (badge) badge.textContent = `${pending.length} 项`;
    if (title) title.textContent = acknowledged ? "待核验与已确认限制" : "待核验问题";
    if (!list) return;
    if (!pending.length) {
      list.innerHTML = `<div class="empty-state compact">当前没有待人工核验的问题。</div>`;
      return;
    }
    list.innerHTML = pending
      .map(
        (question) => `<div class="pending-item">
          <span class="pending-icon">${question.severity === "blocker" ? "■" : "▲"}</span>
          <span class="pending-copy"><strong>${escapeHTML(question.title)}</strong><span>${escapeHTML(question.detail)}</span><span>需要：${escapeHTML(
            question.missing
          )}</span></span>
          <span class="status-badge status-${question.severity === "blocker" ? "danger" : acknowledged ? "good" : "warning"}">${escapeHTML(
            question.severity === "blocker" ? "阻塞" : acknowledged ? "已确认限制" : "待核验"
          )}</span>
        </div>`
      )
      .join("");
  }

  function renderAgentConclusion() {
    const container = $("#agentConclusion");
    if (!container) return;
    const analysis = state.analysis;
    if (!analysis?.verified_conclusions?.length) {
      container.innerHTML = `<div class="empty-state compact">证据核验后，系统会在这里区分已验证事实、待核验事项和不能得出的结论。当前尚未生成结论草稿。</div>`;
    } else {
      const groups = [
        ["verified", "已验证事实", analysis.verified_conclusions],
        ["unverified", "待核验推断", analysis.unverified_conclusions],
        ["cannot", "不能得出的结论", analysis.cannot_conclude],
      ];
      container.innerHTML = groups
        .map(
          ([tone, title, items]) => `<div class="conclusion-group ${tone}"><h3>${escapeHTML(title)}</h3><ul>${(items || [])
            .map((item) => `<li>${escapeHTML(item)}</li>`)
            .join("")}</ul></div>`
        )
        .join("");
    }
    const meter = $(".evidence-meter .progress-fill");
    const meterText = $("#agentEvidenceMeterText");
    const totalSteps = analysis?.progress_total || 1;
    const doneSteps = analysis?.progress_done || 0;
    if (meter) meter.style.width = `${Math.round((doneSteps / totalSteps) * 100)}%`;
    if (meterText) meterText.textContent = `${doneSteps} / ${totalSteps} 步`;
    const meterNote = $("#agentEvidenceMeterNote");
    if (meterNote) {
      meterNote.textContent = `${(analysis?.evidence || []).length} 条可追溯证据 · ${(analysis?.pending_questions || []).length} 项${
        analysis?.status === "completed" ? "已确认限制" : "待核验"
      }；报告生成后可在证据附录追溯。`;
    }
  }

  function updateAgentActions() {
    const analysis = state.analysis;
    const busy = state.busy;
    const canRun = state.online && !busy && analysis && (analysis.status === "created" || analysis.status === "running");
    const canReview = state.online && !busy && analysis && analysis.status === "paused";
    const canImport = state.online && !busy && analysis && (analysis.status === "paused" || analysis.status === "blocked" || analysis.status === "failed");
    const canReset = state.online && !busy && Boolean(analysis);
    const stepBtn = $("#agentStepBtn");
    const runBtn = $("#agentRunBtn");
    const reviewBtn = $("#agentReviewBtn");
    const importBtn = $("#agentImportBtn");
    const resetBtn = $("#agentResetBtn");
    const reportBtn = $("#agentReportBtn");
    if (stepBtn) stepBtn.disabled = !canRun;
    if (runBtn) {
      runBtn.disabled = !canRun;
      runBtn.textContent = busy ? "正在执行…" : "运行到暂停 / 完成";
    }
    if (reviewBtn) reviewBtn.hidden = !canReview;
    if (importBtn) importBtn.hidden = !canImport;
    if (resetBtn) resetBtn.disabled = !canReset;
    if (reportBtn) {
      reportBtn.disabled = !analysis?.report_available;
      reportBtn.title = analysis?.report_available ? "查看报告草稿" : "完成分析并生成报告后可用";
    }
    const nextBadge = $("#agentNextActionBadge");
    const hint = $("#agentActionHint");
    if (nextBadge && analysis) {
      nextBadge.className = `status-badge status-${statusTone(analysis.status)}`;
      nextBadge.innerHTML = `<span class="status-symbol">${statusSymbol(analysis.status)}</span>${escapeHTML(analysis.status_label)}`;
    }
    if (hint && analysis) {
      hint.textContent =
        analysis.status === "paused"
          ? "证据不足：请阅读待核验问题，确认限制后继续，或补充数据重新导入。"
          : analysis.status === "blocked"
            ? "数据存在阻塞问题：请补充或修正源数据。"
            : analysis.status === "completed"
              ? "报告草稿已生成；可查看报告、重新执行或进入讲解模式。"
              : busy
                ? "正在调用真实工具并写入执行记录…"
                : "计划已生成。单步执行可以观察每个工具的真实返回结果。";
    }
  }

  async function runAgentLoop(singleStep = false) {
    if (state.busy || !state.online || !state.analysis) return;
    if (!["created", "running"].includes(state.analysis.status)) return;
    state.busy = true;
    updateAgentActions();
    let steps = 0;
    try {
      while (steps < 12) {
        const payload = await api(`/api/analyses/${state.analysis.id}/advance`, { method: "POST" });
        applyAnalysisPayload(payload, { render: false });
        steps += 1;
        if (state.currentView === "agent") renderAgent();
        if (singleStep) break;
        if (["paused", "blocked", "completed", "failed", "cancelled"].includes(state.analysis.status)) break;
        await delay(450);
      }
      if (state.analysis.status === "paused") showToast("Agent 已暂停：证据不足，等待人工核验。请阅读待核验问题。");
      else if (state.analysis.status === "blocked") showToast("数据存在阻塞问题，分析停止。请补充或修正数据。", "error");
      else if (state.analysis.status === "completed") showToast("分析完成，报告草稿已生成并通过程序核验。");
      else if (state.analysis.status === "failed") showToast(`执行失败：${state.analysis.error || "请查看步骤详情"}`, "error");
    } catch (error) {
      showToast(error.message || "执行失败", "error");
    } finally {
      state.busy = false;
      updateAgentActions();
      refreshAnalysesList();
      if (state.currentView === "agent") renderAgent();
    }
  }

  async function reviewAndContinue() {
    if (state.busy || !state.analysis) return;
    state.busy = true;
    updateAgentActions();
    try {
      const payload = await api(`/api/analyses/${state.analysis.id}/review`, {
        method: "POST",
        body: {
          decision: "confirm_limitations",
          note: "课堂演示：用户确认当前证据边界，继续生成带限制的报告。",
          auto_continue: false,
        },
      });
      applyAnalysisPayload(payload, { render: false });
      state.busy = false;
      if (state.currentView === "agent") renderAgent();
      await runAgentLoop(false);
    } catch (error) {
      state.busy = false;
      updateAgentActions();
      showToast(error.message || "确认核验失败", "error");
    }
  }

  async function resetAgent() {
    if (!state.online || !state.analysis) return;
    try {
      const payload = await api(`/api/analyses/${state.analysis.id}/reset`, { method: "POST" });
      state.report = null;
      state.scenario.result = null;
      state.scenario.sensitivity = null;
      state.scenario.analysisId = null;
      applyAnalysisPayload(payload);
      showToast("已重置为新的分析计划；数据与证据不会丢失。");
      refreshAnalysesList();
    } catch (error) {
      showToast(error.message || "重置失败", "error");
    }
  }

  async function selectAnalysis(analysisId, options = {}) {
    if (!analysisId || !state.online) return;
    try {
      const payload = await api(`/api/analyses/${analysisId}/activate`, { method: "POST" });
      state.report = null;
      state.scenario.result = null;
      state.scenario.sensitivity = null;
      state.scenario.analysisId = null;
      applyAnalysisPayload(payload, { render: false });
      if (options.activate) await refreshAnalysesList();
      renderCurrentView();
    } catch (error) {
      showToast(error.message || "切换分析失败", "error");
    }
  }

  /* ------------------------------------------------------------------------
     Data import
     ------------------------------------------------------------------------ */

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes)) return "—";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function setWorkflowStep(id, tone) {
    const element = document.getElementById(id);
    if (!element) return;
    element.classList.remove("is-current", "is-complete");
    if (tone) element.classList.add(tone);
  }

  function unresolvedMappingItems(dataset) {
    const items = dataset?.mapping?.items || [];
    return items.filter((item) => {
      const status = item.status || "unmapped";
      return status === "ambiguous" || status === "unmapped" || status === "fuzzy";
    });
  }

  function renderFileQueue() {
    const queue = $("#fileQueue");
    const meta = $("#fileQueueMeta");
    if (!queue) return;
    const dataset = state.importDataset;
    if (state.selectedFiles.length) {
      if (meta) meta.textContent = `${state.selectedFiles.length} 个文件 · 尚未上传`;
      queue.innerHTML = state.selectedFiles
        .map((file) => {
          const extension = file.name.split(".").pop()?.toLowerCase() || "";
          const kind = extension === "csv" ? "CSV" : extension === "pdf" ? "PDF" : "XLSX";
          const tone = ["csv", "xlsx", "pdf"].includes(extension) ? "warning" : "danger";
          return `<div class="file-item">
            <span class="file-icon file-${extension === "pdf" ? "pdf" : extension === "csv" ? "csv" : "xlsx"}">${kind}</span>
            <span class="file-copy"><strong>${escapeHTML(file.name)}</strong><span>${formatBytes(file.size)} · 等待后端解析</span></span>
            ${statusBadge(tone, "○", "待上传")}
          </div>`;
        })
        .join("");
      return;
    }
    if (dataset) {
      if (meta) meta.textContent = `${dataset.files.length} 个文件 · 已解析`;
      queue.innerHTML = dataset.files
        .map((file) => {
          const extension = file.name.split(".").pop()?.toLowerCase() || "";
          const kind = extension === "csv" ? "CSV" : extension === "pdf" ? "PDF" : "XLSX";
          return `<div class="file-item">
            <span class="file-icon file-${extension === "pdf" ? "pdf" : extension === "csv" ? "csv" : "xlsx"}">${kind}</span>
            <span class="file-copy"><strong>${escapeHTML(file.name)}</strong><span>${formatBytes(file.size_bytes)} · SHA-256 ${escapeHTML(
              file.sha256.slice(0, 12)
            )}…</span></span>
            ${statusBadge("good", "✓", "已解析")}
          </div>`;
        })
        .join("");
      return;
    }
    if (meta) meta.textContent = "尚未选择文件";
    queue.innerHTML = `<div class="empty-state compact">选择文件后显示文件名、大小与上传状态；未上传前不会读取内容。</div>`;
  }

  function renderQualityOverview(dataset) {
    const container = $("#qualityOverview");
    const badge = $("#qualityStatusBadge");
    if (!container) return;
    if (!dataset) {
      if (badge) {
        badge.className = "status-badge status-neutral";
        badge.innerHTML = `<span class="status-symbol">○</span>未加载`;
      }
      container.innerHTML = `<div class="empty-state">上传文件或载入星禾商贸示例后，这里显示真实解析结果。</div>`;
      return;
    }
    const score = dataset.quality_score ?? 0;
    const offset = 314.159 * (1 - score / 100);
    const periods = (dataset.periods || []).map((period) => period.label || period.raw).slice(0, 3).join("、");
    if (badge) {
      badge.className = `status-badge status-${dataset.blocker_count ? "danger" : dataset.warning_count ? "warning" : "good"}`;
      badge.innerHTML = `<span class="status-symbol">${dataset.blocker_count ? "■" : dataset.warning_count ? "▲" : "✓"}</span>${
        dataset.blocker_count ? `${dataset.blocker_count} 个阻塞` : dataset.warning_count ? `${dataset.warning_count} 个警告` : "校验通过"
      }`;
    }
    container.innerHTML = `<div class="quality-score">
      <div class="score-ring" aria-label="数据质量评分 ${score} 分">
        <svg viewBox="0 0 120 120" aria-hidden="true"><circle class="score-track" cx="60" cy="60" r="50"></circle><circle class="score-value" cx="60" cy="60" r="50" style="stroke-dashoffset:${offset.toFixed(3)}"></circle></svg>
        <span><strong>${score}</strong><small>/ 100</small></span>
      </div>
      <div class="score-copy"><strong>${escapeHTML(dataset.company)}</strong><p>${escapeHTML(dataset.source_label || "本地数据")}</p><div class="score-tags"><span>${dataset.files.length} 个文件</span><span>${dataset.facts_count} 条记录</span><span>${escapeHTML(periods || "期间待识别")}</span></div></div>
    </div>
    <div class="quality-grid">
      <div><span>有效记录</span><strong>${dataset.valid_facts}</strong><em>条</em></div>
      <div><span>阻塞问题</span><strong>${dataset.blocker_count}</strong><em>${dataset.blocker_count ? "需修正" : "无"}</em></div>
      <div><span>警告</span><strong>${dataset.warning_count}</strong><em>${dataset.warning_count ? "需核对" : "无"}</em></div>
      <div><span>映射待确认</span><strong>${unresolvedMappingItems(dataset).length}</strong><em>项</em></div>
    </div>
    ${dataset.blocker_count ? `<p class="helper-text text-danger">存在阻塞问题，创建分析前需修正原始数据或补充文件。</p>` : ""}`;
  }

  function renderMapping(dataset) {
    const container = $("#mappingContent");
    const badge = $("#mappingStatusBadge");
    if (!container) return;
    if (!dataset) {
      if (badge) {
        badge.className = "status-badge status-neutral";
        badge.textContent = "未加载";
      }
      container.innerHTML = `<div class="empty-state">解析完成后展示列映射、科目映射和待确认项。</div>`;
      return;
    }
    const items = dataset.mapping?.items || [];
    const unresolved = unresolvedMappingItems(dataset);
    const columns = dataset.mapping?.columns || [];
    if (badge) {
      badge.className = `status-badge status-${unresolved.length ? "warning" : "good"}`;
      badge.innerHTML = `<span class="status-symbol">${unresolved.length ? "▲" : "✓"}</span>${unresolved.length ? `${unresolved.length} 项待确认` : "映射已确认"}`;
    }
    const columnHTML = columns.length
      ? `<div class="table-wrap"><table class="data-table data-table-compact mapping-table"><caption class="visually-hidden">列映射</caption><thead><tr><th>源列</th><th>标准角色</th><th>置信度</th><th>示例</th><th>状态</th></tr></thead><tbody>${columns
          .map(
            (column) => `<tr><td><span class="cell-primary">${escapeHTML(column.source)}</span></td><td><span class="mono">${escapeHTML(
              column.target || "未映射"
            )}</span></td><td>${Math.round((column.confidence || 0) * 100)}%</td><td class="formula">${escapeHTML(
              column.sample || "—"
            )}</td><td>${column.target ? statusBadge("good", "✓", "已识别") : statusBadge("warning", "▲", "需人工确认")}</td></tr>`
          )
          .join("")}</tbody></table></div>`
      : `<p class="helper-text">当前使用 PDF 文本布局或宽表，列角色由结构或表头推断。</p>`;
    const unresolvedHTML = unresolved.length
      ? `<div class="mapping-confirm-block"><div class="mapping-section-head"><h3>待人工确认的科目</h3><span>系统不会猜测“暂收款”等含义不明确的名称</span></div>
        <div class="table-wrap"><table class="data-table data-table-compact"><caption class="visually-hidden">待确认科目映射</caption><thead><tr><th>原始科目</th><th>来源行</th><th>原始值</th><th>建议 / 选择标准字段</th><th>操作</th></tr></thead><tbody>${unresolved
          .map(
            (item) => `<tr><td><span class="cell-primary">${escapeHTML(item.raw_item)}</span><span class="cell-secondary">${escapeHTML(
              item.note || ""
            )}</span></td><td class="mono">${escapeHTML(item.source_file)}:${item.source_row}</td><td class="formula">${escapeHTML(
              item.sample_value || "—"
            )}</td><td><select class="select select-sm mapping-select" data-raw-item="${escapeHTML(item.raw_item)}"><option value="">保持未映射</option>${Object.entries(
              FIELD_LABELS
            )
              .map(
                ([field, label]) =>
                  `<option value="${field}"${item.field === field ? " selected" : ""}>${escapeHTML(label)}</option>`
              )
              .join("")}</select></td><td><button class="button button-secondary button-sm save-mapping-btn" type="button" data-raw-item="${escapeHTML(
              item.raw_item
            )}">保存映射</button></td></tr>`
          )
          .join("")}</tbody></table></div></div>`
      : `<div class="mapping-success"><span class="status-badge status-good"><span class="status-symbol">✓</span>没有待确认映射</span><span>可以创建 Agent 分析。</span></div>`;
    const confirmedUnmapped = items.filter((item) => item.status === "confirmed_unmapped").length;
    const mappedCount = items.length - unresolved.length - confirmedUnmapped;
    container.innerHTML = `<div class="mapping-summary"><span>布局：${escapeHTML(dataset.mapping.layout || "long")}</span><span>科目：${items.length} 项</span><span>已映射：${mappedCount} 项</span><span>确认不映射：${confirmedUnmapped} 项</span></div>${columnHTML}${unresolvedHTML}`;
  }

  function renderQualityChecks(dataset) {
    const container = $("#qualityChecks");
    const badge = $("#checksStatusBadge");
    if (!container) return;
    if (!dataset) {
      if (badge) {
        badge.className = "status-badge status-neutral";
        badge.textContent = "未加载";
      }
      container.innerHTML = `<div class="empty-state">校验结果来自后端，包含阻塞、警告和提示。</div>`;
      return;
    }
    if (badge) {
      badge.className = `status-badge status-${dataset.blocker_count ? "danger" : dataset.warning_count ? "warning" : "good"}`;
      badge.innerHTML = `<span class="status-symbol">${dataset.blocker_count ? "■" : dataset.warning_count ? "▲" : "✓"}</span>${dataset.blocker_count ? `${dataset.blocker_count} 阻塞` : dataset.warning_count ? `${dataset.warning_count} 警告` : "通过"}`;
    }
    const issues = dataset.quality || [];
    if (!issues.length) {
      container.innerHTML = `<div class="check-list"><div class="check-good"><span class="check-icon"><svg class="icon icon-sm" aria-hidden="true"><use href="#i-check"></use></svg></span><div><strong>未发现数据质量问题</strong><p>字段、期间和数值均已通过当前规则。</p></div></div></div>`;
      return;
    }
    container.innerHTML = `<ul class="check-list">${issues
      .map((issue) => {
        const danger = issue.severity === "blocker";
        const warning = issue.severity === "warning";
        const cls = danger ? "check-danger" : warning ? "check-warning" : "check-good";
        const symbol = danger ? "■" : warning ? "▲" : "○";
        return `<li class="${cls}"><span class="check-icon">${symbol}</span><div><strong>${escapeHTML(issue.message)}</strong><p>${escapeHTML(
          issue.detail || issue.impact || ""
        )}</p><span class="check-meta">${escapeHTML(issue.code)} · ${danger ? "阻塞" : warning ? "警告" : "提示"}</span></div></li>`;
      })
      .join("")}</ul>`;
  }

  function renderRawPreview(dataset) {
    const tabs = $("#rawTabs");
    const wrap = $("#rawTableWrap");
    const meta = $("#rawPreviewMeta");
    if (!tabs || !wrap) return;
    const previews = dataset?.previews || [];
    if (!previews.length) {
      tabs.innerHTML = `<button class="tab is-active" type="button" role="tab" aria-selected="true">等待数据</button>`;
      wrap.innerHTML = `<div class="empty-state compact">解析完成后展示原始工作表内容。</div>`;
      if (meta) meta.textContent = "最多展示前 8 行";
      return;
    }
    state.rawPreviewIndex = Math.min(state.rawPreviewIndex, previews.length - 1);
    tabs.innerHTML = previews
      .map(
        (preview, index) =>
          `<button class="tab${index === state.rawPreviewIndex ? " is-active" : ""}" type="button" role="tab" aria-selected="${index === state.rawPreviewIndex}" data-preview-index="${index}">${escapeHTML(
            `${preview.file}${preview.sheet ? ` / ${preview.sheet}` : ""}`
          )}</button>`
      )
      .join("");
    const preview = previews[state.rawPreviewIndex];
    if (meta) meta.textContent = `${preview.file} · 共 ${preview.total_rows} 行 · 展示前 ${preview.rows.length} 行`;
    wrap.innerHTML = `<table class="data-table data-table-compact"><caption class="visually-hidden">${escapeHTML(preview.file)} 原始预览</caption><thead><tr>${preview.headers
      .map((header) => `<th scope="col">${escapeHTML(header)}</th>`)
      .join("")}</tr></thead><tbody>${preview.rows
      .map(
        (row) =>
          `<tr>${row
            .map((cell) => `<td${cell === "" ? ' class="cell-empty">（空值，未补 0）' : `>${escapeHTML(cell)}`}</td>`)
            .join("")}</tr>`
      )
      .join("")}</tbody></table>`;
  }

  function renderImport() {
    const dataset = state.importDataset;
    const uploadBtn = $("#uploadFilesBtn");
    const statusBadgeEl = $("#uploadStatusBadge");
    if (uploadBtn) uploadBtn.disabled = state.busy || !state.selectedFiles.length || !state.online;
    if (statusBadgeEl) {
      const tone = state.busy ? "info" : state.selectedFiles.length ? "warning" : dataset ? "good" : "neutral";
      statusBadgeEl.className = `status-badge status-${tone}`;
      statusBadgeEl.innerHTML = `<span class="status-symbol">${state.busy ? "◐" : state.selectedFiles.length ? "▲" : dataset ? "✓" : "○"}</span>${state.busy ? "处理中" : state.selectedFiles.length ? "待上传" : dataset ? "已加载" : "等待文件"}`;
    }
    renderFileQueue();
    renderQualityOverview(dataset);
    renderMapping(dataset);
    renderQualityChecks(dataset);
    renderRawPreview(dataset);
    const unresolved = unresolvedMappingItems(dataset);
    const blockers = dataset?.blocker_count || 0;
    const canCreate = Boolean(state.online && dataset && blockers === 0 && unresolved.length === 0 && !state.busy);
    const createTop = $("#createAnalysisBtn");
    const createBottom = $("#createAnalysisBtnBottom");
    if (createTop) createTop.disabled = !canCreate;
    if (createBottom) createBottom.disabled = !canCreate;
    const selectedLabel = $("#selectedDatasetLabel");
    if (selectedLabel) {
      selectedLabel.textContent = dataset
        ? `已选择：${dataset.company} · ${dataset.source_label} · ${dataset.facts_count} 条记录。${
            blockers ? "存在阻塞问题，暂不能创建分析。" : unresolved.length ? `还有 ${unresolved.length} 项映射待确认。` : "可以进行真实分析。"
          }`
        : "尚未选择可分析的数据集。演示数据集只读；请上传文件或载入星禾商贸示例。";
    }
    setWorkflowStep("workflowUpload", dataset ? "is-complete" : "is-current");
    setWorkflowStep("workflowMapping", dataset ? (unresolved.length ? "is-current" : "is-complete") : "");
    setWorkflowStep("workflowQuality", dataset && !unresolved.length ? "is-complete" : dataset ? "is-current" : "");
    setWorkflowStep("workflowAnalysis", dataset && !blockers && !unresolved.length ? "is-current" : "");
  }

  async function uploadFiles() {
    if (!state.selectedFiles.length || state.busy) return;
    state.busy = true;
    renderImport();
    const form = new FormData();
    state.selectedFiles.forEach((file) => form.append("files", file));
    form.append("company", $("#companyInput")?.value || "未命名企业 · 上传数据");
    try {
      const response = await fetch("/api/uploads", { method: "POST", body: form, cache: "no-store" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new ApiError(payload.detail || "上传或解析失败", response.status, payload);
      state.importDataset = payload;
      state.selectedFiles = [];
      state.rawPreviewIndex = 0;
      const input = $("#fileInput");
      if (input) input.value = "";
      showToast(`解析完成：${payload.files.length} 个文件、${payload.facts_count} 条记录。请确认字段映射。`);
    } catch (error) {
      showToast(error.message || "上传失败", "error");
    } finally {
      state.busy = false;
      renderImport();
    }
  }

  async function loadSampleDataset() {
    if (state.busy || !state.online) return;
    state.busy = true;
    renderImport();
    try {
      const dataset = await api("/api/demo/sample/import", { method: "POST" });
      state.importDataset = dataset;
      state.selectedFiles = [];
      state.rawPreviewIndex = 0;
      showToast("已载入星禾商贸 XLSX 示例：包含“暂收款”模糊字段，请人工确认映射。");
    } catch (error) {
      showToast(error.message || "载入示例失败", "error");
    } finally {
      state.busy = false;
      renderImport();
    }
  }

  async function saveMapping(rawItem) {
    if (!state.importDataset || !state.online) return;
    const select = document.querySelector(`.mapping-select[data-raw-item="${CSS.escape(rawItem)}"]`);
    if (!select) return;
    try {
      const dataset = await api(`/api/uploads/${state.importDataset.id}/mapping`, {
        method: "PATCH",
        body: { item_overrides: { [rawItem]: select.value || null } },
      });
      state.importDataset = dataset;
      renderImport();
      showToast(select.value ? `“${rawItem}”已显式映射为 ${FIELD_LABELS[select.value] || select.value}。` : `“${rawItem}”已确认保持未映射；不会进入核心指标。`);
    } catch (error) {
      showToast(error.message || "保存映射失败", "error");
    }
  }

  async function createAnalysisFromImport() {
    if (state.busy || !state.importDataset) return;
    const unresolved = unresolvedMappingItems(state.importDataset);
    if (state.importDataset.blocker_count > 0 || unresolved.length) {
      showToast("存在阻塞问题或待确认映射，暂时不能创建分析。", "error");
      return;
    }
    state.busy = true;
    renderImport();
    try {
      const payload = await api("/api/analyses", {
        method: "POST",
        body: {
          dataset_id: state.importDataset.id,
          goal: `评估 ${state.importDataset.company} 的本期收入增长与利润、现金流变化，识别可核查异常并说明证据边界。`,
          auto_run: false,
        },
      });
      state.report = null;
      state.scenario.result = null;
      state.scenario.analysisId = null;
      applyAnalysisPayload(payload, { render: false });
      await refreshAnalysesList();
      state.busy = false;
      navigate("agent");
      showToast("分析任务已创建，正在按真实工具逐项执行…");
      await delay(250);
      await runAgentLoop(false);
    } catch (error) {
      showToast(error.message || "创建分析失败", "error");
    } finally {
      state.busy = false;
      renderImport();
    }
  }

  function resetImport() {
    state.importDataset = null;
    state.selectedFiles = [];
    state.rawPreviewIndex = 0;
    const input = $("#fileInput");
    if (input) input.value = "";
    renderImport();
    showToast("已清空当前导入；不会删除已经创建的分析。");
  }

  /* ------------------------------------------------------------------------
     Scenario simulation
     ------------------------------------------------------------------------ */

  const PRESETS = {
    cautious: { revenue_change_pct: -3, cost_rate_change_pp: 1, selling_expense_change_pct: -5 },
    base: { revenue_change_pct: 0, cost_rate_change_pp: 0, selling_expense_change_pct: 0 },
    improve: { revenue_change_pct: 6, cost_rate_change_pp: -2, selling_expense_change_pct: 5 },
    custom: { revenue_change_pct: 4, cost_rate_change_pp: -1, selling_expense_change_pct: 6 },
  };

  function readScenarioParams() {
    return {
      revenue_change_pct: Number($("#revChange")?.value || 0),
      cost_rate_change_pp: Number($("#costDelta")?.value || 0),
      selling_expense_change_pct: Number($("#salesChange")?.value || 0),
    };
  }

  function setScenarioInputs(params) {
    if ($("#revChange")) $("#revChange").value = String(params.revenue_change_pct);
    if ($("#costDelta")) $("#costDelta").value = String(params.cost_rate_change_pp);
    if ($("#salesChange")) $("#salesChange").value = String(params.selling_expense_change_pct);
  }

  function updateRangeFill(input) {
    if (!input) return;
    const min = Number(input.min);
    const max = Number(input.max);
    const value = Number(input.value);
    const percent = ((value - min) / (max - min)) * 100;
    input.style.background = `linear-gradient(to right, var(--accent) 0%, var(--accent) ${percent}%, var(--surface-strong) ${percent}%, var(--surface-strong) 100%)`;
  }

  async function loadScenarioView() {
    if (!state.online) return;
    if (!state.analysis || !(state.analysis.metrics || []).length) {
      renderScenarioUnavailable("请先在 Agent 工作区运行指标计算，再使用情景模拟。");
      return;
    }
    const current = readScenarioParams();
    const activeId = state.analysis.id;
    if (state.scenario.analysisId === activeId && state.scenario.result) {
      renderScenario(state.scenario.result, state.scenario.sensitivity);
      return;
    }
    setScenarioOutputsPending();
    await requestScenario(current, { silent: true });
  }

  async function requestScenario(params, options = {}) {
    if (!state.online || !state.analysis) return;
    state.scenario.busy = true;
    const requestSeq = ++state.scenario.seq;
    state.scenario.error = "";
    if (!options.silent) setScenarioOutputsPending();
    try {
      const payload = await api(`/api/analyses/${state.analysis.id}/scenario`, {
        method: "POST",
        body: params,
      });
      if (requestSeq !== state.scenario.seq) return;
      state.scenario.result = payload.scenario;
      state.scenario.sensitivity = payload.sensitivity || [];
      state.scenario.analysisId = state.analysis.id;
      state.scenario.params = { ...params };
      state.scenario.error = "";
      state.report = null;
      if (payload.analysis) {
        applyAnalysisPayload(payload.analysis, { render: false });
      }
      if (state.currentView === "scenario") renderScenario(payload.scenario, payload.sensitivity || []);
      if (state.currentView === "report") loadReportView();
    } catch (error) {
      if (requestSeq !== state.scenario.seq) return;
      state.scenario.error = error.message || "情景模拟不可用。";
      renderScenarioUnavailable(state.scenario.error);
      if (!options.silent) showToast(state.scenario.error, "error");
    } finally {
      if (requestSeq === state.scenario.seq) {
        state.scenario.busy = false;
        renderScenarioStatus();
      }
    }
  }

  function setScenarioOutputsPending() {
    const badge = $("#scenarioBackendStatus");
    if (badge) {
      badge.className = "status-badge status-info";
      badge.innerHTML = `<span class="status-symbol">◐</span>后端计算中`;
    }
  }

  function renderScenarioStatus() {
    const badge = $("#scenarioBackendStatus");
    if (!badge) return;
    if (state.scenario.busy) {
      badge.className = "status-badge status-info";
      badge.innerHTML = `<span class="status-symbol">◐</span>后端计算中`;
    } else if (state.scenario.error) {
      badge.className = "status-badge status-warning";
      badge.innerHTML = `<span class="status-symbol">▲</span>不可用`;
    } else if (state.scenario.result) {
      badge.className = "status-badge status-good";
      badge.innerHTML = `<span class="status-symbol">✓</span>后端确定性计算`;
    } else {
      badge.className = "status-badge status-neutral";
      badge.innerHTML = `<span class="status-symbol">○</span>尚未运行`;
    }
  }

  function renderScenarioUnavailable(message) {
    ["scenarioNetProfit", "scenarioNetMargin", "scenarioBaseNetProfit", "scenarioNetChange", "scenarioRevenue", "scenarioGrossProfit", "scenarioSellingExpense", "scenarioPretax"].forEach((id) => {
      const element = document.getElementById(id);
      if (element) element.textContent = "—";
    });
    const delta = $("#scenarioNetDelta");
    if (delta) {
      delta.textContent = "不可用";
      delta.className = "result-delta is-neutral";
    }
    const body = $("#sensitivityBody");
    if (body) body.innerHTML = `<tr><td colspan="4">情景模拟不可用。</td></tr>`;
    const assumptions = $("#scenarioAssumptions");
    if (assumptions) assumptions.innerHTML = `<h3>不可用</h3><p>${escapeHTML(message)}</p>`;
    const status = $("#scenarioBackendStatus");
    if (status) {
      status.className = "status-badge status-warning";
      status.innerHTML = `<span class="status-symbol">▲</span>不可用`;
    }
    const snapshot = $("#scenarioSnapshotBtn");
    if (snapshot) snapshot.disabled = true;
  }

  function renderScenario(result, sensitivity) {
    if (!result) return;
    const params = result.params;
    setText("revChangeOut", `${params.revenue_change_pct > 0 ? "+" : ""}${formatNumber(params.revenue_change_pct, 1)}%`);
    setText("costDeltaOut", `${params.cost_rate_change_pp > 0 ? "+" : ""}${formatNumber(params.cost_rate_change_pp, 1)}pp`);
    setText("salesChangeOut", `${params.selling_expense_change_pct > 0 ? "+" : ""}${formatNumber(params.selling_expense_change_pct, 1)}%`);
    ["revChange", "costDelta", "salesChange"].forEach((id) => updateRangeFill(document.getElementById(id)));
    const net = $("#scenarioNetProfit");
    if (net) net.innerHTML = `¥${formatNumber(result.net_profit / 10000, 1)}<span>万</span>`;
    const deltaEl = $("#scenarioNetDelta");
    if (deltaEl) {
      const negative = Number(result.delta_amount) < 0;
      deltaEl.className = `result-delta${negative ? " is-negative" : Number(result.delta_amount) > 0 ? "" : " is-neutral"}`;
      deltaEl.textContent = `${result.delta_pct > 0 ? "+" : ""}${formatNumber(result.delta_pct, 1)}% vs 基准`;
    }
    setText("scenarioNetMargin", `${formatNumber(result.net_margin_pct, 1)}%`);
    setText("scenarioBaseNetProfit", `¥${formatNumber(result.baseline.net_profit / 10000, 1)} 万`);
    setText("scenarioNetChange", `${result.delta_amount > 0 ? "+" : ""}¥${formatNumber(Math.abs(result.delta_amount) / 10000, 1)} 万`);
    setText("scenarioRevenue", `¥${formatNumber(result.revenue / 10000, 1)} 万`);
    setText("scenarioRevenueDelta", `${params.revenue_change_pct > 0 ? "+" : ""}${formatNumber(params.revenue_change_pct, 1)}% vs 基准`);
    setText("scenarioGrossProfit", `¥${formatNumber(result.gross_profit / 10000, 1)} 万`);
    setText("scenarioGrossMargin", `毛利率 ${formatNumber(result.gross_margin_pct, 1)}%`);
    setText("scenarioSellingExpense", `¥${formatNumber(result.selling_expenses / 10000, 1)} 万`);
    setText("scenarioSellingDelta", `${params.selling_expense_change_pct > 0 ? "+" : ""}${formatNumber(params.selling_expense_change_pct, 1)}% vs 基准`);
    setText("scenarioPretax", `¥${formatNumber(result.pretax_profit / 10000, 1)} 万`);
    const max = Math.max(result.baseline.net_profit, result.net_profit) * 1.12 || 1;
    const baseBar = $("#compareBaseBar");
    const simBar = $("#compareSimBar");
    if (baseBar) baseBar.style.width = `${(result.baseline.net_profit / max) * 100}%`;
    if (simBar) simBar.style.width = `${(result.net_profit / max) * 100}%`;
    setText("compareBaseValue", formatNumber(result.baseline.net_profit / 10000, 1));
    setText("compareSimValue", formatNumber(result.net_profit / 10000, 1));
    const formula = $("#view-scenario .formula-block code");
    if (formula) formula.textContent = result.formula;
    const assumptions = $("#scenarioAssumptions");
    if (assumptions) {
      assumptions.innerHTML = `<h3>基准来源与固定假设</h3><ul>${(result.baseline.source_fields || [])
        .map((field) => `<li>${escapeHTML(field)}：取自本期报表计算</li>`)
        .join("")}${(result.assumptions || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
        <div class="assumption-divider"></div><h3>限制</h3><ul>${(result.limitations || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
        <p class="helper-text">证据引用：${(result.baseline.evidence_ids || []).join("、") || "—"}</p>`;
    }
    const activePreset = Object.entries(PRESETS).find(
      ([, preset]) => preset.revenue_change_pct === params.revenue_change_pct && preset.cost_rate_change_pp === params.cost_rate_change_pp && preset.selling_expense_change_pct === params.selling_expense_change_pct
    );
    $$("#scenarioPresets [data-preset]").forEach((button) => {
      button.classList.toggle("is-active", Boolean(activePreset && activePreset[0] === button.dataset.preset));
    });
    const snapshot = $("#scenarioSnapshotBtn");
    if (snapshot) snapshot.disabled = false;
    const body = $("#sensitivityBody");
    if (body && Array.isArray(sensitivity)) {
      body.innerHTML = sensitivity
        .map((row) => {
          const cells = (row.cells || [])
            .map((cell) => {
              const tone = Number(cell.delta_pct) > 0.05 ? "cell-positive" : Number(cell.delta_pct) < -0.05 ? "cell-negative" : "cell-neutral";
              return `<td class="${tone}">${formatNumber(cell.net_profit / 10000, 1)}</td>`;
            })
            .join("");
          return `<tr><td>${row.revenue_change_pct > 0 ? "+" : ""}${formatNumber(row.revenue_change_pct, 0)}%</td>${cells}</tr>`;
        })
        .join("");
    }
    renderScenarioStatus();
  }

  function setText(id, text) {
    const element = document.getElementById(id);
    if (element) element.textContent = text;
  }

  function bindScenario() {
    ["revChange", "costDelta", "salesChange"].forEach((id) => {
      const input = document.getElementById(id);
      if (!input) return;
      updateRangeFill(input);
      input.addEventListener("input", () => {
        updateRangeFill(input);
        if (id === "revChange" && $("#revChangeOut")) {
          $("#revChangeOut").textContent = `${Number(input.value) > 0 ? "+" : ""}${formatNumber(input.value, 1)}%`;
        }
        if (id === "costDelta" && $("#costDeltaOut")) {
          $("#costDeltaOut").textContent = `${Number(input.value) > 0 ? "+" : ""}${formatNumber(input.value, 1)}pp`;
        }
        if (id === "salesChange" && $("#salesChangeOut")) {
          $("#salesChangeOut").textContent = `${Number(input.value) > 0 ? "+" : ""}${formatNumber(input.value, 1)}%`;
        }
        if (!state.online) return;
        if (state.scenario.timer) window.clearTimeout(state.scenario.timer);
        state.scenario.timer = window.setTimeout(() => requestScenario(readScenarioParams(), { silent: false }), 260);
      });
    });
    $$("#scenarioPresets [data-preset]").forEach((button) => {
      button.addEventListener("click", () => {
        const preset = PRESETS[button.dataset.preset];
        if (!preset) return;
        setScenarioInputs(preset);
        ["revChange", "costDelta", "salesChange"].forEach((id) => updateRangeFill(document.getElementById(id)));
        requestScenario(preset, { silent: false });
      });
    });
    $("#scenarioReset")?.addEventListener("click", () => {
      setScenarioInputs(PRESETS.base);
      requestScenario(PRESETS.base, { silent: false });
      showToast("已重置为基准：收入 0%、成本率 0pp、销售费用 0%。");
    });
    $("#scenarioSnapshotBtn")?.addEventListener("click", async () => {
      if (!state.scenario.result) return;
      const wasCompleted = state.analysis?.status === "completed";
      await requestScenario(readScenarioParams(), { silent: true });
      if (wasCompleted) {
        state.report = null;
        showToast("情景参数已写入分析状态，报告草稿已按当前结果重新生成。");
      } else {
        showToast("情景参数已写入分析状态；后续生成报告时会使用该结果。");
      }
    });
  }

  /* ------------------------------------------------------------------------
     Report
     ------------------------------------------------------------------------ */

  let reportRequestSeq = 0;

  async function loadReportView() {
    if (!state.online) return;
    if (!state.analysis) return;
    const seq = ++reportRequestSeq;
    if (state.analysis.status !== "completed") {
      renderReportEmpty("报告尚未生成。请先在 Agent 工作区完成证据核验并生成报告。");
      return;
    }
    try {
      const report = await api(`/api/analyses/${state.analysis.id}/report`);
      if (seq !== reportRequestSeq) return;
      state.report = report;
      renderReport(report);
    } catch (error) {
      if (seq !== reportRequestSeq) return;
      state.report = null;
      renderReportEmpty(error.message || "报告尚未生成。");
    }
  }

  function setReportButtons(enabled) {
    ["copySummaryBtn", "downloadReportBtn", "printReportBtn"].forEach((id) => {
      const button = document.getElementById(id);
      if (button) button.disabled = !enabled;
    });
  }

  function renderReportEmpty(message) {
    const pageSubtitle = $("#reportPageSubtitle");
    if (pageSubtitle) pageSubtitle.textContent = "结构化、可追溯、保留边界。报告由当前分析状态生成，尚未经过人工复核。";
    const notice = $("#view-report .notice-bar");
    if (notice) {
      const strong = $("strong", notice);
      const span = $("span:not(.status-badge):not(.demo-inline)", notice);
      if (strong) strong.textContent = "报告草稿 · 未生成";
      if (span) span.textContent = "请先在 Agent 工作区完成证据核验；当前没有可导出的报告。";
    }
    const body = $("#reportBody");
    const appendix = $("#reportAppendix");
    if (body) {
      body.hidden = false;
      body.innerHTML = `<div class="empty-state report-empty"><span class="report-brand">FinGuard</span><h2>报告尚未生成</h2><p>${escapeHTML(
        message
      )}</p><button class="button button-primary" type="button" data-route="agent">前往 Agent 工作区</button></div>`;
    }
    if (appendix) {
      appendix.hidden = true;
      appendix.innerHTML = "";
    }
    switchReportTab("body", { silent: true });
    setReportButtons(false);
    const source = $("#reportSource");
    if (source) source.textContent = "报告未生成 · 数字来自当前分析状态";
    const toc = $("#reportTocStatus");
    if (toc) {
      toc.innerHTML = `<div class="toc-status-head"><span>核验状态</span>${statusBadge("warning", "▲", "未生成")}</div><p>完成证据核验并运行报告步骤后，可在此查看和导出 Markdown。</p>`;
    }
  }

  function renderReport(report) {
    const pageSubtitle = $("#reportPageSubtitle");
    if (pageSubtitle) pageSubtitle.textContent = "结构化、可追溯、保留边界。报告由当前分析状态生成，尚未经过人工复核。";
    const body = $("#reportBody");
    const appendix = $("#reportAppendix");
    if (!body || !appendix) return;
    body.innerHTML = report.html;
    appendix.innerHTML = report.appendix_html;
    switchReportTab("body", { silent: true });
    setReportButtons(true);
    const source = $("#reportSource");
    if (source) {
      source.textContent = `${state.analysis.company} · ${state.analysis.period_label} · ${state.dataset?.source_label || "本地数据"}`;
    }
    const toc = $("#reportTocStatus");
    if (toc) {
      const evidenceCount = (state.analysis.evidence || []).length;
      const pendingCount = (state.analysis.pending_questions || []).length;
      toc.innerHTML = `<div class="toc-status-head"><span>核验状态</span>${statusBadge("warning", "▲", "待人工复核")}</div>
        <div class="progress-track progress-track-thin"><span class="progress-fill evidence-fill" style="width:${state.analysis.progress_total ? Math.round((state.analysis.progress_done / state.analysis.progress_total) * 100) : 0}%"></span></div>
        <p>${evidenceCount} 条可追溯证据；${pendingCount} 项已确认/待核验限制。报告由后端生成。</p>
        <dl><div><dt>版本</dt><dd>${escapeHTML(state.analysis.id)}</dd></div><div><dt>模型</dt><dd>${escapeHTML(report.model_status)}</dd></div><div><dt>生成</dt><dd>${escapeHTML(formatDate(report.generated_at))}</dd></div></dl>`;
    }
    const notice = $("#view-report .notice-bar");
    if (notice) {
      const strong = $("strong", notice);
      const span = $("span:not(.status-badge):not(.demo-inline)", notice);
      if (strong) strong.textContent = "报告草稿 · 待人工复核";
      if (span) span.textContent = "报告由结构化分析结果生成；Markdown 下载来自后端。异常仍只是核查信号。";
    }
  }

  function switchReportTab(tab, options = {}) {
    const appendix = tab === "appendix";
    const body = $("#reportBody");
    const appendixEl = $("#reportAppendix");
    if (!body || !appendixEl) return;
    body.hidden = appendix;
    appendixEl.hidden = !appendix;
    $$("#reportTabs [data-report-tab]").forEach((button) => {
      const active = button.dataset.reportTab === tab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (!options.silent && appendix === false) {
      body.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }

  function scrollToReportTarget(id) {
    const target = document.getElementById(id);
    if (!target) return;
    const appendix = $("#reportAppendix");
    const inAppendix = appendix && appendix.contains(target);
    switchReportTab(inAppendix ? "appendix" : "body", { silent: true });
    requestAnimationFrame(() => target.scrollIntoView({ block: "start", behavior: "smooth" }));
  }

  function buildReportSummary() {
    const analysis = state.analysis;
    if (!analysis) return "";
    const lines = [
      `FinGuard 财务诊断报告 · 执行摘要`,
      `${analysis.company} · ${analysis.period_label}（对比 ${analysis.prior_period || "—"}）`,
      `运行编号：${analysis.id}`,
      `状态：${analysis.status_label}`,
      "",
      "已验证事实：",
      ...(analysis.verified_conclusions || []).map((item) => `- ${item}`),
      "",
      "待核验：",
      ...(analysis.unverified_conclusions || []).map((item) => `- ${item}`),
      "",
      "限制：",
      ...(analysis.cannot_conclude || []).map((item) => `- ${item}`),
      "",
      "本摘要为辅助分析草稿，不构成审计、鉴证、税务或投资建议。",
    ];
    return lines.join("\n");
  }

  function bindReport() {
    $("#copySummaryBtn")?.addEventListener("click", async () => {
      if (!state.report) return;
      const copied = await copyText(buildReportSummary());
      showToast(copied ? "执行摘要已复制。" : "复制失败，请手动选择文本。", copied ? "info" : "error");
    });
    $("#downloadReportBtn")?.addEventListener("click", () => {
      if (!state.report || !state.analysis) return;
      const anchor = document.createElement("a");
      anchor.href = `/api/analyses/${encodeURIComponent(state.analysis.id)}/report.md`;
      anchor.download = "FinGuard_report.md";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      showToast("已从后端请求 Markdown 报告下载。");
    });
    $("#printReportBtn")?.addEventListener("click", () => {
      if (!state.report) return;
      switchReportTab("body", { silent: true });
      window.print();
    });
  }

  /* ------------------------------------------------------------------------
     Anomaly drawer
     ------------------------------------------------------------------------ */

  function openAnomalyDrawer(findingId) {
    const finding = (state.analysis?.findings || []).find((item) => item.id === findingId);
    if (!finding) return;
    state.drawerFindingId = findingId;
    state.lastFocused = document.activeElement;
    const drawerLayer = $("#anomalyDrawer");
    const severity = $("#drawerSeverity");
    const title = $("#drawerTitle");
    const meta = $("#drawerMeta");
    const body = $("#drawerBody");
    if (!drawerLayer || !severity || !title || !meta || !body) return;
    severity.className = `status-badge status-${riskTone(finding.risk_level)}`;
    severity.innerHTML = `<span class="status-symbol">${riskSymbol(finding.risk_level)}</span>${escapeHTML(riskLabel(finding.risk_level))}`;
    title.textContent = finding.title;
    meta.textContent = `${finding.id} · ${finding.period} · 规则 ${finding.rule_id} · ${finding.status_label}`;
    const rawRows = (finding.raw_data || [])
      .map(
        (row) => `<tr><td>${escapeHTML(row.period)}</td><td>${escapeHTML(row.item)}</td><td class="num">${escapeHTML(
          row.value
        )}</td><td>${escapeHTML(row.source)}</td></tr>`
      )
      .join("");
    body.innerHTML = `<section class="drawer-section"><p>${escapeHTML(finding.evidence_summary)}</p></section>
      <section class="drawer-section"><h3>关键指标</h3><div class="metric-compare-grid"><div class="metric-box${finding.risk_level === "high" ? " is-alert" : ""}"><span>${escapeHTML(
        finding.metric_label
      )}</span><strong>${escapeHTML(finding.metric_value)}</strong></div><div class="metric-box"><span>状态 / 规则</span><strong>${escapeHTML(
        finding.status_label
      )}</strong><small>${escapeHTML(finding.rule_id)}</small></div></div></section>
      <section class="drawer-section"><h3>触发规则</h3><div class="formula-box">${escapeHTML(finding.rule_condition)}</div><p>${escapeHTML(
        finding.basis
      )}</p></section>
      <section class="drawer-section"><h3>原始数据与来源</h3><table class="drawer-table"><thead><tr><th>期间</th><th>项目</th><th>原始值</th><th>来源</th></tr></thead><tbody>${
        rawRows || '<tr><td colspan="4">未找到可追溯的原始行。</td></tr>'
      }</tbody></table><div class="drawer-evidence-tags">${(finding.evidence_ids || [])
        .map((id) => `<span class="evidence-tag">${escapeHTML(id)}</span>`)
        .join("")}</div></section>
      <section class="drawer-section"><h3>计算公式</h3><div class="formula-box">${escapeHTML(finding.formula)}</div></section>
      <section class="drawer-section"><h3>结论限制</h3><div class="limit-box"><strong>不能从当前数据得出的结论</strong>${escapeHTML(
        finding.limitation
      )}</div></section>
      <section class="drawer-section"><h3>后续核查建议</h3><p>${escapeHTML(finding.recommendation)}</p></section>`;
    drawerLayer.hidden = false;
    document.body.classList.add("is-drawer-open");
    $(".drawer", drawerLayer)?.focus();
  }

  function closeAnomalyDrawer() {
    const layer = $("#anomalyDrawer");
    if (!layer || layer.hidden) return;
    layer.hidden = true;
    document.body.classList.remove("is-drawer-open");
    state.drawerFindingId = null;
    if (state.lastFocused && state.lastFocused.isConnected) state.lastFocused.focus();
    state.lastFocused = null;
  }

  function copyCurrentFinding() {
    const finding = (state.analysis?.findings || []).find((item) => item.id === state.drawerFindingId);
    if (!finding) return;
    const text = `FinGuard 异常证据摘要
${finding.id} · ${finding.title}
风险：${riskLabel(finding.risk_level)} / ${finding.status_label}
规则：${finding.rule_id} · ${finding.rule_condition}
指标：${finding.metric_value}（${finding.metric_sub}）
证据：${(finding.evidence_ids || []).join("、") || "无"}
摘要：${finding.evidence_summary}
限制：${finding.limitation}
建议：${finding.recommendation}`;
    copyText(text).then((copied) =>
      showToast(copied ? "证据摘要已复制。" : "复制失败，请手动选择文本。", copied ? "info" : "error")
    );
  }

  /* ------------------------------------------------------------------------
     Classroom presentation mode
     ------------------------------------------------------------------------ */

  function presentationSlides() {
    const analysis = state.analysis;
    if (!analysis) return [];
    const steps = analysis.steps || [];
    const executed = steps.filter((step) => step.status !== "pending");
    const lastExecuted = executed.length ? executed[executed.length - 1] : null;
    const findings = analysis.findings || [];
    const planHTML = `<p class="presentation-lead">${escapeHTML(analysis.plan_summary || "")}</p>
      <div class="presentation-metric-row"><div><span>规则结果</span><strong>${findings.length}</strong></div><div><span>证据</span><strong>${(analysis.evidence || []).length}</strong></div><div><span>进度</span><strong>${analysis.progress_done}/${analysis.progress_total}</strong></div></div>
      <ul class="presentation-list">${steps
        .map(
          (step) => `<li><span class="presentation-list-index">${String(step.seq).padStart(2, "0")}</span><div><strong>${escapeHTML(
            step.title
          )}</strong><span>${escapeHTML(step.tool)} · ${escapeHTML(step.status_label)}</span></div></li>`
        )
        .join("")}</ul>`;
    const toolHTML = lastExecuted
      ? `<p class="presentation-lead">最近执行：<strong>${escapeHTML(lastExecuted.title)}</strong>（${escapeHTML(
          lastExecuted.tool
        )}）</p>
        <div class="presentation-grid"><div class="presentation-box"><span>工具输入参数</span><pre>${escapeHTML(
          JSON.stringify(lastExecuted.input_params || {}, null, 2)
        )}</pre></div><div class="presentation-box"><span>程序返回摘要</span><p>${escapeHTML(
          lastExecuted.result_summary || "尚未返回"
        )}</p><small>实际耗时 ${lastExecuted.duration_ms ?? "—"} ms · ${escapeHTML(lastExecuted.status_label)}</small></div></div>`
      : `<p class="presentation-lead">工具尚未执行。</p>`;
    const evidence = (analysis.evidence || []).slice(0, 12);
    const evidenceHTML = `<p class="presentation-lead">所有结论可回到原始文件、工作表、行号与期间。以下为前 ${evidence.length} 条证据。</p>
      <table class="report-table presentation-table"><thead><tr><th>证据</th><th>来源</th><th>期间</th><th>原始值</th></tr></thead><tbody>${evidence
        .map(
          (item) => `<tr><td class="mono">${escapeHTML(item.id)}</td><td>${escapeHTML(item.source_file)} · ${escapeHTML(
            item.source_sheet
          )} 第 ${item.source_row} 行</td><td>${escapeHTML(item.period)}</td><td class="num">${escapeHTML(item.raw_value)}</td></tr>`
        )
        .join("")}</tbody></table>`;
    const pending = analysis.pending_questions || [];
    const pauseHTML = `<p class="presentation-lead">${
      analysis.status === "paused"
        ? "Agent 在证据核验处暂停，因为没有足够证据把候选风险写成确定结论。"
        : analysis.status === "completed"
          ? "证据边界已由用户确认，报告已生成；以下限制仍须在复核时保留。"
          : "当前步骤尚未到达人工核验点。"
    }</p>
      <ul class="presentation-checklist">${pending
        .map(
          (question) => `<li><strong>${escapeHTML(question.title)}</strong><span>${escapeHTML(question.detail)}</span><small>缺少：${escapeHTML(
            question.missing
          )}</small></li>`
        )
        .join("") || "<li><strong>当前没有待核验问题。</strong></li>"}</ul>`;
    const conclusionHTML = analysis.verified_conclusions?.length
      ? `<div class="presentation-conclusions">${[
          ["已验证事实", analysis.verified_conclusions, "verified"],
          ["待核验推断", analysis.unverified_conclusions, "unverified"],
          ["不能得出的结论", analysis.cannot_conclude, "cannot"],
        ]
          .map(
            ([title, items, tone]) => `<div class="conclusion-group ${tone}"><h3>${title}</h3><ul>${(items || [])
              .map((item) => `<li>${escapeHTML(item)}</li>`)
              .join("")}</ul></div>`
          )
          .join("")}</div>`
      : `<p class="presentation-lead">报告与结论尚未生成。先完成证据核验，再生成带限制的报告草稿。</p>`;
    return [
      { stage: "计划", title: "为什么进行这项分析", html: planHTML },
      { stage: "工具", title: "调用了什么工具，返回了什么", html: toolHTML },
      { stage: "证据", title: "数字能回到哪里", html: evidenceHTML },
      { stage: "暂停核验", title: "哪些结论仍然不能下", html: pauseHTML },
      { stage: "结论", title: "最终判断与边界", html: conclusionHTML },
    ];
  }

  function renderPresentationSlide() {
    const slides = presentationSlides();
    if (!slides.length) return;
    const index = Math.max(0, Math.min(state.presentation.index, slides.length - 1));
    state.presentation.index = index;
    const slide = slides[index];
    setText("presentationStageLabel", `阶段 ${index + 1} / ${slides.length}`);
    setText("presentationStageTitle", slide.title);
    const content = $("#presentationContent");
    if (content) content.innerHTML = slide.html;
    const taskRef = $("#presentationTaskRef");
    if (taskRef) taskRef.textContent = `${state.analysis?.company || ""} · ${state.analysis?.id || ""} · ${slide.stage}`;
    $$(".presentation-stage-indicator").forEach((indicator, indicatorIndex) => {
      indicator.classList.toggle("is-active", indicatorIndex === index);
    });
    const prev = $("#presentationPrev");
    const next = $("#presentationNext");
    if (prev) prev.disabled = index === 0;
    if (next) next.disabled = index === slides.length - 1;
  }

  function openPresentation() {
    if (!state.analysis) return;
    state.presentation.lastFocused = document.activeElement;
    state.presentation.index = 0;
    const layer = $("#presentationLayer");
    if (!layer) return;
    layer.hidden = false;
    document.body.classList.add("is-presentation-open");
    renderPresentationSlide();
    const panel = $(".presentation-panel", layer);
    if (panel) panel.focus();
    showToast("讲解模式读取当前真实执行记录；不会播放独立演示动画。");
  }

  function closePresentation() {
    const layer = $("#presentationLayer");
    if (!layer || layer.hidden) return;
    stopPresentationAuto();
    layer.hidden = true;
    document.body.classList.remove("is-presentation-open");
    if (state.presentation.lastFocused?.isConnected) state.presentation.lastFocused.focus();
    state.presentation.lastFocused = null;
  }

  function movePresentation(delta) {
    const slides = presentationSlides();
    state.presentation.index = Math.max(0, Math.min(slides.length - 1, state.presentation.index + delta));
    renderPresentationSlide();
  }

  function startPresentationAuto() {
    stopPresentationAuto();
    const button = $("#presentationAuto");
    if (button) button.textContent = "停止自动";
    state.presentation.timer = window.setInterval(() => {
      const slides = presentationSlides();
      if (state.presentation.index >= slides.length - 1) {
        stopPresentationAuto();
        return;
      }
      state.presentation.index += 1;
      renderPresentationSlide();
    }, 4200);
  }

  function stopPresentationAuto() {
    if (state.presentation.timer) {
      window.clearInterval(state.presentation.timer);
      state.presentation.timer = null;
    }
    const button = $("#presentationAuto");
    if (button) button.textContent = "自动演示";
  }

  /* ------------------------------------------------------------------------
     Small actions
     ------------------------------------------------------------------------ */

  function toggleProjection() {
    const active = document.body.classList.toggle("projection-mode");
    const button = $("#projectionToggle");
    if (button) {
      button.setAttribute("aria-pressed", active ? "true" : "false");
      const label = $("span", button);
      if (label) label.textContent = active ? "退出投影" : "投影模式";
    }
    try {
      localStorage.setItem("finguard-projection-mode", active ? "1" : "0");
    } catch (_error) {
      /* ignore */
    }
    window.setTimeout(() => renderChartsForCurrentView(), 120);
    showToast(active ? "已进入课堂投影模式；可使用浏览器全屏。" : "已退出课堂投影模式。");
  }

  function exportMetricsCsv() {
    const metrics = state.analysis?.metrics || [];
    if (!metrics.length) {
      showToast("尚未生成指标，无法导出。", "error");
      return;
    }
    const rows = [
      ["指标", "本期", "上年同期", "变化", "状态", "公式", "证据"],
      ...metrics.map((metric) => [
        metric.label,
        metric.display?.value || "",
        metric.display?.prior || "",
        metric.display?.delta || "",
        metric.status_label,
        metric.formula || "",
        (metric.evidence_ids || []).join("; "),
      ]),
    ];
    const csv = "﻿" + rows.map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(",")).join("\n");
    downloadBlob(csv, `FinGuard_指标_${state.analysis.id}.csv`, "text/csv;charset=utf-8");
    showToast("指标 CSV 已生成并下载；数值来自当前分析状态。");
  }

  function toggleMetricGlossary() {
    let panel = $("#metricGlossaryPanel");
    if (panel) {
      panel.hidden = !panel.hidden;
      return;
    }
    const metricsCard = $("#metricsTableBody")?.closest(".metrics-table-card");
    if (!metricsCard) return;
    panel = document.createElement("div");
    panel.id = "metricGlossaryPanel";
    panel.className = "glossary-panel";
    panel.innerHTML = `<div class="card-header"><div><div class="card-kicker">FORMULA GLOSSARY</div><h2 class="card-title">指标字典</h2></div><button class="icon-button" type="button" id="closeGlossary" aria-label="关闭指标字典"><svg class="icon" aria-hidden="true"><use href="#i-close"></use></svg></button></div><div class="glossary-grid">${(state.analysis?.metrics || [])
      .map(
        (metric) => `<div class="glossary-item"><strong>${escapeHTML(metric.label)}</strong><code>${escapeHTML(
          metric.formula || "—"
        )}</code><span>${escapeHTML(metric.status_label)}</span></div>`
      )
      .join("")}</div>`;
    metricsCard.insertAdjacentElement("afterend", panel);
    $("#closeGlossary")?.addEventListener("click", () => {
      panel.hidden = true;
    });
  }

  function copyFilteredAnomalies() {
    const analysis = state.analysis;
    if (!analysis) return;
    const query = state.anomalySearch.toLowerCase().trim();
    const findings = (analysis.findings || []).filter((finding) => {
      const matchesFilter = state.anomalyFilter === "all" || finding.risk_level === state.anomalyFilter;
      const text = [finding.id, finding.title, finding.rule_id, finding.metric_value, finding.evidence_summary].join(" ").toLowerCase();
      return matchesFilter && (!query || text.includes(query));
    });
    if (!findings.length) {
      showToast("当前筛选没有异常可复制。", "error");
      return;
    }
    const text = findings
      .map(
        (finding) =>
          `${finding.id} ${finding.title}\n风险：${riskLabel(finding.risk_level)} / ${finding.status_label}\n规则：${finding.rule_id} · ${finding.rule_condition}\n指标：${finding.metric_value}\n摘要：${finding.evidence_summary}\n限制：${finding.limitation}\n建议：${finding.recommendation}`
      )
      .join("\n\n");
    copyText(text).then((copied) =>
      showToast(copied ? `已复制 ${findings.length} 条异常摘要。` : "复制失败，请手动选择文本。", copied ? "info" : "error")
    );
  }

  async function testLlmConnection() {
    if (!state.online) {
      showToast("本地后端未连接。请运行 uv run finguard 后刷新页面。", "error");
      return;
    }
    try {
      const result = await api("/api/config/test-llm", { method: "POST" });
      if (result.ok) {
        showToast(`模型连接测试成功：${result.model} · ${result.latency_ms} ms。未发送财务数据。`);
      } else {
        showToast(`模型连接测试失败：${result.error}`, "error");
      }
      state.config = await api("/api/config/status");
      renderSettings();
    } catch (error) {
      showToast(error.message || "模型测试失败", "error");
    }
  }

  async function checkBackendConnection() {
    if (!state.online) {
      showToast("本地后端未连接。请运行 uv run finguard 后刷新页面。", "error");
      return;
    }
    try {
      const [health, config] = await Promise.all([api("/api/health"), api("/api/config/status")]);
      state.config = config;
      renderSettings();
      showToast(
        health.status === "ok"
          ? `本地服务正常；模型${config.llm.configured ? "已在服务端配置（未主动测试）" : "未配置"}。`
          : "本地服务返回异常。",
        health.status === "ok" ? "info" : "error"
      );
    } catch (error) {
      showToast(error.message || "连接检查失败", "error");
    }
  }

  async function loadDemoAnalysis() {
    if (!state.online) return;
    try {
      const payload = await api("/api/demo/reset", { method: "POST" });
      state.importDataset = null;
      state.report = null;
      state.scenario.result = null;
      state.scenario.analysisId = null;
      applyAnalysisPayload(payload, { render: false });
      await refreshAnalysesList();
      navigate("dashboard");
      showToast("已重新载入澄海智造演示分析；Agent 已真实执行到证据核验暂停点。");
    } catch (error) {
      showToast(error.message || "载入演示失败", "error");
    }
  }

  /* ------------------------------------------------------------------------
     Event wiring
     ------------------------------------------------------------------------ */

  function handleDocumentClick(event) {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const closeDrawer = target.closest("[data-close-drawer]");
    if (closeDrawer) {
      event.preventDefault();
      closeAnomalyDrawer();
      return;
    }

    const route = target.closest("[data-route]");
    if (route) {
      event.preventDefault();
      navigate(route.dataset.route);
      return;
    }

    const analysisRow = target.closest("[data-analysis-id]");
    if (analysisRow) {
      event.preventDefault();
      selectAnalysis(analysisRow.dataset.analysisId, { activate: true });
      return;
    }

    const anomalyButton = target.closest("[data-anomaly-open]");
    if (anomalyButton) {
      event.preventDefault();
      openAnomalyDrawer(anomalyButton.dataset.anomalyOpen);
      return;
    }

    const reportTab = target.closest("[data-report-tab]");
    if (reportTab) {
      event.preventDefault();
      switchReportTab(reportTab.dataset.reportTab);
      return;
    }

    const reportTabTarget = target.closest("[data-report-tab-target]");
    if (reportTabTarget) {
      event.preventDefault();
      switchReportTab(reportTabTarget.dataset.reportTabTarget);
      return;
    }

    const reportTarget = target.closest("[data-report-target]");
    if (reportTarget) {
      event.preventDefault();
      scrollToReportTarget(reportTarget.dataset.reportTarget);
      return;
    }

    const riskFilter = target.closest("[data-risk-filter]");
    if (riskFilter) {
      state.anomalyFilter = riskFilter.dataset.riskFilter;
      renderAnomalies();
      return;
    }

    const previewTab = target.closest("[data-preview-index]");
    if (previewTab) {
      state.rawPreviewIndex = Number(previewTab.dataset.previewIndex) || 0;
      renderRawPreview(state.importDataset);
      return;
    }

    const saveMappingButton = target.closest(".save-mapping-btn");
    if (saveMappingButton) {
      event.preventDefault();
      saveMapping(saveMappingButton.dataset.rawItem);
      return;
    }

    const timelineToggle = target.closest(".timeline-toggle");
    if (timelineToggle) {
      const expanded = timelineToggle.getAttribute("aria-expanded") === "true";
      timelineToggle.setAttribute("aria-expanded", expanded ? "false" : "true");
      const bodyId = timelineToggle.getAttribute("aria-controls");
      const body = bodyId ? document.getElementById(bodyId) : null;
      if (body) body.hidden = expanded;
      updateTimelineToggleLabel();
      return;
    }

  }

  function updateTimelineToggleLabel() {
    const button = $("#timelineToggleAll");
    const toggles = $$(".timeline-toggle", $("#agentTimeline") || document);
    if (!button || !toggles.length) return;
    const allOpen = toggles.every((toggle) => toggle.getAttribute("aria-expanded") === "true");
    button.textContent = allOpen ? "全部收起" : "全部展开";
  }

  function initSidebar() {
    try {
      if (localStorage.getItem("finguard-sidebar-collapsed") === "1") {
        document.body.classList.add("sidebar-collapsed");
        if (sidebarCollapse) {
          const label = $(".nav-label", sidebarCollapse);
          if (label) label.textContent = "展开侧边栏";
          sidebarCollapse.setAttribute("aria-label", "展开侧边栏");
        }
      }
    } catch (_error) {
      /* ignore */
    }
    sidebarToggle?.addEventListener("click", (event) => {
      event.preventDefault();
      toggleSidebar();
    });
    sidebarCollapse?.addEventListener("click", (event) => {
      event.preventDefault();
      toggleSidebar();
    });
    sidebarScrim?.addEventListener("click", (event) => {
      event.preventDefault();
      closeMobileSidebar();
    });
    const media = window.matchMedia("(max-width: 1180px)");
    const onMedia = () => {
      if (!media.matches) closeMobileSidebar();
    };
    if (typeof media.addEventListener === "function") media.addEventListener("change", onMedia);
    else if (typeof media.addListener === "function") media.addListener(onMedia);
  }

  function initImportControls() {
    const input = $("#fileInput");
    const dropzone = $("#dropzone");
    if (dropzone && input) {
      dropzone.addEventListener("click", () => input.click());
      dropzone.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          input.click();
        }
      });
      ["dragenter", "dragover"].forEach((type) =>
        dropzone.addEventListener(type, (event) => {
          event.preventDefault();
          event.stopPropagation();
          dropzone.classList.add("is-dragover");
        })
      );
      ["dragleave", "drop"].forEach((type) =>
        dropzone.addEventListener(type, (event) => {
          event.preventDefault();
          event.stopPropagation();
          dropzone.classList.remove("is-dragover");
        })
      );
      dropzone.addEventListener("drop", (event) => {
        const files = Array.from(event.dataTransfer?.files || []);
        if (files.length) {
          state.selectedFiles = files;
          state.importDataset = null;
          state.rawPreviewIndex = 0;
          renderImport();
        }
      });
      input.addEventListener("change", () => {
        state.selectedFiles = Array.from(input.files || []);
        state.importDataset = null;
        state.rawPreviewIndex = 0;
        renderImport();
        input.value = "";
      });
    }
    $("#uploadFilesBtn")?.addEventListener("click", uploadFiles);
    $("#loadSampleDatasetBtn")?.addEventListener("click", loadSampleDataset);
    $("#resetImportBtn")?.addEventListener("click", resetImport);
    $("#createAnalysisBtn")?.addEventListener("click", createAnalysisFromImport);
    $("#createAnalysisBtnBottom")?.addEventListener("click", createAnalysisFromImport);
  }

  function initAgentControls() {
    $("#agentStepBtn")?.addEventListener("click", () => runAgentLoop(true));
    $("#agentRunBtn")?.addEventListener("click", () => runAgentLoop(false));
    $("#agentReviewBtn")?.addEventListener("click", reviewAndContinue);
    $("#agentResetBtn")?.addEventListener("click", resetAgent);
    $("#agentImportBtn")?.addEventListener("click", () => navigate("import"));
    $("#agentReportBtn")?.addEventListener("click", () => {
      if (!state.analysis?.report_available) {
        showToast("报告尚未生成；请先完成证据核验和报告步骤。", "error");
        return;
      }
      navigate("report");
    });
    $("#presentationBtn")?.addEventListener("click", openPresentation);
    $("#llmInsightBtn")?.addEventListener("click", generateLlmInsight);
    $("#timelineToggleAll")?.addEventListener("click", () => {
      const toggles = $$(".timeline-toggle", $("#agentTimeline") || document);
      const allOpen = toggles.every((toggle) => toggle.getAttribute("aria-expanded") === "true");
      toggles.forEach((toggle) => {
        toggle.setAttribute("aria-expanded", allOpen ? "false" : "true");
        const bodyId = toggle.getAttribute("aria-controls");
        const body = bodyId ? document.getElementById(bodyId) : null;
        if (body) body.hidden = allOpen;
      });
      updateTimelineToggleLabel();
    });
    $("#analysisSelect")?.addEventListener("change", (event) => {
      if (event.target.value) selectAnalysis(event.target.value, { activate: true });
    });
  }

  function initDashboardControls() {
    $("#loadDemoBtn")?.addEventListener("click", loadDemoAnalysis);
    $("#dashboardReportBtn")?.addEventListener("click", () => {
      if (!state.analysis?.report_available) {
        showToast("报告尚未生成；请先在 Agent 工作区完成证据核验与报告生成。", "error");
        navigate("agent");
        return;
      }
      navigate("report");
    });
  }

  function initAnomalyControls() {
    $("#anomalySearch")?.addEventListener("input", (event) => {
      state.anomalySearch = event.target.value || "";
      renderAnomalies();
    });
    $("#copyAnomaliesBtn")?.addEventListener("click", copyFilteredAnomalies);
    $("#copyFindingBtn")?.addEventListener("click", copyCurrentFinding);
    $("#anomalyDrawer .drawer-backdrop")?.addEventListener("click", closeAnomalyDrawer);
  }

  function initUtilityControls() {
    $("#projectionToggle")?.addEventListener("click", toggleProjection);
    $("#exportMetricsBtn")?.addEventListener("click", exportMetricsCsv);
    $("#metricGlossaryBtn")?.addEventListener("click", toggleMetricGlossary);
    $("#checkConnectionBtn")?.addEventListener("click", checkBackendConnection);
    $("#testLlmBtn")?.addEventListener("click", testLlmConnection);
    $("#presentationClose")?.addEventListener("click", closePresentation);
    $("#presentationExit")?.addEventListener("click", closePresentation);
    $("#presentationPrev")?.addEventListener("click", () => {
      stopPresentationAuto();
      movePresentation(-1);
    });
    $("#presentationNext")?.addEventListener("click", () => {
      stopPresentationAuto();
      movePresentation(1);
    });
    $("#presentationAuto")?.addEventListener("click", () => {
      if (state.presentation.timer) {
        stopPresentationAuto();
        showToast("已停止自动演示。");
      } else {
        startPresentationAuto();
        showToast("开始自动播放真实执行记录；可随时停止。");
      }
    });
    $("#presentationLayer")?.addEventListener("click", (event) => {
      if (event.target === $("#presentationLayer")) closePresentation();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        if (state.presentation.open || $("#presentationLayer")?.hidden === false) {
          closePresentation();
          return;
        }
        if ($("#anomalyDrawer")?.hidden === false) {
          closeAnomalyDrawer();
          return;
        }
        if (document.body.classList.contains("sidebar-open")) closeMobileSidebar();
      }
    });
    let chartResizeFrame = null;
    window.addEventListener("resize", () => {
      if (chartResizeFrame) window.cancelAnimationFrame(chartResizeFrame);
      chartResizeFrame = window.requestAnimationFrame(() => {
        renderChartsForCurrentView();
        chartResizeFrame = null;
      });
    });
    const appMain = $(".app-main");
    appMain?.addEventListener("transitionend", (event) => {
      if (event.propertyName === "margin-left") renderChartsForCurrentView();
    });
  }

  function initPresentationState() {
    try {
      if (localStorage.getItem("finguard-projection-mode") === "1") {
        document.body.classList.add("projection-mode");
        const button = $("#projectionToggle");
        if (button) {
          button.setAttribute("aria-pressed", "true");
          const label = $("span", button);
          if (label) label.textContent = "退出投影";
        }
      }
    } catch (_error) {
      /* ignore */
    }
  }

  function showOfflineState(error) {
    state.online = false;
    updateGlobalChrome();
    const overlay = $("#bootOverlay");
    if (overlay) overlay.hidden = true;
    if (!$("#offlineBanner")) {
      const banner = document.createElement("div");
      banner.id = "offlineBanner";
      banner.className = "offline-banner";
      banner.innerHTML = `<svg class="icon" aria-hidden="true"><use href="#i-alert"></use></svg><div><strong>后端未连接，当前页面不能执行真实分析。</strong><span>请在本机启动 <code>uv run finguard</code>，然后刷新页面。当前静态内容仅用于视觉参考，不会生成新的计算、证据或报告。</span></div>`;
      mainContent?.prepend(banner);
    }
    setReportButtons(false);
    renderScenarioUnavailable("后端未连接；情景模拟需要本地 FastAPI 服务。");
    showToast(error?.message || "无法连接本地后端。", "error");
  }

  function hideBootOverlay() {
    const overlay = $("#bootOverlay");
    if (overlay) overlay.hidden = true;
  }

  async function boot() {
    initPresentationState();
    initSidebar();
    initImportControls();
    initAgentControls();
    initDashboardControls();
    initAnomalyControls();
    initUtilityControls();
    bindScenario();
    bindReport();
    document.addEventListener("click", handleDocumentClick);
    window.addEventListener("hashchange", () => renderView(routeFromHash(), { scroll: true }));
    setReportButtons(false);
    renderScenarioStatus();
    try {
      const payload = await api("/api/bootstrap");
      state.online = true;
      state.config = payload.config;
      state.dataset = payload.dataset;
      state.analysis = payload.analysis;
      state.analyses = payload.analyses || [];
      state.selectedAnalysisId = payload.analysis?.id || null;
      state.booted = true;
      hideBootOverlay();
      updateGlobalChrome();
      syncAnalysisSelect();
      if (state.analysis) updateAnalysisSummaryInList(state.analysis);
      renderView(routeFromHash(), { scroll: false, focus: false });
      refreshAnalysesList();
    } catch (error) {
      hideBootOverlay();
      showOfflineState(error);
      renderView(routeFromHash(), { scroll: false, focus: false });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
