import { financialAdmission } from '../financial/research-binding.js';
/** Immutable source selection. Nothing is called ready before a server receipt. */
export function createDatasetWorkspace(C, F, { onBind }) {
  const { api, esc: e, render, toast, state: app } = C,
    { panel, note, empty, advanced } = F;
  const s = {
    cap: null,
    capRefreshing: false,
    capError: '',
    markets: [],
    marketTotal: 0,
    marketPage: 1,
    financial: [],
    financialTotal: 0,
    financialPage: 1,
    items: [],
    total: 0,
    page: 1,
    loaded: false,
    loading: false,
    error: '',
    busy: false,
    name: '财务状态预测数据集',
    market: null,
    symbols: [],
    start: '',
    end: '',
    selected: [],
    plan: null,
    job: null,
    detail: null,
    coverage: [],
    coverageTotal: 0,
    coveragePage: 1,
    definitions: [],
    request: 0,
  };
  let timer = null,
    lastRoute = '',
    detailRequest = 0,
    revision = 0,
    planRequestId = null,
    startRequestId = null;
  const current = () => app.view === 'quant' && app.quantStep === 'datasets',
    route = () => location.hash.split('/').slice(3),
    path = (part = 'source', id = '') =>
      '#quant/studio/datasets/' +
      part +
      (id ? '/' + encodeURIComponent(id) : ''),
    go = (part, id) => {
      location.hash = path(part, id);
    },
    present = () => {
      if (current()) render();
    },
    field = (id, label, body, help = '') =>
      `<label class="sq-field" for="${id}"><span>${e(label)}</span>${body}${help ? `<small>${e(help)}</small>` : ''}</label>`,
    btn = (action, label, attrs = {}) =>
      `<button type="button" class="sq-button ${attrs.primary ? 'primary' : ''}" data-ds="${e(action)}" ${attrs.disabled ? 'disabled' : ''} ${attrs.id ? `data-id="${e(attrs.id)}"` : ''}>${e(label)}</button>`;
  const names = {
      queued: '等待准备',
      running: '正在准备',
      cancel_requested: '正在取消',
      cancelled: '已取消',
      failed: '准备未完成',
      completed: '已生成数据集',
      ready: '完整来源已冻结',
    },
    steps = [
      ['source', '冻结行情'],
      ['scope', '明确范围'],
      ['financial', '财务输入'],
      ['review', '核对并准备'],
    ];
  const date = (x) => x?.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3') || '',
    compact = (x) => x.replaceAll('-', '');
  const label = (id) => s.definitions.find((x) => x.id === id)?.name || id;
  function header() {
    return `<div class="sq-page-heading"><div><span class="sq-kicker">RESEARCH DATASETS · FROZEN SOURCES</span><h1 tabindex="-1">行情与财务数据集</h1><p>选择已有的行情快照和财务状态，明确研究范围，保留完整来源。</p></div><a class="sq-button" href="#quant/studio/financial">财务输入与状态</a></div>`;
  }
  function nav() {
    const active = route()[0] || 'source';
    return `<nav class="ds-steps" aria-label="数据集准备步骤">${steps.map(([id, name], i) => `<a href="${path(id)}" ${id === active ? 'aria-current="step"' : ''}><b>${i + 1}</b>${name}</a>`).join('')}</nav>`;
  }
  function pages(type, page, total) {
    return `<div class="fin-pagination"><span>共 ${total} 项</span>${btn(type + '-prev', '上一页', { disabled: s.loading || page <= 1 })}${btn(type + '-next', '下一页', { disabled: s.loading || page * 25 >= total })}</div>`;
  }
  function source() {
    return (
      panel(
        '已有完整行情快照',
        `${note('这里复用同一工作区已完成研究保存的行情，不会重新请求数据源。')} ${s.loading && !s.loaded ? '<p role="status">正在读取冻结来源…</p>' : s.markets.length ? `<div class="ds-source-grid">${s.markets.map((x, i) => `<article class="ds-source-card ${s.market?.sourceRef.runId === x.sourceRef.runId ? 'selected' : ''}"><h3>${e(x.name)}</h3><span class="sq-status">${x.synthetic ? '明确合成数据' : '已有冻结行情'}</span><p>${e(x.scope?.symbols.length)} 个成员 · ${e(date(x.scope?.start))} — ${e(date(x.scope?.end))}</p><small>${e(x.rowCount)} 行 · ${e(x.sourceLabel)}</small>${x.eligibility.status === 'blocked' ? note(x.eligibility.reasonCodes.join(' / '), 'warning') : btn('choose-market', '选择该快照', { id: String(i), primary: s.market?.sourceRef.runId === x.sourceRef.runId })}</article>`).join('')}</div>` : s.loaded ? empty('没有可组成的行情快照', '先完成一项保存完整行情的预测研究，再选择其冻结来源。', '<a class="sq-button" href="#quant/universe">打开研究范围</a>') : ''}${s.loaded ? pages('market', s.marketPage, s.marketTotal) : ''}`,
      ) +
      panel(
        '已冻结的数据集',
        s.loading && !s.loaded
          ? '<p role="status">正在读取数据集…</p>'
          : s.items.length
            ? `<div class="fin-source-list">${s.items.map((x) => `<article><div><a href="${path('dataset', x.datasetRef.datasetId)}?root=${x.datasetRef.datasetRoot}"><strong>${e(x.name)}</strong></a><p>${e(x.scope.symbols.length)} 个成员 · ${e(date(x.scope.start))}–${e(date(x.scope.end))}</p></div><span class="sq-status">${e(names[x.status] || x.status)}</span>${btn('open-dataset', '查看覆盖', { id: x.datasetRef.datasetId })}</article>`).join('')}</div>${pages('list', s.page, s.total)}`
            : s.loaded
              ? empty(
                  '尚无已完成数据集',
                  '新准备不会替换原始来源，也不会自动开始模型运行。',
                )
              : '',
      )
    );
  }
  function scope() {
    if (!s.market) return note('先选择一项完整行情快照。', 'warning');
    const original = s.market.scope;
    return panel(
      '本次研究范围',
      `${note('原快照完整保留。缩小日期或成员会创建明确的子范围；不会自动补齐历史或偷偷取前几只。')}<dl class="fin-summary"><dt>原始范围</dt><dd>${e(original.symbols.length)} 个成员 · ${e(date(original.start))} — ${e(date(original.end))}</dd><dt>当前选择</dt><dd>${s.symbols.length} 个明确成员</dd></dl><div class="sq-form-grid">${field('ds-start', '开始日期', `<input id="ds-start" type="date" data-ds-input="start" min="${date(original.start)}" max="${date(original.end)}" value="${date(s.start)}">`)}${field('ds-end', '结束日期', `<input id="ds-end" type="date" data-ds-input="end" min="${date(original.start)}" max="${date(original.end)}" value="${date(s.end)}">`)}</div><div class="ds-members">${original.symbols.map((x) => `<label class="fin-checkbox"><input type="checkbox" data-ds-symbol="${e(x)}" ${s.symbols.includes(x) ? 'checked' : ''}>${e(x)}</label>`).join('')}</div><div class="sq-actions">${btn('scope-all', '使用全部成员')}${btn('scope-next', '下一步：财务输入', { primary: true })}</div>`,
    );
  }
  function compatible(x) {
    const selection = x.selection;
    return (
      !!selection &&
      selection.start === s.start &&
      selection.end === s.end &&
      selection.symbols.every((x) => s.symbols.includes(x))
    );
  }
  function financial() {
    return panel(
      '已完成准备的财务输入',
      `${note('财务准备区间必须与本次范围完全一致；成员可覆盖其中一部分。缺失值仍保留，不代表模型已有足够训练数据。')} ${s.loading && !s.loaded ? '<p role="status">正在读取财务来源…</p>' : s.financial.length ? `<div class="ds-source-grid">${s.financial.map((x, i) => `<article class="ds-source-card ${s.selected.some((a) => a.financialRef.preparationId === x.financialRef.preparationId) ? 'selected' : ''}"><h3>${e(x.name)}</h3><p>${e(x.selection?.symbols.join('、'))}</p><p>${e(date(x.selection?.start))} — ${e(date(x.selection?.end))}</p><small>${e(x.selection?.selectedStateIds.length)} 项状态 · ${x.unitPolicy === 'allow_declared' ? '含未核验单位假设' : '严格单位证据'}</small><p>${x.hasUsableStates ? '存在可用状态，样本条件待检验' : '全部缺失，仍可保留完整证据'}</p>${compatible(x) ? btn('toggle-financial', s.selected.some((a) => a.financialRef.preparationId === x.financialRef.preparationId) ? '移除此输入' : '加入组成', { id: String(i) }) : `<p class="sq-note warning">日期或成员不匹配，请明确修订并重新准备。</p><a class="sq-button" href="#quant/studio/financial/${e(x.financialRef.inputId)}/states">打开财务输入</a>`}</article>`).join('')}</div>${pages('financial', s.financialPage, s.financialTotal)}` : s.loaded ? empty('尚无已准备财务输入', '先完成输入核验与状态准备，再回来组成数据集。', '<a class="sq-button" href="#quant/studio/financial">打开财务输入</a>') : ''}<div class="sq-actions"><span>已选择 ${s.selected.length} / 8 项</span>${btn('review', '下一步：核对来源', { primary: true, disabled: !s.selected.length })}</div>`,
    );
  }
  function review() {
    return panel(
      '明确冻结这次组成',
      `${field('ds-name', '数据集名称', `<input id="ds-name" data-ds-input="name" maxlength="80" value="${e(s.name)}">`)}<dl class="fin-summary"><dt>行情来源</dt><dd>${e(s.market?.name || '尚未选择')}</dd><dt>新范围</dt><dd>${e(s.symbols.join('、'))} · ${e(date(s.start))}–${e(date(s.end))}</dd><dt>财务输入</dt><dd>${s.selected.length} 项不可变准备版本</dd><dt>数据请求</dt><dd>0 次新供应商请求</dd><dt>证据边界</dt><dd>供应商历史原始版本和修订时点尚未核实；单位假设不升级为已核验。</dd></dl>${s.plan ? note(`来源引用已核对。将处理约 ${(s.plan.knownSourceBytes / 1024 / 1024).toFixed(2)} MiB 来源；实际组成、日历一致性与缺失覆盖仍需计算服务验证。`) : note('核对会固定来源版本和明确子范围。准备完成后，才可查看真实覆盖。')}<div class="sq-actions">${btn('plan', s.busy ? '正在核对…' : '核对来源与预算', { disabled: s.busy || !s.cap?.enabled, primary: !s.plan })}${btn('start', s.busy ? '处理中…' : '明确开始准备', { disabled: s.busy || s.capRefreshing || !s.plan || !s.cap?.enabled || !s.cap?.composition.online, primary: true })}${btn('refresh-capabilities', s.capRefreshing ? '正在刷新节点状态…' : '刷新节点状态', { disabled: s.busy || s.capRefreshing })}</div><div role="status" aria-live="polite">${s.capRefreshing ? '<p>正在读取准备节点状态，已核对的计划保持不变…</p>' : s.capError ? note(s.capError, 'warning') : s.cap?.composition.online ? note('数据集准备节点在线。') : s.cap ? note('数据集准备节点当前不在线；可刷新状态，已选范围和输入保持不变。', 'warning') : ''}</div>`,
    );
  }
  function job() {
    if (!s.job) return '<p role="status">正在读取准备任务…</p>';
    const x = s.job.preparation;
    return panel(
      '数据集准备',
      `<div class="fin-progress" role="status"><strong>${e(names[x.status] || x.status)}</strong><span>${e({ checking_sources: '核对完整来源', deriving_scope: '生成明确子范围', composing_states: '组合财务状态', writing_evidence: '保存完整闭包' }[x.phase] || '等待准备节点')}</span></div>${x.error ? note(x.error.message, 'error') : ''}${s.job.datasetRef ? btn('open-job-dataset', '查看覆盖与来源', { primary: true }) : ['queued', 'running', 'cancel_requested'].includes(x.status) ? btn('cancel', '取消准备', { disabled: s.busy || x.status === 'cancel_requested' }) : '<p>未生成可用数据集。原始来源与已完成证据保持不变。</p>'}`,
    );
  }
  function detail() {
    if (!s.detail) return '<p role="status">正在读取冻结数据集…</p>';
    const d = s.detail, automatic = financialAdmission(d), ridge = financialAdmission(d, 'ridge');
    return (
      panel(
        d.name,
        `<dl class="fin-summary"><dt>范围</dt><dd>${e(d.scope.symbols.join('、'))} · ${e(date(d.scope.start))}–${e(date(d.scope.end))}</dd><dt>完整行情行数</dt><dd>${e(d.summary.marketRows)}</dd><dt>可用股票×状态</dt><dd>${e(d.summary.availableStateCoverage)} / ${e(d.summary.stateCoverage)}</dd><dt>来源闭包</dt><dd>原行情快照、显式子范围、财务输入与准备、日历授权均保留</dd></dl>${note('覆盖可用不等于预测有效。原始发布版本与修订时点未核验；用户声明的单位仍是假设。')}<div class="sq-actions"><a class="sq-button" href="${e(d.archiveUrl)}" download>下载完整数据集闭包</a>${btn('bind', '创建自动拟合因子研究', { primary: true, disabled: s.busy || !automatic.available })}</div>${automatic.available ? note('以已冻结范围创建基本面自动拟合研究。输入来自实际财务状态；不重新取数，不执行交易。') : note(automatic.reason || '自动拟合协议尚未就绪。', 'warning')}${advanced('Studio · 明确使用固定估计器', '<p>这是单独声明的 Ridge 研究入口。选择不会改变已有研究版本。</p>' + btn('bind-ridge', '在 Studio 创建 Ridge 研究', { disabled: s.busy || !ridge.available }) + (!ridge.available ? note(ridge.reason || 'Ridge 协议尚未就绪。') : ''))}${advanced('不可变数据身份', `<code>${e(d.datasetRef.datasetRoot)}</code>`)}`,
      ) +
      panel(
        '逐状态覆盖',
        `<div class="table-scroll"><table><thead><tr><th>成员 / 状态</th><th>有效 / 缺失</th><th>实际观察覆盖</th><th>最新报告期</th></tr></thead><tbody>${s.coverage.map((x) => `<tr><td>${e(x.symbol)}<br>${e(label(x.stateId))}</td><td>${x.okRows} / ${x.missingRows}</td><td>${e(date(x.firstObserved) || '无')}–${e(date(x.lastObserved) || '无')}</td><td>${e(date(x.latestPeriodEnd) || '无')}</td></tr>`).join('')}</tbody></table></div>${pages('coverage', s.coveragePage, s.coverageTotal)}<p>观察覆盖按实际非缺失会话计算，原披露可用日期保留在完整证据中。</p>`,
      )
    );
  }
  function view() {
    return (
      header() +
      (s.error ? note(s.error, 'error') + btn('retry', '重新读取') : '') +
      (s.loading && s.loaded
        ? '<p role="status">正在更新来源列表，保留当前选择…</p>'
        : '') +
      (s.cap && !s.cap.enabled
        ? note(
            '数据集准备尚未开放。已有冻结记录可读取；不会自动请求或生成数据。',
          )
        : '') +
      (['job', 'dataset'].includes(route()[0]) ? '' : nav()) +
      (
        { scope, financial, review, job, dataset: detail }[route()[0]] || source
      )()
    );
  }
  const apiRoot = '/datasets';
  async function list() {
    s.loading = true;
    s.error = '';
    present();
    const token = ++s.request;
    try {
      const cap = await api('/dataset-capabilities');
      if (token !== s.request) return;
      s.cap = cap;
      const jobs = [
        api(`/datasets?page=${s.page}&pageSize=25`),
        api('/financial/definitions'),
      ];
      if (cap.enabled)
        jobs.push(
          api(`/datasets/sources/markets?page=${s.marketPage}&pageSize=25`),
          api(
            `/datasets/sources/financial?page=${s.financialPage}&pageSize=25`,
          ),
        );
      const data = await Promise.all(jobs);
      if (token !== s.request) return;
      [s.items, s.total] = [data[0].items, data[0].total];
      s.definitions = data[1].items;
      if (cap.enabled) {
        [s.markets, s.marketTotal] = [data[2].items, data[2].total];
        [s.financial, s.financialTotal] = [data[3].items, data[3].total];
      }
      s.loaded = true;
    } catch (err) {
      if (token === s.request) s.error = err.message;
    } finally {
      if (token === s.request) {
        s.loading = false;
        present();
      }
    }
  }
  async function loadJob(id) {
    const x = await api('/dataset-preparations/' + encodeURIComponent(id));
    if (!current() || route()[0] !== 'job' || route()[1] !== id) return;
    s.job = x;
    present();
    if (
      ['queued', 'running', 'cancel_requested'].includes(x.preparation.status)
    )
      timer = setTimeout(() => loadJob(id).catch(error), 3000);
  }
  async function loadDetail(id, root) {
    const token = ++detailRequest;
    const q = '?datasetRoot=' + encodeURIComponent(root),
      [d, c] = await Promise.all([
        api('/datasets/' + encodeURIComponent(id) + q),
        api(
          '/datasets/' +
            encodeURIComponent(id) +
            '/coverage' +
            q +
            `&page=${s.coveragePage}&pageSize=25`,
        ),
      ]);
    if (
      token !== detailRequest ||
      !current() ||
      route()[0] !== 'dataset' ||
      route()[1] !== id + '?root=' + root
    )
      return;
    s.detail = d;
    s.coverage = c.items;
    s.coverageTotal = c.total;
    present();
  }
  function error(err) {
    s.error = err.message;
    present();
  }
  function invalidate() {
    s.plan = null;
    revision++;
    planRequestId = null;
    startRequestId = null;
  }
  function scopeCheck() {
    if (
      !s.market ||
      !s.symbols.length ||
      s.symbols.length > 50 ||
      s.start < s.market.scope.start ||
      s.end > s.market.scope.end ||
      s.start > s.end
    )
      throw Error('请选择原快照内的1–50个成员和有效日期。');
  }
  async function act(action, el) {
    if (s.busy) return;
    if (action === 'choose-market') {
      s.market = s.markets[Number(el.dataset.id)];
      s.symbols = [...s.market.scope.symbols].sort();
      s.start = s.market.scope.start;
      s.end = s.market.scope.end;
      s.selected = [];
      invalidate();
      go('scope');
      return;
    }
    if (action === 'scope-all') {
      s.symbols = [...s.market.scope.symbols].sort();
      invalidate();
      present();
      return;
    }
    if (action === 'scope-next') {
      scopeCheck();
      s.selected = s.selected.filter(compatible);
      go('financial');
      return;
    }
    if (action === 'toggle-financial') {
      const x = s.financial[Number(el.dataset.id)],
        i = s.selected.findIndex(
          (a) => a.financialRef.preparationId === x.financialRef.preparationId,
        );
      if (i >= 0) s.selected.splice(i, 1);
      else if (s.selected.length < 8) s.selected.push(x);
      else throw Error('最多选择8项准备输入。');
      invalidate();
      present();
      return;
    }
    if (action === 'review') {
      scopeCheck();
      go('review');
      return;
    }
    if (action === 'refresh-capabilities') {
      if (s.capRefreshing) return;
      s.capRefreshing = true;
      s.capError = '';
      present();
      try {
        // Advisory service status only: never invalidate the frozen plan or
        // either idempotency key when a worker heartbeat changes.
        s.cap = await api('/dataset-capabilities');
      } catch (err) {
        s.capError = '节点状态读取失败：' + err.message;
      } finally {
        s.capRefreshing = false;
        present();
      }
      return;
    }
    if (action === 'retry') {
      return routeChanged(true);
    }
    if (action === 'open-dataset') {
      const x = s.items.find((x) => x.datasetRef.datasetId === el.dataset.id);
      location.hash =
        path('dataset', x.datasetRef.datasetId) +
        '?root=' +
        x.datasetRef.datasetRoot;
      return;
    }
    if (action === 'open-job-dataset') {
      const x = s.job.datasetRef;
      location.hash =
        path('dataset', x.datasetId) +
        '?root=' +
        (x.datasetRef?.datasetRoot || x.datasetRoot);
      return;
    }
    if (/^(market|financial|list|coverage)-(prev|next)$/.test(action)) {
      const [kind, direction] = action.split('-'),
        key = {
          market: 'marketPage',
          financial: 'financialPage',
          list: 'page',
          coverage: 'coveragePage',
        }[kind];
      s[key] += direction === 'next' ? 1 : -1;
      return kind === 'coverage'
        ? loadDetail(
            s.detail.datasetRef.datasetId,
            s.detail.datasetRef.datasetRoot,
          )
        : list();
    }
    s.busy = true;
    s.error = '';
    present();
    try {
      if (action === 'plan') {
        const submittedRevision = revision;
        planRequestId ||= crypto.randomUUID();
        scopeCheck();
        if (!s.selected.length || s.selected.some((x) => !compatible(x)))
          throw Error('选择1–8项与当前范围匹配的财务输入。');
        const o = s.market.scope,
          mode =
            s.start === o.start &&
            s.end === o.end &&
            JSON.stringify(s.symbols) === JSON.stringify([...o.symbols].sort())
              ? 'exact'
              : 'explicit_subset';
        const result = await api('/dataset-plans', {
          method: 'POST',
          body: JSON.stringify({
            requestId: planRequestId,
            name: s.name,
            profile: s.cap.profile,
            marketSource: {
              ...s.market.sourceRef,
              transform: {
                kind: 'snapshot_scope_view',
                version: 1,
                mode,
                symbols: [...s.symbols],
                start: s.start,
                end: s.end,
              },
            },
            financialInputs: s.selected.map((x) => x.financialRef),
          }),
        });
        if (submittedRevision === revision) s.plan = result.plan;
        else toast('核对期间范围已改变；请重新核对当前选择。');
      } else if (action === 'start') {
        startRequestId ||= crypto.randomUUID();
        const result = await api(`/dataset-plans/${s.plan.id}/start`, {
          method: 'POST',
          body: JSON.stringify({
            requestId: startRequestId,
            expectedPlanRoot: s.plan.planRoot,
          }),
        });
        s.job = result;
        go('job', result.preparation.id);
      } else if (action === 'cancel')
        await api(`/dataset-preparations/${s.job.preparation.id}/cancel`, {
          method: 'POST',
          body: '{}',
        });
      else if (action === 'bind' || action === 'bind-ridge') {
        const original = s.detail, routeAtStart = location.hash, draft = app.strategy,
          fingerprint = JSON.stringify(app.strategy), source = app.dataSource,
          workspaceId = app.session?.workspace?.id, estimator = action === 'bind-ridge' ? 'ridge' : 'auto';
        if (!workspaceId) throw Error('等待私有工作区身份确认后再绑定财务数据。');
        const fresh = await api(`/datasets/${encodeURIComponent(original.datasetRef.datasetId)}?datasetRoot=${encodeURIComponent(original.datasetRef.datasetRoot)}`);
        if (location.hash !== routeAtStart || s.detail !== original || app.strategy !== draft || JSON.stringify(app.strategy) !== fingerprint || app.dataSource !== source || app.session?.workspace?.id !== workspaceId) throw Error('草稿、数据来源或工作区已变化，财务数据尚未绑定；当前修改保留。');
        if (JSON.stringify(fresh.datasetRef) !== JSON.stringify(original.datasetRef) || JSON.stringify(fresh.scope) !== JSON.stringify(original.scope)) throw Error('返回的财务数据身份或冻结范围不一致。');
        const selected = financialAdmission(fresh, estimator);
        s.detail = fresh;
        if (!selected.available) throw Error(selected.reason || '所选拟合协议不可用。');
        await onBind(fresh, fresh.summary.selectedStateIds, s.definitions, { estimator });
      }
    } finally {
      s.busy = false;
      present();
    }
  }
  function routeChanged(force = false) {
    clearTimeout(timer);
    timer = null;
    if (!current()) return;
    const hash = location.hash;
    if (!force && hash === lastRoute) return;
    lastRoute = hash;
    const [page, idAndQuery] = route(),
      [id, query = ''] = (idAndQuery || '').split('?');
    if (!s.cap && ['job', 'dataset'].includes(page)) list();
    if (page === 'job') {
      s.job = null;
      loadJob(id).catch(error);
    } else if (page === 'dataset') {
      s.detail = null;
      s.coverage = [];
      s.coveragePage = 1;
      loadDetail(id, new URLSearchParams(query).get('root')).catch(error);
    } else if (page === 'source' || !s.loaded) list();
    present();
  }
  document.addEventListener('click', (event) => {
    const el = event.target.closest('[data-ds]');
    if (!current() || !el || el.disabled) return;
    event.preventDefault();
    Promise.resolve(act(el.dataset.ds, el)).catch(error);
  });
  document.addEventListener('input', (event) => {
    if (!current()) return;
    const el = event.target;
    if (el.dataset.dsInput) {
      s[el.dataset.dsInput] = ['start', 'end'].includes(el.dataset.dsInput)
        ? compact(el.value)
        : el.value;
      invalidate();
    }
    if (el.dataset.dsSymbol) {
      s.symbols = el.checked
        ? [...new Set([...s.symbols, el.dataset.dsSymbol])].sort()
        : s.symbols.filter((x) => x !== el.dataset.dsSymbol);
      invalidate();
    }
  });
  return {
    render: view,
    routeChanged,
    state: s,
    dispose() {
      clearTimeout(timer);
      s.request++;
      detailRequest++;
    },
  };
}
