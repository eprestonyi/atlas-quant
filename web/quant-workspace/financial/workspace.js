import {createFinancialAcquisition} from './acquisition.js';
/** Financial inputs are separate from research configurations. This slice never
 * attaches a financial component to a run or labels preparation as F readiness. */
export function createFinancialWorkspace(C, F) {
  const { esc: e, api, render, toast, state: app } = C;
  const { panel, note, empty, advanced } = F;
  const acquisition=createFinancialAcquisition(C,F);
  const state = {
    cap: null,
    definitions: null,
    calendars: [],
    items: [],
    total: 0,
    page: 1,
    loaded: false,
    loading: true,
    error: '',
    detail: null,
    sourceId: null,
    request: 0,
    busy: false,
    file: null,
    name: '',
    calendarRef: '',
    proofRefs: '',
    uploadReceipt: null,
    draft: null,
    draftId: null,
    query: '',
    prepared: null,
    coverage: null,
    events: null,
    event: null,
    dependencies: null,
    coverageCursor: null,
    eventCursor: null,
    coverageHistory: [],
    eventHistory: [],
    filterState: '',
    filterSymbol: '',
    eventStatus: 'all',
  };
  const statuses = {
    uploading: '等待上传',
    uploaded: '等待校验',
    validating: '正在校验',
    ready_to_prepare: '可生成状态',
    preparing: '正在准备',
    prepared: '状态已生成',
    blocked: '需要处理',
    queued: '排队中',
    running: '计算中',
    cancel_requested: '正在取消',
    cancelled: '已取消',
    failed: '未完成',
    completed: '已完成',
  };
  let timer = null,
    lastRoute = '';
  const current = () => app.view === 'quant' && app.quantStep === 'financial';
  const path = (id = '', stage = 'source') =>
    '#quant/studio/financial' + (id ? '/' + encodeURIComponent(id) + '/' + stage : '');
  const stage = () => location.hash.split('/')[4] || 'source';
  const btn = (action, label, { disabled = false, primary = false, ...attrs } = {}) =>
    `<button type="button" class="sq-button ${primary ? 'primary' : ''}" data-fin="${e(action)}" ${disabled ? 'disabled' : ''} ${Object.entries(
      attrs
    )
      .map(([k, v]) => `data-${e(k)}="${e(v)}"`)
      .join(' ')}>${e(label)}</button>`;
  const field = (id, label, body, help = '') =>
    `<label class="sq-field" for="${id}"><span>${e(label)}</span>${body}${help ? `<small>${e(help)}</small>` : ''}</label>`;
  const title = (text, subtitle) =>
    `<div class="sq-page-heading"><div><span class="sq-kicker">FINANCIAL INPUTS / AUDITABLE STATES</span><h1 id="financial-title" tabindex="-1">${e(text)}</h1><p>${e(subtitle)}</p></div><a class="sq-button" href="#quant/studio/state">返回状态研究</a></div>`;
  const definition = (id) => state.definitions?.items.find((x) => x.id === id);
  const label = (id) => definition(id)?.name || id;
  const selectionLabel = (value) =>
    ({
      consolidated: '合并报表',
      parent: '母公司报表',
      ytd: '年初至今累计流量',
      quarter: '独立季度流量',
    })[value] || value;
  const badge = (value) => `<span class="sq-status">${e(statuses[value] || value)}</span>`;
  function createDraft(input) {
    return {
      selection: structuredClone(input.selection),
      unitPolicy: input.unitPolicy || 'verified_only',
      declarations: {},
      confirmed: false,
    };
  }
  function busyError() {
    return state.error ? note(state.error, 'error') + btn('retry', '重新读取') : '';
  }
  function renderList() {
    const importEnabled = state.cap?.operations.upload && state.calendars.length;
    return (
      title('财务输入工作区', '冻结报表、核对单位与披露时点，再生成可追溯的研究状态。') +
      busyError() +
      panel('从授权数据源开始', '<p>先选择股票、年报期和观察区间，核对实际请求与缓存预算，再明确开始。获取能力以当前授权和服务状态为准。</p><a class="sq-button" href="#quant/studio/financial/acquire/source">查看自助来源获取</a>') +
      (state.cap && !state.cap.enabled
        ? note('财务工作区尚未启用。已有定义可以查看；当前不能上传或开始计算。')
        : '') +
      panel(
        '导入冻结输入包',
        `${note('只接受已有的财务输入包。不会请求供应商、猜测单位，或把准备完成当作预测有效。')}<div class="sq-form-grid">${field('fin-name', '输入名称', `<input id="fin-name" data-fin-input="name" maxlength="80" value="${e(state.name)}" placeholder="例如：年度财务研究输入">`)}${field('fin-calendar', '已登记交易日历', `<select id="fin-calendar" data-fin-input="calendarRef"><option value="">明确选择日历证据</option>${state.calendars.map((x) => `<option value="${e(x.calendarRef)}" ${state.calendarRef === x.calendarRef ? 'selected' : ''}>${e(x.label || x.calendarRef)} · ${e(x.coverageStart || '')}–${e(x.coverageEnd || '')}</option>`).join('')}</select>`, '包内日历必须与所选登记版本完全一致；不会自动替换。')}</div>${field('fin-file', '版本 1 财务输入包（JSON，最多 24 MiB）', '<input id="fin-file" type="file" accept="application/json,.json" data-fin-file>', state.file ? `${state.file.name} · ${(state.file.size / 1024).toFixed(1)} KiB` : '上传后可校验字段、单位与披露日期。')}${advanced('已有核验证明引用（可选）', field('fin-proofs', '服务器已授权的证明 ID', `<textarea id="fin-proofs" data-fin-input="proofRefs" rows="2" placeholder="多个 ID 用逗号分隔">${e(state.proofRefs)}</textarea>`, '填写引用不代表取得信任；服务器与 Python 会逐项核对范围和内容。'))}<div class="sq-actions">${btn('upload', state.busy ? '正在上传…' : '上传输入包', { primary: true, disabled: state.busy || !importEnabled })}</div>${!state.calendars.length && !state.loading ? note('当前工作区没有已登记日历。请先由服务维护者登记证据，不能使用自认证日历。', 'warning') : ''}`
      ) +
      panel(
        '我的冻结输入',
        state.loading && !state.loaded
          ? '<p role="status">正在读取输入列表…</p>'
          : !state.loaded
            ? '<p>输入列表尚未读取。</p>'
            : state.items.length
              ? `<div class="fin-source-list">${state.items.map((x) => `<article><div><a href="${path(x.id)}"><strong>${e(x.name)}</strong></a><p>${e(x.source?.provider || '待校验来源')} · ${e(x.unitPolicy === 'allow_declared' ? '含显式单位假设' : x.unitPolicy ? '严格单位证据' : '证据待校验')}</p><small>${e(x.latestJob ? `${statuses[x.latestJob.status] || x.latestJob.status} · ${x.latestJob.kind.replace('financial_', '')}` : '尚无计算任务')}</small></div>${badge(x.status)}<a class="sq-button" href="${path(x.id)}">打开输入</a></article>`).join('')}</div><div class="fin-pagination"><span>共 ${state.total} 项</span>${btn('list-prev', '上一页', { disabled: state.page === 1 || state.loading })}${btn('list-next', '下一页', { disabled: state.page * 25 >= state.total || state.loading })}</div>`
              : empty('尚无财务输入', '上传冻结包后，校验、准备和证据都保留在当前工作区。')
      )
    );
  }
  function sourceSummary(input) {
    return `<dl class="fin-summary"><dt>研究范围</dt><dd>${e(input.selection?.symbols?.join('、') || '待校验')}</dd><dt>输入区间</dt><dd>${e(input.selection ? `${input.selection.start}–${input.selection.end}` : '待校验')}</dd><dt>报表口径</dt><dd>${e(input.selection ? `${selectionLabel(input.selection.scope)} · ${selectionLabel(input.selection.flowBasis)}` : '待校验')}</dd><dt>单位政策</dt><dd>${e(input.unitPolicy === 'allow_declared' ? '使用明确记录的用户单位假设' : input.unitPolicy ? '仅使用已核验单位证据' : '待校验')}</dd><dt>历史证据</dt><dd>原始发布版本与修订时点未核实</dd></dl>`;
  }
  function progress() {
    const job = state.detail?.activeJob || state.detail?.latestJob;
    if (!job) return '';
    return `<div class="fin-progress" role="status"><strong>${e(statuses[job.status] || job.status)}</strong><span>${e({ checking_inputs: '核对冻结输入', preparing_states: '计算财务状态', writing_evidence: '保存依赖证据', queued: '等待计算节点' }[job.phase] || '')}</span>${['queued', 'running', 'cancel_requested'].includes(job.status) ? btn('cancel', '取消任务', { disabled: state.busy || job.status === 'cancel_requested', id: job.id }) : ''}${job.error ? `<p>${e(job.error.code)} · ${e(job.error.message)}</p>` : ''}</div>`;
  }
  function sourcePage(input) {
    return panel(
      '冻结源与验证',
      sourceSummary(input) +
        progress() +
        `<div class="sq-actions">${btn('validate', '校验输入与授权证据', { primary: true, disabled: state.busy || input.status !== 'uploaded' || !state.cap?.operations.validate || !state.cap?.runner.online })}${input.uploadSha256 ? `<a class="sq-button" href="/quant/api/financial/inputs/${encodeURIComponent(input.id)}/source-download?uploadSha256=${input.uploadSha256}" download>下载原始上传包</a>` : ''}${input.packRoot ? `<a class="sq-button" href="/quant/api/financial/inputs/${encodeURIComponent(input.id)}/download?packRoot=${input.packRoot}" download>下载已校验包</a>` : ''}</div>` +
        advanced(
          '输入身份与校验明细',
          `<dl class="fin-summary">${['uploadSha256', 'inputRoot', 'packRoot', 'calendarRoot'].map((k) => `<dt>${k}</dt><dd><code>${e(input[k] || '尚未计算')}</code></dd>`).join('')}</dl>${state.detail.validation ? `<pre>${e(JSON.stringify(state.detail.validation, null, 2))}</pre>` : '<p>尚未通过验证。</p>'}`
        )
    );
  }
  function unitPage(input) {
    const d = state.draft;
    if (!d) return note('先完成输入校验，才能创建单位政策的新版本。');
    const required = [
      ...new Set(d.selection.selectedStateIds.flatMap((x) => definition(x)?.requiredFields || [])),
    ];
    return panel(
      '单位与数据假设',
      note('原数据与声明都不会被覆盖。任何调整生成一个新输入版本；声明始终是不经验证的研究假设。') +
        `<div class="sq-form-grid">${['scope', 'flowBasis'].map((key) => field('fin-selection-' + key, key === 'scope' ? '报表范围' : '流量期间基础', `<select id="fin-selection-${key}" data-fin-selection="${key}">${(state.definitions.supportedSelection?.[key] || []).map((value) => `<option value="${e(value)}" ${d.selection[key] === value ? 'selected' : ''}>${e(selectionLabel(value))}</option>`).join('')}</select>`)).join('')}</div>${note('口径来自冻结报表的报告类型。切换不会转换不相容的报表；依赖不足会返回缺失。')}` +
        field(
          'fin-policy',
          '单位政策',
          `<select id="fin-policy" data-fin-policy><option value="verified_only" ${d.unitPolicy === 'verified_only' ? 'selected' : ''}>只使用已核验证据</option><option value="allow_declared" ${d.unitPolicy === 'allow_declared' ? 'selected' : ''}>允许明确记录的用户单位假设</option></select>`
        ) +
        (d.unitPolicy === 'allow_declared'
          ? `<div class="fin-declarations">${required
              .map((id) => {
                const value = d.declarations[id] || {};
                return `<fieldset><legend>${e(id)}</legend><div class="sq-form-grid">${field('fin-unit-' + id, '本次声明的单位', `<select id="fin-unit-${e(id)}" data-fin-unit="${e(id)}"><option value="">不作声明（缺证据时缺失）</option>${(state.definitions.unitOptions || []).map((x) => `<option value="${e(x.nativeUnit)}" ${value.nativeUnit === x.nativeUnit ? 'selected' : ''}>${e(x.nativeUnit)} · ${e(x.currency)}</option>`).join('')}</select>`)}${field('fin-statement-' + id, '明确依据与解释', `<textarea id="fin-statement-${e(id)}" data-fin-statement="${e(id)}" maxlength="2000" rows="2">${e(value.statement || '')}</textarea>`, '至少 10 字符；不预填元或币种。')}</div>${state.definitions.fields.find((x) => x.id === id)?.positive_outflow ? `<label class="fin-checkbox"><input type="checkbox" data-fin-positive="${e(id)}" ${value.positiveOutflow ? 'checked' : ''}>我声明此支付字段以正数表示流出</label>` : ''}</fieldset>`;
              })
              .join('')}</div>`
          : '<p>未获得匹配单位证据的依赖将返回缺失，不会自动采用默认单位。</p>') +
        `<label class="fin-checkbox"><input type="checkbox" data-fin-confirm ${d.confirmed ? 'checked' : ''}>我理解：新版本的声明清单完整替换原声明；空清单会移除原有用户声明。历史输入保留。</label><div class="sq-actions">${btn('revise', '保存声明与所选状态为新版本', { primary: true, disabled: state.busy || !d.confirmed || !state.cap?.operations.validate || !state.cap?.runner.online })}</div>`
    );
  }
  function statePage() {
    const d = state.draft,
      items = (state.definitions?.items || []).filter((x) =>
        (x.name + ' ' + x.id + ' ' + x.requiredFields.join(' '))
          .toLowerCase()
          .includes(state.query.toLowerCase())
      );
    return panel(
      '选择可计算的状态定义',
      note(
        '16 种定义是公式目录。是否有数值由冻结报告期、单位和披露证据决定；季度或 TTM 历史缺失不会被填补。'
      ) +
        field(
          'fin-query',
          '搜索定义或字段',
          `<input id="fin-query" data-fin-query value="${e(state.query)}" placeholder="例如：现金、TTM、total_assets">`
        ) +
        `<div class="fin-definitions">${items.map((x) => `<article class="${d?.selection.selectedStateIds.includes(x.id) ? 'selected' : ''}"><div><strong>${e(x.name)}</strong><small>${e(x.id)}</small></div>${btn(d?.selection.selectedStateIds.includes(x.id) ? 'state-remove' : 'state-add', d?.selection.selectedStateIds.includes(x.id) ? '移除' : '选择', { id: x.id, disabled: !d })}<p>${e(x.requiredFields.join(' · '))}</p>${advanced('公式与报告期依赖', `<p>${e(x.definition.numerator.map((v) => `${v[0]}(${v[1]}, 季度偏移 ${v[2]})`).join(' + '))} / ${e(`${x.definition.denominator[0]}(${x.definition.denominator[1]}, 季度偏移 ${x.definition.denominator[2]})`)}${x.definition.subtract_one ? ' − 1' : ''}</p>`)}</article>`).join('')}</div>${d ? `<p>当前新版本选择 ${d.selection.selectedStateIds.length} 项。请到“单位与版本”确认并保存。</p>` : note('先完成输入校验。当前仅浏览公式定义。')}`
    );
  }
  function coveragePage(input) {
    const p = state.prepared,
      data = state.coverage;
    return (
      panel(
        '生成状态与实际覆盖',
        progress() +
          `<div class="sq-actions">${btn('prepare', '按当前冻结版本生成状态', { primary: true, disabled: state.busy || !['ready_to_prepare', 'prepared'].includes(input.status) || !state.cap?.operations.prepare || !state.cap?.runner.online })}</div>${note('可查看状态值、可用日期和逐项来源。财务状态接入模型研究尚未开放。')}`
      ) +
      (!p
        ? empty('尚无已完成的准备结果', '任务完成后显示真实覆盖与缺失原因。')
        : panel(
            '状态 × 公司',
            `<div class="sq-form-grid">${field('fin-filter-state', '状态', `<select id="fin-filter-state" data-fin-filter="state"><option value="">全部状态</option>${p.preparation.selection.selectedStateIds.map((id) => `<option value="${e(id)}" ${state.filterState === id ? 'selected' : ''}>${e(label(id))}</option>`).join('')}</select>`)}${field('fin-filter-symbol', '公司', `<select id="fin-filter-symbol" data-fin-filter="symbol"><option value="">全部公司</option>${p.preparation.selection.symbols.map((x) => `<option ${state.filterSymbol === x ? 'selected' : ''}>${e(x)}</option>`).join('')}</select>`)}</div>${
              !data
                ? '<p role="status">正在读取覆盖…</p>'
                : `<div class="fin-table-scroll"><table><thead><tr><th>状态 / 公司</th><th>有效 / 缺失行</th><th>实际观察覆盖</th><th>披露可用日期</th><th>报告期末</th><th>缺失原因</th></tr></thead><tbody>${
                    data.items
                      .map(
                        (x) =>
                          `<tr><td>${e(label(x.stateId))}<small>${e(x.symbol)}</small></td><td>${x.okRows} / ${x.missingRows}</td><td>${e(x.firstObserved || '—')}<small>${e(x.lastObserved || '—')}</small></td><td>${e(x.firstAvailable || '—')}<small>${e(x.lastAvailable || '—')}</small></td><td>${e(x.latestPeriodEnd || '—')}<small>${x.latestAgeCalendarDays == null ? '' : `${x.latestAgeCalendarDays} 自然日距样本末`}</small></td><td>${e(
                            Object.entries(x.reasonCounts || {})
                              .map(([k, v]) => `${k}: ${v}`)
                              .join('；') || '—'
                          )}</td></tr>`
                      )
                      .join('') || '<tr><td colspan="6">此筛选没有记录。</td></tr>'
                  }</tbody></table></div><div class="fin-pagination"><span>共 ${data.total} 项</span>${btn('coverage-prev', '上一页', { disabled: !state.coverageHistory.length })}${btn('coverage-next', '下一页', { disabled: !data.nextCursor })}</div>`
            }`
          ) + eventPanel())
    );
  }
  function eventPanel() {
    const events = state.events,
      event = state.event,
      deps = state.dependencies;
    return (
      panel(
        '来源事件与依赖',
        !events
          ? '<p role="status">正在读取事件…</p>'
          : `<div class="fin-table-scroll"><table><thead><tr><th>状态 / 公司</th><th>计算时点</th><th>状态数值</th><th>报告期 / 可用日</th><th></th></tr></thead><tbody>${events.items.map((x) => `<tr><td>${e(label(x.stateId))}<small>${e(x.symbol)}</small></td><td>${e(x.computedAsOf)}</td><td>${x.status === 'ok' ? e(x.decimalValue) : `缺失：${e((x.reasonCodes || []).join('、'))}`}</td><td>${e(x.periodEnd || '—')}<small>${e(x.availableDate || '—')}</small></td><td>${btn('event', '查看依赖', { id: x.eventId })}</td></tr>`).join('')}</tbody></table></div><div class="fin-pagination"><span>共 ${events.total} 个事件</span>${btn('events-prev', '上一页', { disabled: !state.eventHistory.length })}${btn('events-next', '下一页', { disabled: !events.nextCursor })}</div>`
      ) +
      (event
        ? panel(
            '单个状态的来源证据',
            `<p><strong>${e(label(event.stateId))} · ${e(event.symbol)}</strong></p><p>${event.status === 'ok' ? `Decimal 值 ${e(event.decimalValue)}` : `缺失：${e((event.reasonCodes || []).join('、'))}`} · ${event.dependencyCount} 个依赖</p>${note((event.qualityFlags || []).includes('USER_DECLARED_UNIT_ASSUMPTION') ? '此结果含用户单位假设；不是已验证单位。' : '单位核验不证明原始发布版本、历史修订或未来预测能力。')}${!deps ? '<p role="status">正在读取依赖…</p>' : `<div class="fin-table-scroll"><table><thead><tr><th>字段</th><th>原数值 / 单位</th><th>报告期</th><th>可用日</th><th>证据</th></tr></thead><tbody>${deps.items.map((x) => `<tr><td>${e(x.fieldId)}</td><td>${e(x.rawDecimal ?? '—')}<small>${e(x.rawUnit || '未知')}</small></td><td>${e(x.periodEnd || '—')}</td><td>${e(x.availableDate || '—')}</td><td>${e(x.evidenceLevel || '未核验')}<small>${e(x.referencePreview || '')}</small></td></tr>`).join('')}</tbody></table></div>${deps.nextCursor ? btn('dependencies-next', '下一页依赖') : ''}`}${advanced('完整事件与身份', `<p><code>${e(event.eventId)}</code></p><p><code>${e(event.lineageHash)}</code></p><a class="sq-button" href="/quant/api/financial/preparations/${encodeURIComponent(state.prepared.preparation.id)}/events/${event.eventId}/download?preparedRoot=${state.prepared.preparation.preparedRoot}" download>下载完整私有事件</a>`)}`
          )
        : '')
    );
  }
  function renderSource() {
    const input = state.detail?.input;
    if (!input || input.id !== app.quantEntityId)
      return (
        title('财务输入', '正在读取冻结源与任务状态。') +
        busyError() +
        '<p role="status">正在加载输入…</p>'
      );
    const active = stage(),
      steps = [
        ['source', '输入与验证'],
        ['units', '单位与版本'],
        ['states', '状态定义'],
        ['coverage', '准备与证据'],
      ];
    return (
      title(input.name, '输入与每个准备版本保持不可变，证据可以独立下载核对。') +
      `<div class="fin-source-top"><a href="${path()}">← 全部财务输入</a>${badge(input.status)}</div>` +
      busyError() +
      `<nav class="fin-step-tabs" aria-label="财务输入步骤">${steps.map(([id, label], n) => `<a href="${path(input.id, id)}" ${active === id ? 'aria-current="step"' : ''}>${n + 1}. ${label}</a>`).join('')}</nav>` +
      (
        {
          source: () => sourcePage(input),
          units: () => unitPage(input),
          states: statePage,
          coverage: () => coveragePage(input),
        }[active] || (() => sourcePage(input))
      )() +
      `<div class="fin-step-footer">${steps.findIndex((x) => x[0] === active) > 0 ? `<a class="sq-button" href="${path(input.id, steps[steps.findIndex((x) => x[0] === active) - 1][0])}">上一步</a>` : ''}${
        steps.findIndex((x) => x[0] === active) < 3
          ? `<a class="sq-button primary" href="${path(
              input.id,
              steps[
                Math.max(
                  0,
                  steps.findIndex((x) => x[0] === active)
                ) + 1
              ][0]
            )}">下一步</a>`
          : ''
      }</div>`
    );
  }
  function view() {
    if(acquisition.active()) return acquisition.render();
    return `<div class="fin-workspace">${state.cap?.enabled && !state.cap.runner.online ? note('财务计算节点暂未在线。已上传输入和草稿保留。', 'warning') + btn('retry', '重新检查服务') : ''}${app.quantEntityId ? renderSource() : renderList()}</div>`;
  }

  // Reads are scoped to the route/version that initiated them. Polling never
  // replaces a user's unit draft, selected file, search text or local choices.
  let bootPromise = null;
  const reads = { coverage: 0, events: 0, event: 0, dependencies: 0 };
  const present = () => {
    if (current()) render();
  };
  const reportError = (error) => {
    state.error = error.message || String(error);
    present();
  };
  const post = (url, data) => api(url, { method: 'POST', body: JSON.stringify(data) });
  async function boot() {
    if (bootPromise) return bootPromise;
    bootPromise = (async () => {
      const responses = await Promise.allSettled([
        api('/financial/capabilities'),
        api('/financial/definitions'),
        api('/financial/calendars?page=1&pageSize=100'),
      ]);
      const keys = ['cap', 'definitions', 'calendars'];
      const errors = [];
      responses.forEach((r, n) => {
        if (r.status === 'fulfilled') state[keys[n]] = n === 2 ? r.value.items : r.value;
        else errors.push(r.reason.message);
      });
      if (errors.length) throw Error(errors.join('；'));
    })();
    try {
      await bootPromise;
    } catch (error) {
      bootPromise = null;
      throw error;
    }
  }
  function resetEvidence() {
    state.prepared = state.coverage = state.events = state.event = state.dependencies = null;
    state.coverageCursor = state.eventCursor = null;
    state.coverageHistory = [];
    state.eventHistory = [];
    state.filterState = state.filterSymbol = '';
    for (const key in reads) reads[key]++;
  }
  function cancelTimer() {
    clearTimeout(timer);
    timer = null;
  }
  function schedulePoll() {
    cancelTimer();
    if (!current() || !state.detail?.activeJob) return;
    const inputId = state.detail.input.id;
    timer = setTimeout(async () => {
      if (!current() || app.quantEntityId !== inputId) return;
      try {
        // Job reads run the server's lease/deadline reconciliation.
        await api('/financial/jobs/' + state.detail.activeJob.id);
        await loadSource(inputId, { quiet: true });
      } catch (error) {
        reportError(error);
      }
    }, 2500);
  }
  async function loadList() {
    const request = ++state.request,
      page = state.page;
    state.loading = true;
    state.error = '';
    present();
    try {
      const data = await api(`/financial/inputs?page=${page}&pageSize=25`);
      if (request !== state.request || !current() || app.quantEntityId) return;
      state.items = data.items;
      state.total = data.total;
      state.loaded = true;
    } catch (error) {
      if (request === state.request) reportError(error);
    } finally {
      if (request === state.request) {
        state.loading = false;
        present();
      }
    }
  }
  async function loadSource(inputId, { quiet = false } = {}) {
    const request = ++state.request;
    if (!quiet) {
      state.loading = true;
      state.error = '';
      present();
    }
    try {
      const data = await api('/financial/inputs/' + encodeURIComponent(inputId));
      if (request !== state.request || !current() || app.quantEntityId !== inputId) return;
      state.detail = data;
      state.sourceId = inputId;
      if (data.input.selection && state.draftId !== inputId) {
        state.draft = createDraft(data.input);
        state.draftId = inputId;
      }
      const latest = data.latestPreparation;
      if (latest && state.prepared?.preparation.id !== latest.id) {
        const prepared = await api('/financial/preparations/' + encodeURIComponent(latest.id));
        if (request !== state.request || !current() || app.quantEntityId !== inputId) return;
        resetEvidence();
        state.prepared = prepared;
        await Promise.allSettled([loadEvidence('coverage'), loadEvidence('events')]);
      }
    } catch (error) {
      if (request === state.request) reportError(error);
    } finally {
      if (request === state.request) {
        state.loading = false;
        present();
        schedulePoll();
      }
    }
  }
  function evidenceUrl(collection, cursor = null) {
    const p = state.prepared.preparation;
    const params = new URLSearchParams({
      preparedRoot: p.preparedRoot,
      limit: 25,
    });
    if (cursor) params.set('cursor', cursor);
    if (state.filterState) params.set('stateId', state.filterState);
    if (state.filterSymbol) params.set('symbol', state.filterSymbol);
    return `/financial/preparations/${p.id}/${collection}?${params}`;
  }
  async function loadEvidence(collection) {
    if (!state.prepared) return;
    const request = ++reads[collection],
      root = state.prepared.preparation.preparedRoot;
    const url = evidenceUrl(
      collection,
      state[collection === 'events' ? 'eventCursor' : 'coverageCursor']
    );
    state[collection] = null;
    present();
    try {
      const data = await api(url);
      if (request === reads[collection] && state.prepared?.preparation.preparedRoot === root) {
        state[collection] = data;
        present();
      }
    } catch (error) {
      if (request === reads[collection]) reportError(error);
    }
  }
  async function loadEvent(eventId) {
    const request = ++reads.event,
      root = state.prepared.preparation.preparedRoot;
    state.event = state.dependencies = null;
    reads.dependencies++;
    present();
    try {
      const data = await api(
        `/financial/preparations/${state.prepared.preparation.id}/events/${eventId}?preparedRoot=${root}`
      );
      if (request !== reads.event || state.prepared?.preparation.preparedRoot !== root) return;
      state.event = data.event;
      present();
      await loadDependencies();
    } catch (error) {
      if (request === reads.event) reportError(error);
    }
  }
  async function loadDependencies(cursor = null) {
    const request = ++reads.dependencies,
      eventId = state.event.eventId,
      p = state.prepared.preparation;
    state.dependencies = null;
    present();
    try {
      const params = new URLSearchParams({
        preparedRoot: p.preparedRoot,
        limit: 25,
      });
      if (cursor) params.set('cursor', cursor);
      const data = await api(
        `/financial/preparations/${p.id}/events/${eventId}/dependencies?${params}`
      );
      if (request === reads.dependencies && state.event?.eventId === eventId) {
        state.dependencies = data;
        present();
      }
    } catch (error) {
      if (request === reads.dependencies) reportError(error);
    }
  }
  async function routeChanged() {
    cancelTimer();
    if (acquisition.active()) { await acquisition.routeChanged(); return; }
    acquisition.dispose();
    if (!current()) {
      state.request++;
      return;
    }
    const route = location.hash;
    if (lastRoute !== route) {
      lastRoute = route;
      setTimeout(
        () => document.getElementById('financial-title')?.focus({ preventScroll: true }),
        0
      );
    }
    const requestedId = app.quantEntityId;
    if (requestedId !== state.sourceId) {
      state.detail = null;
      state.sourceId = requestedId || null;
      state.draft = state.draftId = null;
      resetEvidence();
    }
    state.loading = true;
    present();
    try {
      await boot();
      if (!current() || app.quantEntityId !== requestedId) return;
      if (requestedId) await loadSource(requestedId);
      else await loadList();
    } catch (error) {
      state.loading = false;
      reportError(error);
    }
  }
  async function upload() {
    const file = state.file;
    if (!file) throw Error('请选择冻结输入包文件。');
    if (file.size < 1 || file.size > (state.cap?.limits.packageBytes || 24 * 1024 * 1024))
      throw Error('文件必须为 1 字节至 24 MiB。');
    if (!state.name.trim() || !state.calendarRef) throw Error('请输入名称并明确选择已登记日历。');
    const proofRefs = [...new Set(state.proofRefs.split(/[\s,，]+/).filter(Boolean))];
    if (proofRefs.length > 256) throw Error('证明引用最多 256 项。');
    const descriptor = {
      name: state.name.trim(),
      calendarRef: state.calendarRef,
      proofRefs,
      byteLength: file.size,
    };
    const fingerprint = JSON.stringify(descriptor);
    // Preserve the same request ID after an unknown network outcome. The file
    // identity must also match; a changed selection is a new immutable upload.
    if (state.uploadReceipt?.file !== file || state.uploadReceipt?.fingerprint !== fingerprint)
      state.uploadReceipt = {
        file,
        fingerprint,
        requestId: crypto.randomUUID(),
        receipt: null,
      };
    const pending = state.uploadReceipt;
    pending.receipt ||= await post('/financial/inputs', {
      ...descriptor,
      requestId: pending.requestId,
    });
    await api(`/financial/inputs/${pending.receipt.input.id}/content`, {
      method: 'PUT',
      body: file,
      timeoutMs: 120000,
    });
    state.loaded = false;
    if (current() && !app.quantEntityId) location.hash = path(pending.receipt.input.id);
    toast('冻结输入已上传，尚未校验。');
  }
  function revision() {
    const d = state.draft,
      input = state.detail.input;
    if (!d?.confirmed) throw Error('请确认新版本将完整替换用户声明。');
    if (!d.selection.selectedStateIds.length) throw Error('至少选择一个财务状态。');
    const allowed = new Set(
      d.selection.selectedStateIds.flatMap((id) => definition(id).requiredFields)
    );
    const declarations = [];
    if (d.unitPolicy === 'allow_declared')
      for (const [fieldId, value] of Object.entries(d.declarations)) {
        if (!allowed.has(fieldId) || !value.nativeUnit) continue;
        const unit = state.definitions.unitOptions.find((x) => x.nativeUnit === value.nativeUnit);
        if (!unit) throw Error('请选择支持的明确单位。');
        if ((value.statement || '').trim().length < 10)
          throw Error(`${fieldId} 的声明依据至少 10 字符。`);
        declarations.push({
          fieldId,
          inputRoot: input.inputRoot,
          nativeUnit: unit.nativeUnit,
          currency: unit.currency,
          positiveOutflow: state.definitions.fields.find((x) => x.id === fieldId)?.positive_outflow
            ? !!value.positiveOutflow
            : null,
          statement: value.statement.trim(),
        });
      }
    return {
      expectedPackRoot: input.packRoot,
      selection: structuredClone(d.selection),
      unitPolicy: d.unitPolicy,
      declarations,
    };
  }
  async function operation(action) {
    const input = state.detail.input;
    const value =
      action === 'validate'
        ? { expectedUploadSha256: input.uploadSha256 }
        : action === 'prepare'
          ? { expectedPackRoot: input.packRoot }
          : revision();
    const fingerprint = JSON.stringify({ inputId: input.id, action, value });
    if (state.pendingOperation?.fingerprint !== fingerprint)
      state.pendingOperation = { fingerprint, requestId: crypto.randomUUID() };
    const data = await post(
      `/financial/inputs/${input.id}/${action === 'revise' ? 'revisions' : action}`,
      { requestId: state.pendingOperation.requestId, ...value }
    );
    state.pendingOperation = null;
    if (!current() || app.quantEntityId !== input.id) {
      toast('财务任务已提交；可在输入列表查看。');
      return;
    }
    if (data.input.id !== input.id) location.hash = path(data.input.id, 'source');
    else await loadSource(input.id);
  }
  async function act(action, element) {
    if (action === 'state-add' || action === 'state-remove') {
      const ids = state.draft.selection.selectedStateIds,
        id = element.dataset.id;
      if (!definition(id)) return;
      state.draft.selection.selectedStateIds =
        action === 'state-add' ? [...new Set([...ids, id])] : ids.filter((x) => x !== id);
      state.draft.confirmed = false;
      present();
      return;
    }
    if (action === 'list-prev' || action === 'list-next') {
      state.page += action === 'list-next' ? 1 : -1;
      await loadList();
      return;
    }
    for (const collection of ['coverage', 'events'])
      if (action === collection + '-prev' || action === collection + '-next') {
        const prefix = collection === 'events' ? 'event' : 'coverage',
          history = state[prefix + 'History'];
        if (action.endsWith('-next')) {
          history.push(state[prefix + 'Cursor']);
          state[prefix + 'Cursor'] = state[collection].nextCursor;
        } else state[prefix + 'Cursor'] = history.pop() || null;
        await loadEvidence(collection);
        return;
      }
    if (action === 'event') {
      await loadEvent(element.dataset.id);
      return;
    }
    if (action === 'dependencies-next') {
      await loadDependencies(state.dependencies.nextCursor);
      return;
    }
    if (action === 'retry') {
      state.error = '';
      bootPromise = null;
      await routeChanged();
      return;
    }
    if (state.busy) return;
    state.busy = true;
    state.error = '';
    present();
    try {
      if (action === 'upload') await upload();
      else if (['validate', 'prepare', 'revise'].includes(action)) await operation(action);
      else if (action === 'cancel') {
        const inputId = app.quantEntityId;
        await post('/financial/jobs/' + element.dataset.id + '/cancel', {});
        if (current() && app.quantEntityId === inputId) await loadSource(inputId);
      }
    } finally {
      state.busy = false;
      present();
    }
  }
  function input(event) {
    if (!current()) return;
    const el = event.target,
      d = state.draft;
    if (el.dataset.finInput) state[el.dataset.finInput] = el.value;
    if (el.hasAttribute('data-fin-query')) {
      state.query = el.value;
      present();
    }
    if (el.hasAttribute('data-fin-file') && event.type === 'change') {
      state.file = el.files?.[0] || null;
      present();
    }
    if (!d) return;
    if (el.dataset.finSelection) {
      d.selection[el.dataset.finSelection] = el.value;
      d.confirmed = false;
      present();
    }
    if (el.hasAttribute('data-fin-policy')) {
      d.unitPolicy = el.value;
      d.confirmed = false;
      present();
    }
    if (el.hasAttribute('data-fin-confirm')) {
      d.confirmed = el.checked;
      present();
    }
    const fieldId = el.dataset.finUnit || el.dataset.finStatement || el.dataset.finPositive;
    if (fieldId) {
      const value = (d.declarations[fieldId] ||= {});
      if (el.dataset.finUnit) value.nativeUnit = el.value;
      if (el.dataset.finStatement) value.statement = el.value;
      if (el.dataset.finPositive) value.positiveOutflow = el.checked;
      d.confirmed = false;
    }
    if (el.dataset.finFilter) {
      state[el.dataset.finFilter === 'state' ? 'filterState' : 'filterSymbol'] = el.value;
      state.coverageCursor = state.eventCursor = null;
      state.coverageHistory = [];
      state.eventHistory = [];
      state.event = state.dependencies = null;
      reads.event++;
      reads.dependencies++;
      Promise.allSettled([loadEvidence('coverage'), loadEvidence('events')]);
    }
  }
  document.addEventListener('click', (event) => {
    const el = event.target.closest('[data-fin]');
    if (!el || !current() || el.disabled) return;
    event.preventDefault();
    act(el.dataset.fin, el).catch(reportError);
  });
  document.addEventListener('input', input);
  document.addEventListener('change', (event) => {
    if (!['INPUT', 'TEXTAREA'].includes(event.target.tagName) || event.target.type === 'file')
      input(event);
  });
  return {
    render: view,
    routeChanged,
    state,
    loadSource,
    loadEvidence,
    acquisition,
    dispose: ()=>{cancelTimer();acquisition.dispose();},
  };
}
