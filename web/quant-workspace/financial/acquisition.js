/** Self-service source acquisition. No browser credentials or fabricated ready state. */
export function createFinancialAcquisition(C, F) {
  const { esc: e, api, render, toast, state: app } = C,
    { panel, note, empty, advanced } = F;
  const year = new Date().getFullYear() - 1,
    today = new Date().toISOString().slice(0, 10);
  const state = {
    loaded: false,
    loading: false,
    cap: null,
    definitions: [],
    error: "",
    busy: false,
    plan: null,
    job: null,
    receipts: [],
    jobs: null,
    draft: {
      name: "年度财务来源",
      symbols: "",
      year: String(year),
      announcementStart: `${year + 1}-01-01`,
      start: `${year + 1}-01-01`,
      end: today,
      selectedStateIds: [],
    },
    revision: 0,
  };
  const active = () =>
    app.view === "quant" &&
    app.quantStep === "financial" &&
    location.hash.split("/")[3] === "acquire";
  const route = (step = "source", id = "") =>
    "#quant/studio/financial/acquire/" +
    step +
    (id ? "/" + encodeURIComponent(id) : "");
  const step = () => location.hash.split("/")[4] || "source";
  const present = () => {
    if (active()) render();
  };
  const btn = (action, label, disabled = false) =>
    `<button type="button" class="sq-button ${action === "start" || action === "plan" ? "primary" : ""}" data-acq="${action}" ${disabled ? "disabled" : ""}>${e(label)}</button>`;
  const field = (key, label, help = "", type = "text") =>
    `<label class="sq-field" for="acq-${key}"><span>${e(label)}</span><input id="acq-${key}" data-acq-field="${key}" type="${type}" value="${e(state.draft[key])}" ${key === "name" ? 'maxlength="80"' : ""}>${help ? `<small>${e(help)}</small>` : ""}</label>`;
  const status = (x) =>
    ({
      queued: "等待获取节点",
      running: "正在获取与冻结",
      completed: "来源已冻结，待校验",
      failed: "获取未完成",
      cancel_requested: "正在停止",
      cancelled: "已停止",
    })[x] || x;
  let request = 0,
    timer = null;
  const stop = () => {
    clearTimeout(timer);
    timer = null;
  };
  function sourcePage() {
    return panel(
      "1. 明确来源范围",
      note(
        "当前范围：Tushare 授权数据，同一交易所的 1–2 家沪深一般工商业公司、一个十二月年报期、合并报表。不适用于银行等其他公司类型，也不会自动移除没有数据的股票。",
      ) +
        `<div class="sq-form-grid">${field("name", "输入名称")}${field("symbols", "股票代码", "例如 600690.SH；两个代码以逗号或空格分隔。")}${field("year", "年报年份", "所选年份必须已经结束。", "number")}${field("announcementStart", "公告历史起点", "这是可知历史的下界；更早公告不会向后填充。", "date")}${field("start", "状态观察开始", "必须在公告历史起点之后。", "date")}${field("end", "状态观察结束", "公告起点至观察结束最多 366 日。", "date")}</div>` +
        `<fieldset class="acq-states"><legend>需要准备的状态定义</legend>${state.definitions.map((x) => `<label><input type="checkbox" data-acq-state="${e(x.id)}" ${state.draft.selectedStateIds.includes(x.id) ? "checked" : ""}><span>${e(x.name)}</span></label>`).join("")}</fieldset>` +
        note(
          "单期年报可能缺少季度或 TTM 历史。获取并不核验单位，未获得证据的数值仍会缺失；你可在冻结后明确记录单位假设。",
        ) +
        `<div class="sq-actions">${btn("plan", state.busy ? "正在核对请求…" : "查看实际请求与预算", state.busy || !state.cap?.enabled)}</div>`,
    );
  }
  function reviewPage() {
    const p = state.plan;
    if (!p)
      return (
        empty("还没有核对计划", "先选择来源范围。") +
        `<a class="sq-button" href="${route()}">返回选择</a>`
      );
    const names = {
      income: "利润表",
      balancesheet: "资产负债表",
      cashflow: "现金流量表",
      trade_cal: "交易日历",
    };
    return panel(
      "2. 核对不可变的获取计划",
      `<dl class="fin-summary"><dt>股票</dt><dd>${e(p.selection.symbols.join("、"))}</dd><dt>年报期</dt><dd>${e(p.selection.period)}</dd><dt>观察区间</dt><dd>${e(p.selection.start)}–${e(p.selection.end)}</dd><dt>供应商请求</dt><dd>最多 ${p.budget.maximumProviderCalls} 次；${p.budget.cachedRequests} 项已有冻结缓存，${p.budget.newRequests} 项需要读取</dd><dt>响应预算</dt><dd>每次最多 4 MiB，合计最多 16 MiB；不自动重试</dd></dl>` +
        `<div class="sq-table-wrap"><table><thead><tr><th>来源</th><th>明确范围</th><th>当前冻结记录</th></tr></thead><tbody>${p.requests.map((x) => `<tr><td>${e(names[x.endpoint] || x.endpoint)}</td><td>${e(x.params.ts_code || x.params.exchange)} · ${e(x.params.period || `${x.params.start_date}–${x.params.end_date}`)}</td><td>${e({ frozen: "复用冻结响应", missing: "尚未读取", in_progress: "来源请求进行中", outcome_unknown: "结果未知，需人工核对", failed: "需要人工核对", blocked: "来源暂不可用" }[x.cache.status] || x.cache.status)}</td></tr>`).join("")}</tbody></table></div>` +
        (state.cap.budget
          ? note(
              `今日来源请求剩余 ${state.cap.budget.newProviderRequestsRemaining} / ${state.cap.budget.maxNewProviderRequestsPerUtcDay} 次（UTC ${state.cap.budget.utcDay}）。冻结缓存与回执重传不重复计数；实际开始时会重新核对额度。`,
            )
          : "") +
        (p.blockedReasons.length
          ? note(
              "同一授权范围中已有未核清的请求。不能创建另一个计划绕过，也不会自动再次读取。请由服务维护者核对原始回执。",
              "warning",
            )
          : "") +
        note(
          "点击开始将授权此清单中的未缓存请求。完成后生成冻结输入与专属日历，再由你校验字段、单位和披露日期；不会启动模型研究。",
        ) +
        advanced(
          "请求身份与字段清单",
          `<p><code>${e(p.planRoot)}</code></p>${p.requests.map((x) => `<details><summary>${e(x.endpoint)} · ${e(x.params.ts_code || x.params.exchange)}</summary><p>${e(x.fields.join("、"))}</p><code>${e(x.requestKey)}</code></details>`).join("")}`,
        ) +
        `<div class="sq-actions"><a class="sq-button" href="${route()}">修改范围</a>${btn("start", state.busy ? "正在提交…" : "按此清单开始获取", state.busy || !state.cap?.enabled || !state.cap?.runner.online || state.cap?.maintenancePaused || !!p.blockedReasons.length)}</div>`,
    );
  }
  function jobPage() {
    const job = state.job;
    if (!job) return '<p role="status">正在读取获取任务…</p>';
    const waiting = ["queued", "running", "cancel_requested"].includes(
      job.status,
    );
    return panel(
      "3. 获取进度与冻结输入",
      `<div class="fin-progress" role="status"><strong>${e(status(job.status))}</strong><span>${e({ checking_plan: "核对请求与授权范围", fetching_sources: "读取并保存原始回执", normalizing: "核对报表范围与日历", writing_evidence: "保存冻结输入", queued: "等待独立获取节点" }[job.phase] || "")}</span></div>` +
        `<p>已冻结 ${state.receipts.filter((x) => x.status === "received").length} 项来源回执；未完成的请求不会冒充可用输入。</p>` +
        (job.error
          ? note(`${job.error.code} · ${job.error.message}`, "warning")
          : "") +
        (job.error?.code?.includes("UNKNOWN") ||
        job.error?.code === "ACQUISITION_LEASE_EXPIRED"
          ? note(
              "已发出但缺少完整回执的请求不会自动重试。已完成来源保留；未知请求需要人工核对。",
            )
          : "") +
        (job.status === "completed" && job.result?.inputId
          ? `<p>原始回执、输入包和交易日历已冻结。当前单位尚未核验，输入也尚未通过财务准备校验。</p><a class="sq-button primary" href="#quant/studio/financial/${e(job.result.inputId)}/source">打开冻结输入并校验</a>`
          : "") +
        `<div class="sq-actions">${waiting ? btn("cancel", "停止后续获取", state.busy || job.status === "cancel_requested") : ""}${btn("refresh", "刷新任务", state.busy)}<a class="sq-button" href="${route()}">返回来源选择</a></div>` +
        advanced(
          "来源任务身份",
          `<dl class="fin-summary"><dt>任务</dt><dd><code>${e(job.id)}</code></dd><dt>计划</dt><dd><code>${e(job.planId)}</code></dd><dt>更新时间</dt><dd>${e(job.updatedAt)}</dd></dl>`,
        ),
    );
  }
  function renderView() {
    return (
      `<div class="fin-workspace acq-workspace"><div class="sq-page-heading"><div><span class="sq-kicker">FINANCIAL SOURCE ACQUISITION</span><h1 id="acq-title" tabindex="-1">获取财务研究来源</h1><p>先核对请求范围，再冻结来源；证据与单位政策保持可追溯。</p></div><a class="sq-button" href="#quant/studio/financial">全部财务输入</a></div>` +
      `<nav class="fin-step-tabs" aria-label="来源获取步骤"><a href="${route()}" ${step() === "source" ? 'aria-current="step"' : ""}>1. 来源与范围</a><a href="${route("review", state.plan?.id || "")}" ${step() === "review" ? 'aria-current="step"' : ""}>2. 请求与预算</a><span ${step() === "job" ? 'aria-current="step"' : ""}>3. 获取与冻结</span></nav>` +
      (state.error
        ? note(state.error, "error") + btn("reload", "重新读取服务状态")
        : "") +
      (!state.loaded
        ? '<p role="status">正在读取获取能力与定义…</p>'
        : (state.cap.testFixtureMode
            ? note(
                "合成验收环境：记录明确标记为测试数据，不代表真实公司或交易所证据。",
                "warning",
              )
            : "") +
          (!state.cap.enabled
            ? note(
                "当前尚未开放自助获取。已有冻结包仍可通过财务输入工作区导入；不会发送供应商请求。",
                "warning",
              )
            : state.cap.maintenancePaused
              ? note(
                  "来源获取正在维护，已核对计划保留；维护结束后可开始。",
                  "warning",
                )
              : !state.cap.runner.online
                ? note(
                    "独立来源获取节点暂未在线。可以核对计划，确认节点在线后才能开始读取。",
                    "warning",
                  )
                : "") +
          (step() === "review"
            ? reviewPage()
            : step() === "job"
              ? jobPage()
              : sourcePage())) +
      (step() === "source" && state.jobs
        ? panel(
            "当前工作区的获取记录",
            state.jobs.items.length
              ? `<div class="fin-source-list">${state.jobs.items.map((x) => `<article><div><strong>${e(status(x.status))}</strong><small>${e(x.createdAt)}</small></div><a class="sq-button" href="${route("job", x.id)}">查看记录</a></article>`).join("")}</div>`
              : empty("尚无来源获取任务", "核对计划本身不会访问供应商。"),
          )
        : "") +
      "</div>"
    );
  }
  async function boot() {
    state.loading = true;
    present();
    const [cap, defs, jobs] = await Promise.all([
      api("/financial/acquisition-capabilities"),
      api("/financial/definitions"),
      api("/financial/acquisition-jobs?page=1&pageSize=25"),
    ]);
    state.cap = cap;
    state.definitions = defs.items;
    state.jobs = jobs;
    state.loaded = true;
    state.loading = false;
  }
  async function loadJob(id) {
    const n = ++request,
      data = await api("/financial/acquisition-jobs/" + encodeURIComponent(id));
    if (
      !active() ||
      step() !== "job" ||
      location.hash.split("/")[5] !== id ||
      n !== request
    )
      return;
    state.job = data.job;
    state.receipts = data.receipts || [];
    present();
    stop();
    if (["queued", "running", "cancel_requested"].includes(data.job.status))
      timer = setTimeout(() => loadJob(id).catch(report), 4000);
  }
  function report(error) {
    state.error = error.message || String(error);
    state.busy = false;
    state.loading = false;
    present();
  }
  async function routeChanged() {
    stop();
    request++;
    state.error = "";
    if (!active()) return;
    try {
      if (!state.loaded) await boot();
      if (!active()) return;
      if (step() === "source") {
        const currentRequest = ++request;
        const data = await api(
          "/financial/acquisition-jobs?page=1&pageSize=25",
        );
        if (!active() || step() !== "source" || currentRequest !== request)
          return;
        state.jobs = data;
      }
      if (step() === "review") {
        const id = location.hash.split("/")[5];
        if (id && state.plan?.id !== id) {
          const currentRequest = ++request;
          const data = await api(
            "/financial/acquisition-plans/" + encodeURIComponent(id),
          );
          if (
            !active() ||
            step() !== "review" ||
            location.hash.split("/")[5] !== id ||
            currentRequest !== request
          )
            return;
          state.plan = data.plan;
          const p = data.plan.selection,
            day = (value) =>
              value.slice(0, 4) +
              "-" +
              value.slice(4, 6) +
              "-" +
              value.slice(6);
          state.draft = {
            name: data.plan.name,
            symbols: p.symbols.join(", "),
            year: p.period.slice(0, 4),
            announcementStart: day(p.announcementStart),
            start: day(p.start),
            end: day(p.end),
            selectedStateIds: [...p.selectedStateIds],
          };
          state.revision++;
        }
      }
      if (step() === "job") {
        const id = location.hash.split("/")[5];
        if (state.job?.id !== id) state.job = null;
        present();
        if (id) await loadJob(id);
      } else present();
      document.getElementById("acq-title")?.focus({ preventScroll: true });
    } catch (error) {
      report(error);
    }
  }
  function planValue() {
    const d = state.draft,
      symbols = d.symbols
        .toUpperCase()
        .split(/[\s,，;；]+/)
        .filter(Boolean);
    if (
      !symbols.length ||
      symbols.length > 2 ||
      symbols.some((x) => !/^\d{6}\.(SH|SZ)$/.test(x)) ||
      new Set(symbols).size !== symbols.length
    )
      throw Error("请输入一到两只唯一 SH/SZ 股票代码。");
    if (new Set(symbols.map((x) => x.slice(-2))).size !== 1)
      throw Error("当前范围要求同一交易所，不会替换跨市场日历。");
    if (!/^\d{4}$/.test(d.year)) throw Error("请输入四位年报年份。");
    if (!d.selectedStateIds.length) throw Error("至少选择一个财务状态定义。");
    return {
      profile: "annual_statements_2_v1",
      name: d.name.trim(),
      symbols,
      period: d.year + "1231",
      announcementStart: d.announcementStart.replaceAll("-", ""),
      start: d.start.replaceAll("-", ""),
      end: d.end.replaceAll("-", ""),
      selectedStateIds: [...d.selectedStateIds],
    };
  }
  const post = (url, data) =>
    api(url, { method: "POST", body: JSON.stringify(data) });
  async function act(action) {
    if (state.busy) return;
    state.busy = true;
    state.error = "";
    present();
    try {
      if (action === "reload") {
        await boot();
        if (step() === "job") await loadJob(location.hash.split("/")[5]);
      }
      if (action === "plan") {
        const value = planValue(),
          fingerprint = JSON.stringify(value),
          revision = state.revision;
        if (state.pendingPlan?.fingerprint !== fingerprint)
          state.pendingPlan = { fingerprint, requestId: crypto.randomUUID() };
        const { plan } = await post("/financial/acquisition-plans", {
          requestId: state.pendingPlan.requestId,
          ...value,
        });
        if (revision !== state.revision) {
          toast("计划已保存；范围刚发生修改，请核对当前范围后重新查看预算。");
          return;
        }
        state.plan = plan;
        state.pendingPlan = null;
        if (active()) location.hash = route("review", state.plan?.id || "");
        else toast("获取计划已保存，可返回财务来源继续核对。");
      }
      if (action === "start") {
        if (!state.plan) throw Error("请先核对计划。");
        if (state.pendingStart?.planId !== state.plan.id)
          state.pendingStart = {
            planId: state.plan.id,
            requestId: crypto.randomUUID(),
          };
        const { job } = await post(
          `/financial/acquisition-plans/${state.plan.id}/start`,
          {
            requestId: state.pendingStart.requestId,
            expectedPlanRoot: state.plan.planRoot,
          },
        );
        state.job = job;
        state.pendingStart = null;
        if (active()) location.hash = route("job", job.id);
        else toast("获取任务已提交，可在财务来源记录中查看。");
      }
      if (action === "refresh") await loadJob(state.job.id);
      if (action === "cancel") {
        const { job } = await post(
          "/financial/acquisition-jobs/" + state.job.id + "/cancel",
          {},
        );
        state.job = job;
        await loadJob(job.id);
      }
    } catch (error) {
      report(error);
    } finally {
      state.busy = false;
      present();
    }
  }
  document.addEventListener("click", (event) => {
    const el = event.target.closest("[data-acq]");
    if (!active() || !el || el.disabled) return;
    event.preventDefault();
    act(el.dataset.acq);
  });
  document.addEventListener("input", (event) => {
    if (!active()) return;
    const el = event.target;
    if (el.dataset.acqField) {
      state.draft[el.dataset.acqField] = el.value;
      state.revision++;
      state.plan = null;
    }
  });
  document.addEventListener("change", (event) => {
    if (!active()) return;
    const id = event.target.dataset.acqState;
    if (id) {
      state.draft.selectedStateIds = event.target.checked
        ? [...new Set([...state.draft.selectedStateIds, id])]
        : state.draft.selectedStateIds.filter((x) => x !== id);
      state.revision++;
      state.plan = null;
    }
  });
  return {
    active,
    render: renderView,
    routeChanged,
    dispose: () => {
      stop();
      request++;
    },
    state,
  };
}
