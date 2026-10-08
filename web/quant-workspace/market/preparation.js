import { scopeKey, marketAdmission, matchesMarketScope } from '../research-data-binding.js';
import { marketDatasetDownload } from '../source-downloads.js';

// The only provider-triggering action here is an explicit, idempotent start click.
export function createMarketPreparation(C, F, { freezeScope, onBind }) {
  const { state: app, api, esc: e, render, persistDraft, toast } = C;
  const { panel, note, advanced, button } = F;
  const STORAGE = 'atlas-quant-market-preparation-v1', DATA_PROFILE = 'pooled_asset_1000_v1';
  const extraFields = 'turnover_rate turnover_rate_f volume_ratio pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm total_share float_share free_share total_mv circ_mv'.split(' ');
  const names = { queued: '等待独立数据准备', running: '正在准备完整行情', cancel_requested: '正在取消', cancelled: '已取消', failed: '准备未完成', completed: '完整行情已冻结' };
  const phases = { checking_plan: '核对冻结请求计划', fetching_sources: '读取声明的供应商数据', normalizing: '核对完整成员与行情', writing_evidence: '保存来源与数据', ready: '准备完成' };
  const blank = () => ({ plan: null, selectionKey: '', job: null, startRequestId: null, startUnknown: false, extra: [], verified: false, admissions: null, busy: '', error: '', requests: null, page: 1 });
  const s = blank();
  let timer = null, readSequence = 0, owner = null;
  const workspaceId = () => typeof app.session?.workspace?.id === 'string' && app.session.workspace.id ? app.session.workspace.id : null;
  function syncOwner() {
    const next = workspaceId();
    if (next === owner) return owner;
    owner = next; readSequence++; clearTimeout(timer); Object.assign(s, blank());
    if (owner) {
      try {
        const saved = JSON.parse(localStorage.getItem(STORAGE + ':' + encodeURIComponent(owner)) || 'null');
        if (saved?.workspaceId === owner) Object.assign(s, { plan: saved.plan, selectionKey: saved.selectionKey, job: saved.job, startRequestId: saved.startRequestId, startUnknown: saved.startUnknown, extra: saved.extra || [] });
      } catch {}
    }
    return owner;
  }
  async function ownerApi(expectedOwner, path, options) {
    const response = await api(path, options);
    if (syncOwner() !== expectedOwner) throw Error('工作区身份已变化；此前响应未写入当前研究。');
    return response;
  }
  const current = () => app.view === 'quant' && app.quantStep === 'settings';
  const present = () => { if (current()) render(); };
  const save = () => { if (!owner || workspaceId() !== owner) return; try { localStorage.setItem(STORAGE + ':' + encodeURIComponent(owner), JSON.stringify({ workspaceId: owner, plan: s.plan, selectionKey: s.selectionKey, job: s.job, startRequestId: s.startRequestId, startUnknown: s.startUnknown, extra: s.extra })); } catch {} };
  const fields = () => [...new Set([...s.extra, ...extraFields.filter(name => app.strategy.factors.some(f => new RegExp('\\b' + name + '\\b').test(f.expression)))])].sort();
  const selectionKey = () => JSON.stringify([scopeKey(app.strategy.universe), fields()]);
  const same = () => s.selectionKey === selectionKey();
  const admission = () => s.verified && s.admissions?.find(x => x.admissionProfile === marketAdmission(app.strategy) && x.families?.includes(app.strategy.model.family) && x.estimator === app.strategy.model.estimator && x.targetKind === app.strategy.target.kind && x.executionEnabled === false);
  const running = () => ['queued', 'running', 'cancel_requested'].includes(s.job?.status);
  const hash = x => /^[a-f0-9]{64}$/.test(x || '');
  const uuid = x => /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(x || '');
  const sameRef = (a, b) => a?.scopeId === b?.scopeId && a?.scopeRoot === b?.scopeRoot && a?.format === b?.format && a?.version === b?.version;
  function validatePlan(p, expectedUniverse, expectedRef) {
    if (!uuid(p.planRef?.planId) || !hash(p.planRef?.planRoot) || p.profile !== DATA_PROFILE || p.planRef.format !== 'atlas.quant.market_acquisition_plan' || p.planRef.version !== 1) throw Error('市场准备计划身份无效。');
    if (!sameRef(p.universeScopeRef, expectedRef) || p.scope.start !== expectedUniverse.start || p.scope.end !== expectedUniverse.end || p.scope.symbolCount !== expectedUniverse.symbols.length || JSON.stringify(p.scope.symbols) !== JSON.stringify(expectedUniverse.symbols)) throw Error('准备计划与完整冻结范围不一致；不会启动。');
  }
  function completion(job) {
    const result = job?.result, ref = result?.marketDatasetRef;
    if (job?.status !== 'completed' || !ref || ref.format !== 'atlas.quant.market_dataset' || ref.version !== 1 || !uuid(ref.datasetId) || !hash(ref.datasetRoot) || !sameRef(result.universeScopeRef, s.plan?.universeScopeRef) || result.profile !== DATA_PROFILE || result.symbolCount !== s.plan.scope.symbolCount || !Number.isSafeInteger(result.rowCount) || result.rowCount < 1) throw Error('准备未完成，或完整数据回执与冻结范围不一致；不能绑定研究。');
    return result;
  }
  function planView() {
    const p = s.plan;
    if (!p) return note('先核对完整股票筛选与研究日期，生成可检查的请求计划。此步不请求供应商。');
    const blocked = p.blockedReasons || [];
    return `<dl class="fin-summary"><dt>完整范围</dt><dd>${e(p.scope.symbolCount)} 只 · ${e(C.dateText(p.scope.start))} — ${e(C.dateText(p.scope.end))}</dd><dt>预计供应商请求</dt><dd>${e(p.budget.declaredRequests)} 次 · 每次最多实际请求 1 次</dd><dt>数据字段</dt><dd>${e(p.fields.join('、'))}</dd><dt>原始响应上界</dt><dd>${C.fmt(p.budget.rawResponseCeilingBytes / 1048576, 1)} MiB</dd></dl>${!same() ? note('筛选、日期或所需字段已变化。此计划只对应之前的范围；需重新核对当前范围。', 'warning') : ''}${!s.verified ? note('当前是本浏览器保存的准备记录，请读取服务器最新状态后继续。') : ''}${blocked.map(x => note(x.message, 'warning')).join('')}${!p.canStart && !blocked.length ? note('独立市场数据准备尚未开放；已有计划保留，不能开始供应商请求。') : ''}<div class="sq-actions">${button('market-requests', '逐项查看请求计划', { disabled: !!s.busy, small: true })}${button('market-refresh', '读取最新准备状态', { disabled: !!s.busy, small: true })}</div>${advanced('数据计划身份', `<code>${e(p.planRef.planRoot)}</code><p>数据准备口径：${e(p.profile)}。计算协议另行绑定，不要求重新取数。</p>`)}`;
  }
  function requestsView() {
    const page = s.requests;
    if (!page) return '';
    return advanced('本次声明的供应商请求', `<div class="sq-table-scroll"><table class="sq-table"><thead><tr><th>序号</th><th>接口</th><th>成员 / 市场</th><th>开始 / 结束</th></tr></thead><tbody>${page.items.map(x => `<tr><td>${e(x.ordinal + 1)}</td><td>${e(x.apiName)}</td><td>${e(x.params.ts_code || x.params.exchange)}</td><td>${e(x.params.start_date)} — ${e(x.params.end_date)}</td></tr>`).join('')}</tbody></table></div><div class="sq-actions"><span>共 ${e(page.total)} 次请求</span>${button('market-requests', '上一页', { page: s.page - 1, disabled: !!s.busy || s.page <= 1 })}${button('market-requests', '下一页', { page: s.page + 1, disabled: !!s.busy || s.page * 50 >= page.total })}</div>`, true);
  }
  function view() {
    syncOwner();
    if (!owner) return panel('完整筛选池 · 独立市场数据准备', note('正在确认私有工作区身份；确认前不会恢复或提交市场准备任务。'));
    const b = app.marketDatasetBinding?.workspaceId === owner ? app.marketDatasetBinding : null, active = app.dataSource === 'ready_market';
    const boundDownload = active && b ? marketDatasetDownload(b.marketDatasetRef) : null;
    let completedDownload = null;
    if (s.verified && s.job?.status === 'completed') {
      try { completedDownload = marketDatasetDownload(completion(s.job).marketDatasetRef); } catch {}
    }
    const download = url => url ? `<a class="sq-button small" href="${e(url)}" download>下载完整行情来源包</a><p class="sq-subtle">包含冻结输入与来源证据；模型预测结果保存在独立报告包中。</p>` : '';
    const profile = marketAdmission(app.strategy), declared = admission(), blockedByJob = running() || s.startUnknown;
    return panel('完整筛选池 · 先准备行情，再研究', `${note('数据准备与模型拟合分开进行。仅“明确开始一次数据准备”会启动已声明的供应商请求；运行 F 只读取完成的冻结数据。')}${!active ? button('market-select', '使用完整池的独立数据准备', { small: true }) : ''}${active && b ? `<dl class="fin-summary"><dt>已绑定行情</dt><dd>${e(b.scope.symbolCount ?? b.scope.symbols.length)} 只 · ${e(C.dateText(b.scope.start))} — ${e(C.dateText(b.scope.end))}</dd><dt>计算协议</dt><dd>${b.admissionProfile === 'pooled_asset_1000_auto_candidate_v1' ? '状态均值回归 · 自动选择' : 'Studio 声明的拟合协议'}</dd></dl>${download(boundDownload)}${matchesMarketScope(b, app.strategy.universe) ? note('当前完整范围与冻结行情一致。模型运行不会重新请求供应商。') : note('当前范围已改变，原行情绑定失配。需重新核对并准备完整范围。', 'warning')}` : ''}${s.error ? note(s.error, 'error') : ''}${advanced('补充数据字段', '<p>行情与复权字段自动纳入；已选因子引用的已注册估值字段自动纳入。附加字段会增加明确列出的请求。</p><div class="ds-members">' + extraFields.map(name => `<label class="fin-checkbox"><input type="checkbox" data-market-field="${name}" ${s.extra.includes(name) ? 'checked' : ''} ${s.busy ? 'disabled' : ''}>${e(name)}</label>`).join('') + '</div>')}${planView()}${requestsView()}<div class="sq-actions">${button('market-plan', s.busy === 'plan' ? '正在核对…' : '核对完整范围与请求预算', { disabled: !!s.busy || blockedByJob, primary: !s.plan })}${button('market-start', s.startUnknown ? '读取原启动请求结果' : '明确开始一次数据准备', { disabled: !!s.busy || !s.plan || (!s.startUnknown && (!s.verified || !same() || !s.plan.canStart || !!s.job)), primary: true })}</div>${s.startUnknown ? note('上次启动响应未知，保留同一启动标识读取原结果，不创建第二次准备。', 'warning') : ''}${s.job ? `<div class="fin-progress" role="status"><strong>${e(names[s.job.status] || '状态待确认')}</strong><span>${e(phases[s.job.phase] || '')}</span></div>${s.job.error ? note(s.job.error.message, 'error') : ''}${button('market-refresh', '刷新任务', { small: true, disabled: !!s.busy })}${running() ? button('market-cancel', '取消剩余准备', { small: true, disabled: !!s.busy || s.job.status === 'cancel_requested' }) : ''}${s.job.status === 'completed' ? `${completedDownload !== boundDownload ? download(completedDownload) : ''}<p>${e(s.job.result?.symbolCount)} 个完整成员 · ${e(s.job.result?.rowCount)} 行。覆盖完成不意味着模型有效。</p>${button('market-bind', '绑定完整行情用于本次研究', { primary: true, disabled: !!s.busy || !same() || !declared?.available })}` : ''}` : ''}${profile && s.verified && !declared?.available ? note(({ MARKET_RESEARCH_DISABLED: '服务器尚未开放完整池模型计算。', RUNNER_OFFLINE: '完整池计算节点当前离线。', RUNNER_UPGRADE_REQUIRED: '计算节点尚未声明支持此模型协议。' })[declared?.reason] || '服务器尚未声明此机制有可用计算协议。', 'warning') : ''}${!profile ? note('当前完整池计算尚未接通此机制与估计器组合。保留当前选择，不自动更换模型。', 'warning') : advanced('完整池计算预算与协议', `<p>最多 16 因子、重拟合至少 20 交易日、2×2 时间验证。实际运行资格由服务器与计算节点核验。</p><code>${e(profile)}</code>`)}<p class="sq-subtle">本页保留此浏览器最近一次准备。跨浏览器来源列表尚未接通；保存后的研究保留其冻结数据引用。</p>`);
  }
  async function refresh() {
    const expectedOwner = syncOwner();
    if (!expectedOwner || !s.plan) return;
    const token = ++readSequence, planId = s.plan.planRef.planId, expected = s.plan;
    const p = await ownerApi(expectedOwner, '/market-preparation-plans/' + encodeURIComponent(planId));
    validatePlan(p, expected.scope, expected.universeScopeRef);
    if (p.planRef.planRoot !== expected.planRef.planRoot) throw Error('服务器返回了不同的计划版本。');
    let job = s.job, admissions = p.researchAdmissions;
    if (job) {
      const result = await ownerApi(expectedOwner, '/market-preparation-jobs/' + encodeURIComponent(job.id));
      if (result.job?.id !== job.id || result.job?.planId !== planId) throw Error('任务不属于当前准备计划。');
      job = result.job; admissions = result.researchAdmissions;
    }
    if (token !== readSequence || s.plan?.planRef.planId !== planId) return;
    s.plan = p; s.job = job; s.admissions = Array.isArray(admissions) ? admissions : null; s.verified = true; save(); present();
  }
  function schedule() {
    clearTimeout(timer);
    const expectedOwner = owner;
    if (current() && running()) timer = setTimeout(async () => { try { if (!s.busy) await refresh(); } catch (error) { if (syncOwner() === expectedOwner) { s.error = error.message; present(); } } if (syncOwner() === expectedOwner) schedule(); }, 15000);
  }
  async function handle(el) {
    const action = el.dataset.sq;
    if (!action?.startsWith('market-')) return false;
    const expectedOwner = syncOwner();
    if (!expectedOwner) { toast('等待私有工作区身份确认后再操作。', true); return true; }
    if (s.busy) return true;
    if (action === 'market-select') { app.dataSource = 'ready_market'; persistDraft(); present(); return true; }
    s.busy = action.slice(7); s.error = ''; present();
    try {
      if (action === 'market-plan') {
        if (running() || s.startUnknown) throw Error('先读取或结束现有准备任务，不能跳过结果未知的启动。');
        const universe = structuredClone(app.strategy.universe), selected = fields(), key = selectionKey();
        if (!universe.selection || universe.subsetPolicy !== 'all' || !universe.symbols.length) throw Error('请先完成股票过滤并解析完整筛选集合。');
        const scopeRef = await freezeScope(universe, 'ready_market');
        if (syncOwner() !== expectedOwner) throw Error('工作区身份已变化，未创建市场计划。');
        const p = await ownerApi(expectedOwner, '/market-preparation-plans', { method: 'POST', body: JSON.stringify({ scopeRef, profile: DATA_PROFILE, requiredFields: selected }) });
        validatePlan(p, universe, scopeRef);
        s.plan = p; s.selectionKey = key; s.job = null; s.startRequestId = null; s.startUnknown = false; s.requests = null; s.admissions = Array.isArray(p.researchAdmissions) ? p.researchAdmissions : null; s.verified = true; save();
      } else if (action === 'market-refresh') await refresh();
      else if (action === 'market-requests') {
        if (!s.plan) throw Error('先生成完整准备计划。');
        const page = Math.max(1, Number(el.dataset.page) || 1), id = s.plan.planRef.planId, root = s.plan.planRef.planRoot;
        const result = await ownerApi(expectedOwner, `/market-preparation-plans/${encodeURIComponent(id)}/requests?page=${page}&pageSize=50`);
        if (result.planRoot !== root) throw Error('请求列表不属于当前冻结计划。');
        s.requests = result; s.page = page;
      } else if (action === 'market-start') {
        if (!s.plan || (!s.startUnknown && (!same() || !s.verified || !s.plan.canStart || s.job))) throw Error('当前计划不可启动，请核对范围和服务器状态。');
        s.startRequestId ||= crypto.randomUUID(); s.startUnknown = true; save();
        const response = await ownerApi(expectedOwner, `/market-preparation-plans/${encodeURIComponent(s.plan.planRef.planId)}/start`, { method: 'POST', body: JSON.stringify({ requestId: s.startRequestId, planRoot: s.plan.planRef.planRoot }) });
        if (!uuid(response.job?.id) || response.job.planId !== s.plan.planRef.planId) throw Error('启动未返回可核对的任务身份，仍保留原启动标识。');
        s.job = response.job; s.startUnknown = false; save();
      } else if (action === 'market-cancel') {
        if (!running()) throw Error('任务已不在准备中。');
        const response = await ownerApi(expectedOwner, `/market-preparation-jobs/${encodeURIComponent(s.job.id)}/cancel`, { method: 'POST', body: '{}' });
        if (response.job?.id !== s.job.id || response.job?.planId !== s.plan.planRef.planId) throw Error('取消响应身份不匹配，请读取任务状态。');
        s.job = response.job; save();
      } else if (action === 'market-bind') {
        const draft = app.strategy, key = selectionKey(), source = app.dataSource, protocol = JSON.stringify([app.strategy.model, app.strategy.target]);
        await refresh();
        const result = completion(s.job), profile = marketAdmission(app.strategy);
        if (syncOwner() !== expectedOwner || draft !== app.strategy || source !== app.dataSource || protocol !== JSON.stringify([app.strategy.model, app.strategy.target]) || key !== selectionKey() || !same()) throw Error('研究范围在核对时变化，数据尚未绑定；当前草稿保留。');
        if (!profile || !admission()?.available) throw Error('服务器未声明当前机制的完整池计算协议可用，不会自动改用其他模型。');
        await onBind({ workspaceId: expectedOwner, marketDatasetRef: structuredClone(result.marketDatasetRef), universeScopeRef: structuredClone(result.universeScopeRef), admissionProfile: profile, scope: structuredClone(s.plan.scope), selectionKey: scopeKey(app.strategy.universe), rowCount: result.rowCount });
        toast('完整行情已绑定；尚未开始 F 模型研究。');
      }
    } catch (error) { if (syncOwner() === expectedOwner) s.error = error.message; }
    finally { if (syncOwner() === expectedOwner) { s.busy = ''; save(); present(); schedule(); } }
    return true;
  }
  function onChange(el) {
    if (!syncOwner()) return false;
    if (!el.dataset.marketField || !extraFields.includes(el.dataset.marketField)) return false;
    s.extra = el.checked ? [...new Set([...s.extra, el.dataset.marketField])] : s.extra.filter(x => x !== el.dataset.marketField);
    save(); present(); return true;
  }
  function routeChanged() { const expectedOwner = syncOwner(); clearTimeout(timer); if (current() && s.plan) { refresh().catch(error => { if (syncOwner() === expectedOwner) { s.error = error.message; present(); } }).finally(() => { if (syncOwner() === expectedOwner) schedule(); }); } }
  return { view, handle, onChange, routeChanged, state: s };
}
