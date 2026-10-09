import { financialProfile, datasetLocation } from './datasets/protocol.js';
import { financialAdmission, financialBindingErrors } from './financial/research-binding.js';
import { SOURCE_LABELS, scopeKey, activeBinding, bindingFields, restoreBindings, marketBindingErrors } from './research-data-binding.js';
import { createMarketPreparation } from './market/preparation.js';
import { mechanismAdmission } from './mechanism-admission.js';
import { createModuleHub } from './module-hub.js';
import { firstIncompleteStep, stepErrors, isPairTarget, pairTarget, easyTargetScopeMismatch, canUseIndividualTarget, easyTargetScopeMessage } from './research-steps.js';
import { createDatasetWorkspace } from './datasets/workspace.js';
// Statistical research routes and private-workspace orchestration; numerical work stays in the engine.
import {
  defaultStrategy,
  normalizeStrategy,
  validateStrategy,
  isStatistical,
  STEPS,
  FAMILIES,
  ESTIMATORS,
  describeFactor,
} from './defaults.js';
import { createForms, setPath } from './forms.js';
import { createModuleCatalog } from './catalog.js';
import { createForecastReports } from './reports.js';
import { createIndustryBrowser } from './industry-browser.js';
import { createFinancialWorkspace } from './financial/workspace.js';

window.AtlasQuantV4 = {
  describeFactor,
  defaultStrategy,
  normalizeStrategy,
  validateStrategy,
  isStatistical,
  create(C) {
    const {
      state: s,
      esc: e,
      icon: i,
      api,
      render,
      toast,
      persistDraft,
      clone,
    } = C;
    const F = createForms(C),
      { button, input, select, toggle, panel, note, empty, advanced } = F;
    const summaryMedia =
      typeof matchMedia === 'function'
        ? matchMedia('(min-width: 1250px)')
        : null;
    let firstDraft = false;
    try { firstDraft = !localStorage.getItem('atlas-quant-statistical-draft-v2') && !localStorage.getItem('atlas-quant-draft-v1'); } catch {}
    const ui = {
      ready: false,
      firstDraft,
      pageErrors: [],
      pageErrorInputs: null,
      errors: [],
      experiments: [],
      experimentsLoaded: false,
      experimentsError: '',
      experimentsRequest: 0,
      recipes: [],
      recipesLoaded: false,
      loading: false,
      activeId: null,
      activeVersion: null,
      boundSource: null,
      universeScope: null,
      runError: '',
      summaryOpen: summaryMedia?.matches || false,
      summaryUserChoice: null,
      featureTab: 'catalog',
      recipeQuery: '',
      recipePage: 1,
      recipeTotal: 0,
      recipeLoading: false,
      recipeError: '',
      recipeRequest: 0,
      compareRequest: 0,
      experimentPage: 1,
      experimentTotal: 0,
      summary: null,
      comparePage: 1,
      compareTotal: 0,
      comparisons: [],
      comparisonHistoryPage: 1,
      comparisonHistoryTotal: 0,
      compareKind: 'forecast',
      compareChoices: [],
      compareLoaded: false,
      compareError: '',
      compareIds: [],
      compare: null,
      compareLoading: false,
      comparisonHistoryLoaded: false,
      comparisonHistoryLoading: false,
      comparisonHistoryError: '',
      comparisonHistoryRequest: 0,
      comparisonDetailLoading: false,
      comparisonDetailError: '',
      comparisonDetailId: null,
      comparisonDetailRequest: 0,
      selectedModules: [],
      viewedExperiment: null,
      experimentDetailLoading: false,
      experimentDetailError: '',
      experimentDetailId: null,
      experimentDetailRequest: 0,
    };
    const catalog = createModuleCatalog(C, F, applyModule);
    const financial = createFinancialWorkspace(C, F);
    const datasets = createDatasetWorkspace(C, F, { onBind: bindDataset });
    const market = createMarketPreparation(C, F, { freezeScope, onBind: bindMarket });
    try {
      const saved = JSON.parse(
        localStorage.getItem('atlas-quant-statistical-draft-v2') || 'null',
      );
      ui.activeId = saved?.experimentId || null;
      ui.activeVersion = saved?.experimentVersion || null;
      ui.universeScope = saved?.universeScope || null;
      ui.boundSource = saved?.boundSource || null;
    } catch {}
    const reports = createForecastReports(C, F);
    const industryBrowser = createIndustryBrowser(C, F);
    const isStudio = () => s.quantMode === 'studio';
    const step = () =>
      s.view === 'runs'
        ? 'report'
        : STEPS.some((x) => x.id === s.quantStep)
          ? s.quantStep
          : 'universe';
    const route = (id = step(), studio = isStudio()) =>
      'quant/' + (studio ? 'studio/' : 'easy/') + ({ target: 'model', validation: 'report', risk: 'strategies', execution: 'instruments' }[id] || id);
    const hub = createModuleHub(C, F, { heading, route });
    const goto = (id) => {
      location.hash = route(id);
    };
    const stepOptions = () => ({ easy: !isStudio(), dataSource: s.dataSource, dataset: s.dataset, session: s.session });
    function setPageErrors(errors) {
      ui.pageErrors = [...new Set(errors)];
      ui.pageErrorInputs = { strategy: JSON.stringify(s.strategy), source: s.dataSource, dataset: s.dataset };
    }
    function showStepErrors(id, errors) {
      setPageErrors(errors);
      s.quantStep = id;
      window.history.replaceState(null, '', '#' + route(id));
      render();
      const error = document.querySelector('#sq-page-errors');
      error?.focus({ preventScroll: true });
      error?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    }
    function admitStep(destination) {
      const incomplete = firstIncompleteStep(s.strategy, destination, stepOptions());
      if (!incomplete) return true;
      showStepErrors(incomplete.step, incomplete.errors);
      return false;
    }
    const targetLabel = () =>
      s.strategy.target?.kind === 'frozen_basket'
        ? '冻结数量篮子'
        : '单资产价格';
    const legacy = () => !isStatistical(s.strategy);
    const sourceLabel = () => SOURCE_LABELS[s.dataSource] || '待选择';

    function sidebar() {
      const factorWorkspace = s.view === 'runs' || (s.view === 'quant' && !['modes', 'statistical', 'strategies', 'instruments', 'monitor'].includes(s.quantStep));
      return `<aside class="sq-sidebar"><a href="#dashboard" class="sq-brand"><span class="atlas-mark">A</span><span>atlas <b>quant</b><small>OPEN QUANTITATIVE RESEARCH</small></span></a><a class="sq-workspace-label" href="#${route('statistical')}">统计量化交易<span>STATISTICAL QUANT</span></a><nav aria-label="工作区"><a href="#${route('statistical')}" class="sq-nav ${s.view === 'dashboard' || s.quantStep === 'statistical' ? 'active' : ''}">${i('workflow')}统计量化交易</a><a href="#quant/researches" class="sq-nav ${s.quantStep === 'researches' ? 'active' : ''}">${i('save')}我的研究<span>${ui.experimentsLoaded ? ui.experimentTotal || '' : ''}</span></a></nav><a class="sq-nav-caption sq-research-entry" href="#quant/modes">因子研究 <span>01 — 05</span></a>${factorWorkspace ? `<nav aria-label="统计量化研究步骤">${STEPS.map((x, n) => `<a class="sq-step ${s.view === 'quant' && s.quantStep === x.id ? 'active' : ''}" href="#${route(x.id)}" ${s.view === 'quant' && s.quantStep === x.id ? 'aria-current="step"' : ''}><b>${String(n + 1).padStart(2, '0')}</b><span>${x.name}</span>${i(x.icon)}</a>`).join('')}</nav><div class="sq-nav-caption">研究工具</div><nav><a href="#quant/studio/datasets/source" class="sq-nav ${s.quantStep === 'datasets' ? 'active' : ''}">${i('database')}行情与财务数据集</a><a href="#quant/studio/financial" class="sq-nav ${s.quantStep === 'financial' ? 'active' : ''}">${i('database')}财务输入与状态</a><a href="#quant/studio/code" class="sq-nav ${s.quantStep === 'code' ? 'active' : ''}">${i('code')}代码与 AI 审阅</a><a href="#quant/community" class="sq-nav ${s.quantStep === 'community' ? 'active' : ''}">${i('users')}因子社区</a><a href="#quant/recipes" class="sq-nav">${i('layers')}模块配方目录</a><a href="#quant/compare" class="sq-nav">${i('chart')}研究比较</a><a href="#quant/history" class="sq-nav ${s.quantStep === 'history' ? 'active' : ''}">${i('clock')}历史版本研究</a></nav>` : ''}<div class="sq-sidebar-bottom"><span><i class="dot ${s.session?.runner?.online ? 'online' : ''}"></i>${s.session?.runner?.online ? '计算节点在线' : '计算节点状态待确认'}</span><a href="https://github.com/eprestonyi/atlas-quant" target="_blank" rel="noopener noreferrer">${i('code')} GitHub 开源代码</a><a href="/cn/terminal">${i('external')} Atlas Terminal</a></div></aside>`;
    }
    function topbar() {
      const title = s.view === 'dashboard' ? '统计量化交易' : STEPS.find((x) => x.id === s.quantStep)?.name || { modes: '选择模式', statistical: '统计量化交易', strategies: '策略研究', instruments: '执行仪器', monitor: 'E 检测仪', researches: '我的研究', code: '代码与 AI', financial: '财务输入与状态', datasets: '行情与财务数据集', history: '历史版本', community: '因子社区', compare: '研究比较' }[s.quantStep] || '报告';
      const research = s.view === 'runs' || s.quantStep === 'modes' || STEPS.some(x => x.id === s.quantStep);
      return `<header class="sq-topbar"><a href="#dashboard" class="sq-mobile-brand">atlas <b>quant</b></a><div class="sq-breadcrumb"><a href="#quant/statistical">统计量化交易</a>${research ? '<span>/</span><a href="#quant/modes">因子研究</a>' : ''}${title !== '统计量化交易' ? `<span>/</span><strong>${e(title)}</strong>` : ''}</div><div class="sq-topbar-actions"><a class="sq-source-link" href="https://github.com/eprestonyi/atlas-quant" target="_blank" rel="noopener noreferrer" aria-label="查看 Atlas Quant GitHub 开源代码">${i('code')}<span>源码</span></a><a class="sq-window-link" href="#quant/statistical" target="_blank" rel="noopener" title="在独立窗口中打开统计量化工作区" aria-label="独立打开统计量化工作区">${i('external')}</a></div></header>`;
    }
    function heading(kicker, title, description, actions = '') {
      return `<div class="sq-page-heading"><div><span class="sq-kicker">${e(kicker)}</span><h1>${e(title)}</h1>${description ? `<p>${e(description)}</p>` : ''}</div>${actions ? `<div class="sq-actions">${actions}</div>` : ''}</div>`;
    }
    function researchName() {
      return `<div class="sq-research-name"><label><span>当前研究</span><input id="sq-research-name" maxlength="80" value="${e(s.strategy.name)}" aria-label="研究名称"></label><span>${ui.activeId ? `版本 ${ui.activeVersion}` : '未保存草稿'} · ${s.dirty ? '有更改' : '已保存'}</span></div>`;
    }
    function summary() {
      if (legacy() || !isStudio()) return '';
      const st = s.strategy;
      return `<aside class="sq-summary"><details ${ui.summaryOpen ? 'open' : ''} id="sq-summary"><summary>当前研究协议 <span>${st.universe.symbols.length} 标的 · ${st.factors.length} 因子</span>${i('sliders')}</summary><div><span class="sq-kicker">CONFIGURATION SNAPSHOT</span><h3>${e(st.name)}</h3><dl><dt>研究范围</dt><dd>${st.universe.symbols.length} 个筛选成员</dd><dt>目标</dt><dd>${targetLabel()}</dd><dt>预测期限</dt><dd>${e(st.target.horizonSessions)} 个交易日</dd><dt>模型族</dt><dd>${e(FAMILIES[st.model.family]?.name || st.model.family)}</dd><dt>拟合方式</dt><dd>${!isStudio() && !boundDataset() ? '系统按时间验证选择' : e(ESTIMATORS[st.model.estimator] || st.model.estimator)}</dd><dt>观察 / 重拟合</dt><dd>${st.research.observationDays} / ${st.model.refitDays} 日</dd><dt>研究类型</dt><dd>因子模型研究</dd><dt>数据</dt><dd>${e(sourceLabel())}</dd></dl><div class="sq-summary-equation">V̂ = F<sub>h</sub>(X<sub>t</sub>)<br><small>e = 当前状态 − 预期未来状态</small></div>${button('save', '保存研究', { icon: 'save', disabled: s.saving })}</div></details></aside>`;
    }
    function frame(body) {
      return `<div class="sq-shell">${sidebar()}<main class="sq-main" id="main-content" tabindex="-1">${topbar()}${['modes','statistical','strategies','instruments','monitor'].includes(s.quantStep) || s.view === 'dashboard' ? '' : ['financial', 'datasets'].includes(s.quantStep) ? '<div class="sq-mobile-step"><a href="#quant/studio/financial">财务输入列表</a><a href="#quant/studio/state">返回研究</a></div>' : `<div class="sq-mobile-step"><label for="sq-step-picker">研究步骤</label><select id="sq-step-picker">${STEPS.map((x, n) => `<option value="${x.id}" ${step() === x.id ? 'selected' : ''}>${n + 1}. ${x.name}</option>`).join('')}</select><a href="#quant/researches">研究列表</a></div>`}<div class="sq-content">${s.error ? note(s.error, 'error') + '<button class="sq-button small" data-action="refresh">重新连接服务</button>' : ''}${ui.errors.length ? note(ui.errors.join('；'), 'warning') + button('workspace-retry', '重新读取工作区', { small: true }) : ''}${body}</div><footer class="sq-footer"><span>ATLAS QUANT · OPEN RESEARCH</span><span>预测有据 · 目标固定 · 执行可核对</span></footer></main></div>`;
    }
    function recipesView() {
      if (ui.recipeError)
        return (
          note(ui.recipeError, 'error') +
          button('recipe-retry', '重新加载配方', { small: true })
        );
      if (ui.recipeLoading || !ui.recipesLoaded)
        return '<div class="sq-loading" role="status">正在读取配方目录…</div>';
      return `<div class="sq-recipe-grid">${
        ui.recipes
          .slice(0, s.quantStep === 'recipes' ? 12 : 6)
          .map(
            (item) =>
              `<article class="sq-recipe-card"><span class="sq-kicker">RESEARCH RECIPE · v${e(item.version || 1)}</span><h3>${e(item.name)}</h3><p>${e(item.description || item.mechanism || '组合公开模块来检验一个研究假设。')}</p><small>${e(item.availability?.reason || '有效性由实际运行检验')}</small>${button('recipe', '查看并应用配方', { icon: 'fork', id: item.id, disabled: item.availability?.status && item.availability.status !== 'ready' })}</article>`,
          )
          .join('') ||
        empty('配方目录尚未就绪', '可以从明确的目标与模型配置创建研究。')
      }</div>`;
    }
    function page() {
      const previous = ui.pageErrorInputs;
      if (ui.pageErrors.length && previous && (previous.strategy !== JSON.stringify(s.strategy) || previous.source !== s.dataSource || previous.dataset !== s.dataset)) {
        ui.pageErrors = [];
        ui.pageErrorInputs = null;
      }
      const st = step(),
        index = STEPS.findIndex((x) => x.id === st),
        meta = STEPS[index];
      if (legacy())
        return `${heading('HISTORICAL CONFIGURATION', '这是历史版本研究', '原始配置和报告保持其当时的语义。新研究采用独立预测协议。', button('new', '创建新的预测研究', { primary: true, icon: 'plus' }))}<div class="sq-legacy">${C.legacy.renderScreen('code')}</div>`;
      return `${heading(`${isStudio() ? 'QUANT STUDIO' : 'EASY MODE'} / ${String(index + 1).padStart(2, '0')}`, meta.name, '', button('save', '保存', { icon: 'save', disabled: s.saving }))}${researchName()}${ui.pageErrors.length ? `<div id="sq-page-errors" tabindex="-1" role="alert">${note(ui.pageErrors.join('；'), 'warning')}${!isStudio() && (st === 'state' || ui.pageErrors.some(message => message.includes('Studio'))) ? `<a class="sq-button small" href="#${route(st, true)}">在 Studio 配置</a>` : ''}</div>` : ''}<div class="sq-progress"><span style="width:${((index + 1) / STEPS.length) * 100}%"></span></div><div class="sq-research-layout ${isStudio() ? '' : 'sq-focused-layout'}"><div class="sq-research-body">${{ universe: universePage, state: statePage, settings: settingsPage, model: modelPage, report: reviewPage }[st]()}<div class="sq-page-navigation">${index ? button('step', `上一步 · ${STEPS[index - 1].short}`, { id: STEPS[index - 1].id }) : `<a class="sq-button" href="#${route('statistical')}">统计量化交易</a>`}<span>STEP ${index + 1} OF ${STEPS.length}</span>${index < STEPS.length - 1 ? button('step', `下一步 · ${STEPS[index + 1].short}`, { id: STEPS[index + 1].id, direction: 'next', primary: true, icon: 'arrow' }) : button('run', s.submitting ? '正在提交' : '拟合并生成 F 模型', { primary: true, icon: 'play', disabled: s.submitting })}</div></div>${summary()}</div>`;
    }
    const boundDataset = () =>
      s.dataSource === 'ready_dataset' && s.datasetBinding;
    const boundAdmissionIs = estimator => {
      const profile = financialProfile(s.datasetBinding?.datasetRef, estimator);
      return !!profile && profile === s.datasetBinding?.admissionProfile;
    };
    function boundNote() {
      return (
        note(
          boundAdmissionIs('auto')
            ? '本研究使用已冻结财务数据和完整范围，以基本面条件自动拟合未来状态；只生成预测，不执行交易。'
            : boundAdmissionIs('ridge') ? '这是已声明的基本面 / Ridge / 单资产价格协议。冻结范围和原模型选择保持不变，交易执行关闭。' : '当前财务来源与计算协议尚未识别，不能保存或运行；请重新读取原版本。',
        ) +
        `<a class="sq-button" href="${e(datasetLocation(s.datasetBinding.datasetRef)?.page || '#quant/studio/datasets/source')}">查看数据覆盖与完整来源</a>`
      );
    }
    function bindMarket(binding) {
      s.dataSource = 'ready_market';
      s.marketDatasetBinding = clone(binding);
      s.datasetBinding = null;
      s.dataset = null;
      ui.universeScope = { ref: clone(binding.universeScopeRef), key: scopeKey(s.strategy.universe), workspaceId: s.session?.workspace?.id };
      persistDraft();
    }
    async function bindDataset(detail, stateIds, stateDefinitions = [], options = {}) {
      const estimator = options.estimator || 'auto', available = financialAdmission(detail, estimator);
      if (!available.available) throw Error(available.reason || '所选财务计算协议不可用。');
      if (!detail.researchBindingEnabled || !stateIds?.length)
        throw Error('该数据集目前不能创建模型研究。');
      const strategy = defaultStrategy();
      strategy.name = detail.name + ' · 财务预测';
      strategy.universe = clone(detail.scope);
      // A newly created bound study chooses an explicit boundary inside its immutable dates.
      const from = Date.parse(C.dateText(detail.scope.start)), to = Date.parse(C.dateText(detail.scope.end));
      if (Number.isFinite(from) && Number.isFinite(to) && from < to)
        strategy.validation.testStart = new Date(from + (to - from) * .8).toISOString().slice(0, 10).replaceAll('-', '');
      else delete strategy.validation.testStart;
      strategy.model.family = 'fundamental';
      strategy.model.estimator = estimator;
      strategy.target.kind = 'asset_price';
      strategy.execution.enabled = false;
      strategy.factors = stateIds.map((id) => ({
        id,
        expression: id,
        direction: 1,
        role: 'predictor',
      }));
      s.strategy = normalizeStrategy(strategy);
      s.dataSource = 'ready_dataset';
      s.marketDatasetBinding = null;
      s.dataset = null;
      s.datasetBinding = {
        datasetRef: clone(detail.datasetRef),
        admissionProfile: available.admission.profile,
        workspaceId: s.session?.workspace?.id || null,
        scope: clone(detail.scope),
        selectedStateIds: [...stateIds],
        stateDefinitions: clone(
          stateDefinitions
            .filter((x) => stateIds.includes(x.id))
            .map((x) => ({ id: x.id, name: x.name })),
        ),
      };
      ui.activeId = null;
      ui.activeVersion = null;
      s.strategyId = null;
      s.strategyVersion = null;
      persistDraft();
      location.hash = route('state', estimator === 'ridge');
    }
    function universePage() {
      if (boundDataset())
        return panel(
          '已冻结的研究范围',
          boundNote() +
            `<dl class="fin-summary"><dt>明确成员</dt><dd>${e(s.strategy.universe.symbols.join('、'))}</dd></dl>`,
        );

      return `<div class="sq-legacy sq-universe">${C.legacy.flow.universePage()}</div>`;
    }
    function statePage() {
      if (boundDataset()) return panel('因子库', `<div class="ds-members">${s.datasetBinding.selectedStateIds.map((id) => `<label class="fin-checkbox"><input type="checkbox" data-sq-dataset-state="${e(id)}" ${s.strategy.factors.some((f) => f.expression === id) ? 'checked' : ''}>${e(s.datasetBinding.stateDefinitions?.find((x) => x.id === id)?.name || s.strategy.factors.find((f) => f.id === id)?.name || id)}</label>`).join('')}</div>`);
      const factors = s.strategy.factors;
      const selected = `<div class="sq-selected-factors" data-sq-drop="state" data-v2-drop="recipe"><div class="sq-section-heading"><h2>已选因子 <span>${factors.length} / 32</span></h2></div>${factors.map((f) => `<article class="sq-selected-factor"><span class="sq-drag-grip">⠿</span><div><strong>${e(C.findFactor(f.id)?.name || f.name || f.id)}</strong>${isStudio() ? `<code>${e(f.expression)}</code>` : ''}</div>${isStudio() ? `<label><span class="sr-only">${e(C.findFactor(f.id)?.name || f.id)} 的因子角色</span><select data-sq-factor-role="${e(f.id)}"><option value="predictor" ${(f.role || 'predictor') === 'predictor' ? 'selected' : ''}>预测因子</option><option value="hedge" ${f.role === 'hedge' ? 'selected' : ''}>对冲暴露</option><option value="event" ${f.role === 'event' ? 'selected' : ''}>事件输入</option></select></label>` : ''}${button('remove-factor', '移除', { icon: 'close', small: true, id: f.id, ariaLabel: '移除 ' + (C.findFactor(f.id)?.name || f.id) })}</article>`).join('')}<div class="sq-drop-caption">${i('plus')}拖入因子</div></div>`;
      const tabs = [['catalog', '因子库'], ['industries', '行业 / ETF'], ['builder', '构建因子'], ...(isStudio() ? [['modules', '状态模块'], ['fields', '数据库字段']] : [])];
      const active = tabs.some(([id]) => id === ui.featureTab) ? ui.featureTab : 'catalog';
      const library = active === 'industries' ? industryBrowser.view() : active === 'modules' ? catalog.view('state') : `<div class="sq-legacy sq-factor-library">${active === 'builder' ? C.legacy.builder({ compact: true }) : active === 'fields' ? C.legacy.fieldBrowser() : C.legacy.catalogBrowser({ compact: true })}</div>`;
      return `<div class="sq-factor-workbench"><div class="sq-factor-source"><div class="sq-tabs" role="group" aria-label="因子来源">${tabs.map(([id, label]) => button('feature-tab', label, { id, primary: active === id, pressed: active === id, small: true })).join('')}</div>${library}</div>${selected}</div>` +
        (isStudio() ? advanced('预处理与输入冗余', `<div class="sq-form-grid">${toggle('训练期截尾', 'preprocess.winsorize')}${toggle('训练期标准化', 'preprocess.standardize')}${select('冗余处理', 'preprocess.decorrelation', { none: '保留全部输入', drop_correlated: '剔除高度相关输入' })}${input('绝对相关阈值', 'preprocess.correlationThreshold', { min: 0.5, max: 1, step: 0.01 })}</div>`) : '');
    }
    const mechanismStatus = family => mechanismAdmission(s, family, market.researchAdmission, isStudio());
    function suggestedTestStart() {
      const u = s.strategy.universe;
      const parse = value => Date.parse(`${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)}T00:00:00Z`);
      const start = parse(u.start), end = parse(u.end);
      return Number.isFinite(start) && Number.isFinite(end) && start < end
        ? new Date(start + (end - start) * (1 - s.strategy.validation.holdoutFraction)).toISOString().slice(0,10).replaceAll('-', '')
        : '';
    }
    function settingsPage() {
      const st = s.strategy, split = st.validation.testStart;
      const boundary = (label, path, value) => boundDataset()
        ? `<div class="sq-field"><span>${e(label)}</span><output>${e(C.dateText(value))}</output></div>`
        : input(label, path, { type: 'date', value: C.dateText(value) });
      return `<div class="sq-window-grid">${panel('训练集', `<div class="sq-form-grid">${boundary('开始日期', 'universe.start', st.universe.start)}<div class="sq-field"><span>${split ? '结束边界（不含）' : '日期占比'}</span><output>${split ? e(C.dateText(split)) : `${Math.round((1-st.validation.holdoutFraction)*100)}%`}</output></div></div>`)}${panel('测试集', `<div class="sq-form-grid">${split ? input('开始日期', 'validation.testStart', { type: 'date', value: C.dateText(split) }) : input('末尾日期占比', 'validation.holdoutFraction', { min: .1, max: .4, step: .05 })}${boundary('结束日期', 'universe.end', st.universe.end)}</div>${split ? '' : button('date-split', '改为日期切分', { small: true })}`)}</div>${panel('滚动窗口', `<div class="sq-form-grid">${input('训练窗口', 'model.trainWindow', { min: 120, max: 1260, unit: '交易日' })}${input('重新拟合间隔', 'model.refitDays', { min: 1, max: 126, unit: '交易日' })}</div>`)}`;
    }
    function targetPage() {
      const t = s.strategy.target;
      const choices = boundDataset() || !isStudio() ? '' : `<div class="sq-choice-grid">${[
        ['asset_price', '单资产价格'], ['frozen_basket', '冻结数量篮子'],
      ].map(([id, name]) => `<button class="sq-choice ${t.kind === id ? 'selected' : ''}" data-sq="target-kind" data-id="${id}" aria-pressed="${t.kind === id}">${i(id === 'asset_price' ? 'chart' : 'layers')}<strong>${name}</strong></button>`).join('')}</div>`;
      const objects = !isStudio() && !boundDataset()
        ? s.strategy.model.family === 'pair_reversion' ? pairObjects()
          : t.kind === 'frozen_basket' ? `<div class="sq-data-binding"><span>研究对象：${e((t.basket?.symbols || []).join('、') || '尚未设置')}</span><a class="sq-button small" href="#${route('model', true)}">在 Studio 调整</a></div>${easyTargetScopeMismatch(s.strategy) ? `<div class="sq-target-repair" role="status">${note(easyTargetScopeMessage(s.strategy), 'warning')}<div class="sq-actions">${canUseIndividualTarget(s.strategy) ? button('repair-asset-target', '改为逐只研究当前股票', { small: true }) : ''}<a class="sq-button small" href="#${route('universe')}">调整筛选，保留原组合</a></div></div>` : ''}` : ''
        : '';
      return panel(isStudio() ? '研究目标' : '研究设置', `${choices}${objects}<div class="sq-form-grid">${input('预测期限', 'target.horizonSessions', { min: 1, max: 60, unit: '交易日' })}${input('观察间隔', 'research.observationDays', { min: 1, max: 60, unit: '交易日' })}</div>`) +
        (isStudio() && t.kind === 'frozen_basket' ? basketDefinition() : '');
    }
    function pairObjects() {
      const selected = isPairTarget(s.strategy) ? s.strategy.target.basket.symbols : [], members = s.strategy.universe.symbols;
      return `<div class="sq-form-grid sq-pair-objects">${[0, 1].map(index => `<label class="sq-field"><span>配对对象${index + 1}</span><select data-sq-pair-object="${index}" required aria-label="配对对象${index + 1}"><option value="">选择股票</option>${selected[index] && !members.includes(selected[index]) ? `<option value="${e(selected[index])}" selected disabled>${e(selected[index])} · 已移出筛选范围</option>` : ''}${members.map(code => `<option value="${e(code)}" ${selected[index] === code ? 'selected' : ''} ${selected[1-index] === code ? 'disabled' : ''}>${e(code)}</option>`).join('')}</select></label>`).join('')}</div>`;
    }
    function basketDefinition() {
      const b = s.strategy.target.basket || {};
      return panel('篮子定义', `${select('构造方法', 'target.basket.method', { pair_ols: '两腿价格 OLS 配对', pca_residual: 'PCA 投影状态篮子', fixed: '固定数量' })}<div class="sq-basket-heading"><span>篮子成员</span><strong>${(b.symbols || []).length}${b.method === 'pair_ols' ? ' / 2' : ' / 20'}</strong></div><div class="sq-basket-members">${s.strategy.universe.symbols.map((code) => `<label><input type="checkbox" data-sq-basket-symbol="${e(code)}" ${(b.symbols || []).includes(code) ? 'checked' : ''}><span>${e(code)}</span>${b.method === 'fixed' ? `<input type="number" data-sq-quantity="${e(code)}" aria-label="${e(code)} 固定数量" value="${e(b.quantities?.[code] ?? '')}" min="-1000000" max="1000000" step="any" ${(b.symbols || []).includes(code) ? '' : 'disabled'}>` : ''}</label>`).join('') || '<span>暂无筛选成员</span>'}</div><div class="sq-form-grid">${b.method !== 'fixed' ? input('形成窗口', 'target.basket.formationDays', { min: 60, max: 504, unit: '交易日' }) : ''}${b.method === 'pca_residual' ? input('主成分数量', 'target.basket.components', { min: 1, max: Math.min(10, Math.max(1, (b.symbols || []).length - 2)) }) : ''}</div>`);
    }
    function modelPage() {
      const mechanisms = boundDataset()
        ? `<div class="sq-family-grid"><button class="sq-family selected" type="button" aria-pressed="true" disabled><span>${i('model')}</span><strong>${e(FAMILIES[s.strategy.model.family]?.name || '基本面条件预测')}</strong></button></div>`
        : `<div class="sq-family-grid">${Object.entries(FAMILIES).map(([id, x]) => {
            const status = mechanismStatus(id);
            return `<button class="sq-family ${s.strategy.model.family === id ? 'selected' : ''}" data-sq="family" data-id="${id}" aria-pressed="${s.strategy.model.family === id}" data-mechanism-status="${e(status.status)}" ${!status.selectable ? 'disabled' : ''}><span>${i(id === 'pair_reversion' ? 'link' : id === 'event' ? 'spark' : 'model')}</span><strong>${e(x.name)}</strong>${!status.selectable ? '<small>暂不可用</small>' : ''}</button>`;
          }).join('')}</div>`;
      const data = boundDataset()
        ? `<div class="sq-data-binding"><span>${e(sourceLabel())}</span><a class="sq-button small" href="${e(datasetLocation(s.datasetBinding.datasetRef)?.page || '#quant/studio/datasets/source')}">查看数据</a></div>`
        : `<div class="sq-legacy">${C.legacy.flow.sourceControls({ compact: true })}</div>`;
      return panel('研究机制', mechanisms) + targetPage() +
        panel('数据', data) +
        (!boundDataset() && ['tushare', 'ready_market'].includes(s.dataSource) ? advanced('数据准备', market.view()) : '') +
        (isStudio() ? advanced('模型设置', `<div class="sq-form-grid">${select('函数估计方式', 'model.estimator', ESTIMATORS)}${input('最少训练日期', 'validation.minTrainDates', { min: 40, max: 252 })}${input('内层时间折数', 'validation.innerFolds', { min: 2, max: 3 })}${input('外层时间折数', 'validation.outerFolds', { min: 2, max: 3 })}</div>`) + advanced('跨数据库时点映射', C.legacy.mappingEditor()) : '');
    }
    function reviewPage() {
      const errors = validateStrategy(s.strategy, {
        includeData: true,
        dataSource: s.dataSource,
        dataset: s.dataset,
        session: s.session,
      });
      return `${ui.runError ? panel('运行准入未通过', note(ui.runError, 'error')) : ''}${panel('F 模型', `${errors.length ? errors.map((x) => note(x, 'warning')).join('') : ''}<div class="sq-model-placeholder"><span>F<sub>h</sub>(X)</span><strong>尚未拟合</strong></div><dl class="fin-summary"><dt>研究机制</dt><dd>${e(FAMILIES[s.strategy.model.family]?.name)}</dd><dt>范围</dt><dd>${s.strategy.universe.symbols.length} 个成员 · ${s.strategy.factors.length} 个因子</dd><dt>目标</dt><dd>${targetLabel()} · ${s.strategy.target.horizonSessions} 交易日</dd><dt>数据</dt><dd>${e(sourceLabel())}</dd></dl><div class="sq-actions">${button('save', '保存版本', { icon: 'save' })}${button('export', '导出配置', { icon: 'download' })}${ui.activeId ? button('experiment-detail', '已生成的模型与报告', { id: ui.activeId, icon: 'clock' }) : ''}</div>`)}`;
    }
    function latestRunStatus(item) {
      if (!Object.hasOwn(item, 'latestRun'))
        return '<span>运行状态尚未返回</span>';
      if (!item.latestRun) return '<span>尚未运行</span>';
      const run = item.latestRun;
      const label =
        {
          queued: '排队中',
          running: '计算中',
          completed: '已完成',
          failed: '失败',
          cancelled: '已取消',
        }[run.status] || '状态待确认';
      return `<a class="sq-inline-link" href="#runs/${encodeURIComponent(run.id)}">${e(label)}</a><small>${run.jobKind === 'execution' ? '独立执行' : '预测研究'} · 研究 v${e(run.experimentVersion ?? '—')}</small>`;
    }
    function experimentList() {
      return `${heading('MY RESEARCH', '我的统计研究', '版本化配置、预测产物与独立执行记录。', button('new', '新建研究', { primary: true, icon: 'plus' }))}${panel(
        '已保存的研究',
        ui.experimentsError
          ? note(ui.experimentsError, 'error') +
              button('refresh-experiments', '重试读取研究', { small: true })
          : ui.loading || !ui.experimentsLoaded
            ? '<div class="sq-loading" role="status">正在读取私有研究…</div>'
            : ui.experiments.length
              ? `<div class="sq-table-scroll"><table class="sq-table"><thead><tr><th>研究名称</th><th>目标 / 模型</th><th>版本</th><th>最近状态</th><th>操作</th></tr></thead><tbody>${ui.experiments
                  .map((item) => {
                    const spec = item.strategy || item.spec || {};
                    return `<tr><td><button class="sq-inline-link" data-sq="experiment-detail" data-id="${e(item.id)}">${e(item.name || spec.name || '未命名研究')}</button><small>${e(C.dateText(item.updatedAt || item.createdAt))}</small></td><td>${e(spec.target?.kind || item.targetKind || '—')}<small>${e(spec.model?.family || item.modelFamily || '')}</small></td><td>v${e(item.version)}</td><td>${latestRunStatus(item)}</td><td><div class="sq-actions">${button('experiment-load', '编辑', { small: true, id: item.id })}${button('experiment-copy', '复制', { small: true, id: item.id, icon: 'fork' })}</div></td></tr>`;
                  })
                  .join('')}</tbody></table></div>`
              : empty(
                  '还没有保存的统计研究',
                  '从一个明确目标开始；保存后保留版本与运行关系。',
                  button('new', '新建研究', { primary: true }),
                ),
        {
          actions: button('refresh-experiments', '刷新', {
            small: true,
            icon: 'refresh',
          }),
        },
      )}<div class="sq-catalog-pagination"><span>${ui.loading || !ui.experimentsLoaded ? '研究数量读取中' : `共 ${ui.experimentTotal} 项 · 第 ${ui.experimentPage} 页`}</span><div>${button('experiment-page', '上一页', { small: true, page: ui.experimentPage - 1, disabled: ui.loading || !ui.experimentsLoaded || ui.experimentPage <= 1 })}${button('experiment-page', '下一页', { small: true, page: ui.experimentPage + 1, disabled: ui.loading || !ui.experimentsLoaded || ui.experimentPage * 100 >= ui.experimentTotal })}</div></div>`;
    }
    function experimentDetail() {
      const record = ui.viewedExperiment,
        item = record && (record.experiment || record.item || record);
      if (!item || item.id !== s.quantEntityId)
        return (
          heading(
            'RESEARCH DETAIL',
            '读取研究详情',
            '配置、运行和预测产物将在这里关联展示。',
          ) +
          panel(
            '私有研究',
            ui.experimentDetailId === s.quantEntityId &&
              ui.experimentDetailError
              ? note(ui.experimentDetailError, 'error') +
                  button('experiment-detail-retry', '重试读取研究', {
                    id: s.quantEntityId,
                    small: true,
                  })
              : '<div class="sq-loading" role="status">正在读取这份研究…</div>',
          )
        );
      const runList = record.runs || item.runs || [];
      return `${heading('RESEARCH DETAIL', item.name || item.strategy?.name || '研究详情', '同一研究的配置版本、运行与预测产物。', button('experiment-load', '编辑配置', { id: item.id, icon: 'sliders' }) + button('experiment-copy', '复制研究', { id: item.id, icon: 'fork' }))}${panel('运行记录', runList.length ? `<div class="sq-run-list">${runList.map((run) => `<article><div><strong>${e(run.name || item.name || run.id)}</strong><span>${e(run.status)} · ${e(C.dateText(run.createdAt))}</span><small>${e(run.id)}</small></div><a class="sq-button" href="#runs/${encodeURIComponent(run.id)}">查看预测与报告 ${i('arrow')}</a></article>`).join('')}</div>` : empty('此研究尚未运行', '配置保存和预测运行是不同状态。', button('experiment-load', '打开配置', { id: item.id, primary: true })))}`;
    }
    function history() {
      return `${heading('HISTORICAL RESEARCH', '历史版本', '保留旧版配置与结果；旧报告不会补造当时不存在的预测。')}${button('restore-legacy-draft', '打开本浏览器旧版草稿', { icon: 'clock', small: true })}<div class="sq-legacy">${C.strategyTable()}${C.runsView()}</div>`;
    }
    function renderWorkspace() {
      let body;
      if (s.view === 'runs')
        body = `<div class="sq-report-wrap">${C.runsView()}</div>`;
      else if (s.quantStep === 'modes') body = hub.modes();
      else if (s.view === 'dashboard') body = hub.statistical();
      else if (s.quantStep === 'statistical') body = hub.statistical();
      else if (['strategies', 'instruments', 'monitor'].includes(s.quantStep)) body = hub.pending(s.quantStep);
      else if (s.quantStep === 'financial') body = financial.render();
      else if (s.quantStep === 'datasets') body = datasets.render();
      else if (s.quantStep === 'researches') body = experimentList();
      else if (s.quantStep === 'experiment') body = experimentDetail();
      else if (s.quantStep === 'compare') body = comparisonPage();
      else if (s.quantStep === 'recipes') body = recipeLibrary();
      else if (s.quantStep === 'code')
        body = `${heading('QUANT STUDIO / CODE', '代码与 AI 审阅', '独立探索状态与方法；只有受支持且已验证的模型协议进入研究引擎。')}<div class="sq-legacy">${C.legacy.renderScreen('code', { embedded: true })}</div>`;
      else if (s.quantStep === 'community')
        body = `<div class="sq-legacy">${C.legacy.renderScreen('community')}</div>`;
      else if (s.quantStep === 'history') body = history();
      else body = page();
      return frame(body);
    }

    async function loadRecipes() {
      const request = ++ui.recipeRequest;
      ui.recipeLoading = true;
      ui.recipeError = '';
      render();
      try {
        const response = await api(
          '/statistical-quant/recipes?' +
            new URLSearchParams({
              q: ui.recipeQuery,
              page: ui.recipePage,
              pageSize: 12,
            }),
        );
        if (request !== ui.recipeRequest) return;
        ui.recipes = response.items || [];
        ui.recipeTotal = response.total || 0;
        ui.recipesLoaded = true;
      } catch (err) {
        if (request === ui.recipeRequest) ui.recipeError = err.message;
      } finally {
        if (request === ui.recipeRequest) {
          ui.recipeLoading = false;
          render();
        }
      }
    }
    function recipeLibrary() {
      return `${heading('VERSIONED MODULE RECIPES', '组合研究配方', '每一项都是配置组合。选择后仍需明确成员、数据和目标数量，效用由实际预测检验。')}<div class="sq-catalog-tools"><label class="sq-search">${i('search')}<input id="sq-recipe-search" aria-label="搜索研究配方" placeholder="模型族、估计器、因子包或目标类型" value="${e(ui.recipeQuery)}"></label></div><div class="sq-catalog-count">${ui.recipeLoading || !ui.recipesLoaded ? '配方数量读取中' : `${ui.recipeTotal} 个配置配方 · 第 ${ui.recipePage} 页`} · 不代表已运行的实验</div>${ui.recipeLoading ? '<div class="sq-loading" role="status">正在搜索版本化配方…</div>' : ui.recipeError ? note(ui.recipeError, 'error') + button('recipe-retry', '重新搜索配方', { small: true }) : recipesView()}<div class="sq-catalog-pagination"><span>每页 12 项</span><div>${button('recipe-page', '上一页', { page: ui.recipePage - 1, small: true, disabled: ui.recipeLoading || !ui.recipesLoaded || ui.recipePage <= 1 })}${button('recipe-page', '下一页', { page: ui.recipePage + 1, small: true, disabled: ui.recipeLoading || !ui.recipesLoaded || ui.recipePage * 12 >= ui.recipeTotal })}</div></div>`;
    }
    async function loadCompareChoices() {
      const request = ++ui.compareRequest,
        kind = ui.compareKind;
      ui.compareLoading = true;
      ui.compareError = '';
      render();
      await Promise.allSettled([
        (async () => {
          try {
            const response = await api(
              '/statistical-quant/' +
                (kind === 'forecast' ? 'forecasts' : 'executions') +
                '?' +
                new URLSearchParams({ page: ui.comparePage, pageSize: 30 }),
            );
            if (request !== ui.compareRequest) return;
            ui.compareTotal = response.total || 0;
            ui.compareChoices = (response.items || []).map((x) => ({
              ...x,
              kind,
              label:
                kind === 'forecast'
                  ? `${x.metadata?.target?.kind === 'frozen_basket' ? '冻结篮子' : '单资产目标'} · ${FAMILIES[x.metadata?.model?.family]?.name || '预测产物'}`
                  : `执行方案 · ${x.status}`,
            }));
            ui.compareLoaded = true;
          } catch (err) {
            if (request === ui.compareRequest) ui.compareError = err.message;
          } finally {
            if (request === ui.compareRequest) {
              ui.compareLoading = false;
              render();
            }
          }
        })(),
        loadComparisonHistory(),
      ]);
    }
    async function loadComparisonHistory() {
      const request = ++ui.comparisonHistoryRequest;
      ui.comparisonHistoryLoading = true;
      ui.comparisonHistoryError = '';
      render();
      try {
        const response = await api(
          '/statistical-quant/comparisons?' +
            new URLSearchParams({
              page: ui.comparisonHistoryPage,
              pageSize: 20,
            }),
        );
        if (request !== ui.comparisonHistoryRequest) return;
        ui.comparisons = response.items || [];
        ui.comparisonHistoryTotal = response.total || 0;
        ui.comparisonHistoryLoaded = true;
      } catch (err) {
        if (request === ui.comparisonHistoryRequest)
          ui.comparisonHistoryError = err.message;
      } finally {
        if (request === ui.comparisonHistoryRequest) {
          ui.comparisonHistoryLoading = false;
          render();
        }
      }
    }
    async function loadComparisonDetail(id) {
      const request = ++ui.comparisonDetailRequest;
      const routeAtStart = location.hash;
      const kindAtStart = ui.compareKind;
      ui.comparisonDetailId = id;
      ui.comparisonDetailLoading = true;
      ui.comparisonDetailError = '';
      render();
      try {
        const response = await api(
          '/statistical-quant/comparisons/' + encodeURIComponent(id),
        );
        if (request !== ui.comparisonDetailRequest) return;
        ui.compare = response.comparison;
        if (
          location.hash === routeAtStart &&
          ui.compareKind === kindAtStart &&
          ['forecast', 'execution'].includes(ui.compare?.kind) &&
          ui.compareKind !== ui.compare.kind
        ) {
          ui.compareKind = ui.compare.kind;
          ui.compareIds = [];
          ui.comparePage = 1;
          ui.compareLoaded = false;
          await loadCompareChoices();
        }
      } catch (err) {
        if (request === ui.comparisonDetailRequest)
          ui.comparisonDetailError = err.message;
      } finally {
        if (request === ui.comparisonDetailRequest) {
          ui.comparisonDetailLoading = false;
          render();
        }
      }
    }
    function comparisonHistory() {
      return panel(
        '已保存的比较',
        ui.comparisonHistoryError
          ? note(ui.comparisonHistoryError, 'error') +
              button('comparison-history-retry', '重试读取已保存比较', {
                small: true,
              })
          : ui.comparisonHistoryLoading || !ui.comparisonHistoryLoaded
            ? '<div class="sq-loading" role="status">正在读取已保存比较…</div>'
            : ui.comparisons.length
              ? `<div class="sq-run-list">${ui.comparisons.map((x) => `<article><div><strong>${e(x.name)}</strong><span>${x.kind === 'forecast' ? '预测比较' : '执行比较'} · ${x.members.length} 项 · ${e(C.dateText(x.createdAt))}</span></div><a class="sq-button small" href="#quant/compare/${encodeURIComponent(x.id)}">打开比较</a></article>`).join('')}</div><div class="sq-catalog-pagination"><span>共 ${ui.comparisonHistoryTotal} 项</span><div>${button('comparison-history-page', '上一页', { small: true, page: ui.comparisonHistoryPage - 1, disabled: ui.comparisonHistoryPage <= 1 })}${button('comparison-history-page', '下一页', { small: true, page: ui.comparisonHistoryPage + 1, disabled: ui.comparisonHistoryPage * 20 >= ui.comparisonHistoryTotal })}</div></div>`
              : empty(
                  '尚未保存比较',
                  '比较产物会保留到私有工作区，可在重新打开页面后继续读取。',
                ),
      );
    }
    async function createComparison() {
      if (ui.compareIds.length < 2 || ui.compareIds.length > 8)
        throw Error('选择 2–8 项同类产物。');
      const response = await api('/statistical-quant/comparisons', {
        method: 'POST',
        body: JSON.stringify({
          kind: ui.compareKind,
          members: ui.compareIds,
          name:
            ui.compareKind === 'forecast' ? '预测证据比较' : '同预测执行比较',
        }),
      });
      ui.compare = response.comparison;
      await loadComparisonHistory();
      location.hash = 'quant/compare/' + encodeURIComponent(ui.compare.id);
      render();
    }
    function comparisonPage() {
      const choices = ui.compareChoices.filter(
          (x) => x.kind === ui.compareKind,
        ),
        r =
          s.quantEntityId && ui.compare?.id !== s.quantEntityId
            ? null
            : ui.compare;
      return `${heading('CONTROLLED RESEARCH COMPARISON', '比较已生成的研究产物', '并排保留好坏结果；只有数据、目标与范围可比时，才称为受控比较。', button('compare-refresh', '刷新产物', { icon: 'refresh' }))}${panel('选择 2–8 项产物', `<label class="sq-field"><span>比较类型</span><select id="sq-compare-kind"><option value="forecast" ${ui.compareKind === 'forecast' ? 'selected' : ''}>预测产物与误差</option><option value="execution" ${ui.compareKind === 'execution' ? 'selected' : ''}>同预测的独立执行</option></select></label>${ui.compareError ? note(ui.compareError, 'error') + button('compare-refresh', '重试读取产物', { small: true }) : ui.compareLoading || !ui.compareLoaded ? '<div class="sq-loading" role="status">读取当前研究产物…</div>' : `<div class="sq-comparison-picker">${choices.map((x) => `<label><input type="checkbox" data-sq-compare="${e(x.id)}" ${ui.compareIds.includes(x.id) ? 'checked' : ''} ${(ui.compareIds.length >= 8 && !ui.compareIds.includes(x.id)) || (x.kind === 'execution' && x.status !== 'completed') ? 'disabled' : ''}><span>${e(x.label)}<small>${e(x.id)} · ${e(C.dateText(x.createdAt))}</small></span></label>`).join('') || empty('尚无可比较产物', '先完成至少两次预测研究，或在同一产物上完成两次独立执行。')}</div>`}<div class="sq-catalog-pagination"><span>${ui.compareLoading || !ui.compareLoaded ? '产物数量读取中' : `共 ${ui.compareTotal} 项 · 第 ${ui.comparePage} 页`}</span><div>${button('compare-page', '上一页', { small: true, page: ui.comparePage - 1, disabled: ui.compareLoading || !ui.compareLoaded || ui.comparePage <= 1 })}${button('compare-page', '下一页', { small: true, page: ui.comparePage + 1, disabled: ui.compareLoading || !ui.compareLoaded || ui.comparePage * 30 >= ui.compareTotal })}</div></div>${button('compare-create', '保存并生成比较', { primary: true, disabled: ui.compareIds.length < 2 || ui.compareLoading || !ui.compareLoaded || Boolean(ui.compareError) })}`)}${s.quantEntityId && !r ? panel('读取已保存比较', ui.comparisonDetailId === s.quantEntityId && ui.comparisonDetailError ? note(ui.comparisonDetailError, 'error') + button('compare-detail-retry', '重试读取比较', { id: s.quantEntityId, small: true }) : '<div class="sq-loading" role="status">正在读取这份比较的成员和结果…</div>') : ''}${
        r
          ? panel(
              '比较结果',
              `${note(r.controlledComparison ? '后台已核对本次比较所需的一致条件。预测或执行差异仍不代表显著或稳定优势。' : '这些产物的目标、数据、范围或预测身份并不一致。仅作并列展示，不能解释为控制其他条件后的增量。', r.controlledComparison ? 'info' : 'warning')}<div class="sq-table-scroll"><table class="sq-table"><thead><tr><th>产物</th>${r.kind === 'forecast' ? '<th>归一化 RMSE</th><th>无变化 MSE</th><th>MSE 改善</th>' : '<th>净收益</th><th>最大回撤</th><th>总成本</th>'}</tr></thead><tbody>${r.items
                .map((x) => {
                  const m =
                    r.kind === 'forecast'
                      ? x.diagnostics?.metrics || {}
                      : x.metrics || {};
                  return `<tr><td><code>${e(x.id)}</code></td>${r.kind === 'forecast' ? `<td>${C.fmt(m.rmse, 6)}</td><td>${C.fmt(m.noChangeMse, 6)}</td><td>${C.fmt(m.mseImprovement, 6)}</td>` : `<td>${C.pct(m.totalReturn)}</td><td>${C.pct(m.maxDrawdown)}</td><td>${C.fmt(m.totalCosts)}</td>`}</tr>`;
                })
                .join(
                  '',
                )}</tbody></table></div>${advanced('可比性与逐项配置证据', `<pre class="sq-report-code">${e(JSON.stringify(r, null, 2))}</pre>`)}<a class="sq-button" href="/quant/api/statistical-quant/comparisons/${encodeURIComponent(r.id)}/download" download>导出比较</a>`,
            )
          : ''
      }${comparisonHistory()}`;
    }
    async function initialize() {
      ui.errors = [];
      await Promise.allSettled([
        refreshExperiments(),
        loadRecipes(),
        (async () => {
          try {
            const result = await api('/statistical-quant/summary');
            ui.summary = result.counts || result || null;
          } catch (err) {
            ui.errors.push('工作区统计：' + err.message);
          }
        })(),
      ]);
      ui.ready = true;
      render();
      await routeChanged();
    }
    async function refreshExperiments() {
      const request = ++ui.experimentsRequest;
      ui.loading = true;
      ui.experimentsError = '';
      render();
      try {
        const result = await api(
          '/statistical-quant/experiments?' +
            new URLSearchParams({ page: ui.experimentPage, pageSize: 100 }),
        );
        if (request !== ui.experimentsRequest) return;
        ui.experiments = result.items || [];
        ui.experimentTotal = result.total || 0;
        ui.experimentsLoaded = true;
      } catch (err) {
        if (request === ui.experimentsRequest)
          ui.experimentsError = err.message;
      } finally {
        if (request === ui.experimentsRequest) {
          ui.loading = false;
          render();
        }
      }
    }
    async function loadExperiment(id, edit = false) {
      const request = ++ui.experimentDetailRequest,
        expectedOwner = s.session?.workspace?.id,
        routeAtStart = location.hash;
      ui.experimentDetailId = id;
      ui.experimentDetailLoading = true;
      ui.experimentDetailError = '';
      render();
      try {
        const response = await api(
          '/statistical-quant/experiments/' + encodeURIComponent(id),
        );
        if (request !== ui.experimentDetailRequest || expectedOwner !== s.session?.workspace?.id) return;
        ui.viewedExperiment = response;
        const item = response.experiment || response.item || response;
        if (edit && location.hash === routeAtStart) {
          s.strategy = normalizeStrategy(item.strategy || item.spec);
          restoreBindings(s, item);
          ui.boundSource = item.marketDatasetBinding ? 'ready_market' : item.datasetBinding ? 'ready_dataset' : null;
          ui.activeId = item.id;
          ui.activeVersion = item.version;
          ui.universeScope = item.universeScopeRef ? { ref: clone(item.universeScopeRef), key: scopeKey(s.strategy.universe), workspaceId: s.session?.workspace?.id } : null;
          C.legacy.flow.reset();
          s.strategyId = null;
          s.strategyVersion = null;
          s.dirty = false;
          persistDraft(false);
          goto('universe');
        }
      } catch (err) {
        if (request === ui.experimentDetailRequest) {
          ui.experimentDetailError = err.message;
          if (edit) toast(err.message, true);
        }
      } finally {
        if (request === ui.experimentDetailRequest) {
          ui.experimentDetailLoading = false;
          render();
        }
      }
    }
    async function freezeScope(universe, dataSource) {
      if (!universe.selection || dataSource === 'ready_dataset') return null;
      const key = scopeKey(universe), expectedOwner = s.session?.workspace?.id;
      if (ui.universeScope?.key === key && ui.universeScope.workspaceId === expectedOwner) return clone(ui.universeScope.ref);
      const response = await api('/universe-scopes', {
        method: 'POST',
        body: JSON.stringify({ selection: universe.selection, expectedResolutionHash: universe.resolutionHash, expectedSnapshotHash: universe.snapshotHash, start: universe.start, end: universe.end }),
      });
      const ref = response.scopeRef;
      if (ref?.format !== 'atlas.quant.universe_scope' || ref.version !== 1 || !ref.scopeId || !/^[a-f0-9]{64}$/.test(ref.scopeRoot || '')) throw Error('筛选集合未返回有效的冻结引用，研究尚未保存。');
      if (response.scope && (response.scope.symbolCount !== universe.symbols.length || JSON.stringify(response.scope.symbols) !== JSON.stringify(universe.symbols) || response.scope.start !== universe.start || response.scope.end !== universe.end)) throw Error('冻结结果与本次完整筛选范围不一致，请重新计算筛选集合。');
      if (expectedOwner !== s.session?.workspace?.id) throw Error('工作区身份已变化，冻结响应未写入当前研究。');
      ui.universeScope = { ref: clone(ref), key, workspaceId: expectedOwner };
      return ref;
    }
    async function save() {
      if (s.saving) {
        toast('当前版本正在保存，请等待完成。');
        return null;
      }
      prepareFactorProtocol();
      const errors = [...validateStrategy(s.strategy), ...marketBindingErrors(s), ...financialBindingErrors(s)];
      if (errors.length) {
        setPageErrors(errors);
        render();
        document.querySelector('#sq-page-errors')?.focus();
        toast(errors[0], true);
        return null;
      }
      ui.pageErrors = []; // Keep the submitted version separate from inputs that change while the request is in flight.
      const draft = s.strategy,
        submitted = clone(draft),
        submittedSource = s.dataSource,
        expectedOwner = s.session?.workspace?.id,
        submittedBinding = activeBinding(s) ? clone(activeBinding(s)) : null,
        previousId = ui.activeId,
        id = ui.boundSource && ui.boundSource !== submittedSource ? null : ui.activeId,
        version = ui.activeVersion;
      s.saving = true;
      render();
      try {
        const universeScopeRef = await freezeScope(submitted.universe, submittedSource);
        if (expectedOwner !== s.session?.workspace?.id) throw Error('工作区身份已变化，此前研究尚未提交。');
        const response = await api(
          id
            ? '/statistical-quant/experiments/' + encodeURIComponent(id)
            : '/statistical-quant/experiments',
          {
            method: id ? 'PUT' : 'POST',
            body: JSON.stringify({
              strategy: submitted,
              ...(universeScopeRef ? { universeScopeRef } : {}),
              ...bindingFields(submittedSource, submittedBinding),
              ...(id ? { version } : {}),
            }),
          },
        );
        const item = response.experiment || response.item || response;
        const sameDraft = expectedOwner === s.session?.workspace?.id && s.strategy === draft && ui.activeId === previousId && s.dataSource === submittedSource && JSON.stringify(activeBinding(s) || null) === JSON.stringify(submittedBinding);
        if (sameDraft) {
          ui.activeId = item.id;
          ui.activeVersion = item.version;
          ui.boundSource = ['ready_market', 'ready_dataset'].includes(submittedSource) ? submittedSource : null;
          const unchanged =
            JSON.stringify(s.strategy) === JSON.stringify(submitted);
          if (unchanged) {
            s.strategy = normalizeStrategy(item.strategy || submitted);
            persistDraft(false);
          } else persistDraft();
          toast(
            unchanged
              ? '研究版本已保存。'
              : '提交时的版本已保存；之后的修改保留为未保存草稿。',
          );
        } else toast('此前提交的研究版本已保存；当前研究保持不变。');
        if (expectedOwner !== s.session?.workspace?.id) return item;
        ui.experiments = [
          item,
          ...ui.experiments.filter((x) => x.id !== item.id),
        ];
        return item;
      } finally {
        s.saving = false;
        render();
      }
    }
    function prepareFactorProtocol() {
      if (!isStatistical(s.strategy)) return;
      s.strategy.execution.enabled = false;
    }
    async function run() {
      if (s.submitting) return;
      prepareFactorProtocol();
      if (!admitStep('report')) return;
      const bindingErrors = [...marketBindingErrors(s, { run: true }), ...financialBindingErrors(s)];
      if (bindingErrors.length) { ui.runError = bindingErrors.join('；'); goto('report'); render(); toast(ui.runError, true); return; }
      if (s.dataSource === 'ready_dataset' && !s.datasetBinding) {
        toast('请重新选择已冻结数据集。', true);
        return;
      }
      const errors = validateStrategy(s.strategy, {
        includeData: true,
        dataSource: s.dataSource,
        dataset: s.dataset,
        session: s.session,
      });
      if (errors.length) {
        toast(errors[0], true);
        goto('report');
        return;
      }
      const submitted = clone(s.strategy),
        expectedOwner = s.session?.workspace?.id,
        dataSource = s.dataSource,
        dataset = s.dataset,
        submittedBinding = activeBinding(s) ? clone(activeBinding(s)) : null;
      ui.runError = '';
      s.submitting = true;
      render();
      try {
        let item = {
          id: ui.activeId,
          version: ui.activeVersion,
          strategy: submitted,
        };
        if (!item.id || s.dirty) {
          item = await save();
          if (!item) return;
        }
        if (expectedOwner !== s.session?.workspace?.id) throw Error('工作区身份已变化，此前研究未运行。');
        const response = await api(
          '/statistical-quant/experiments/' +
            encodeURIComponent(item.id) +
            '/run',
          {
            method: 'POST',
            body: JSON.stringify({
              version: item.version,
              dataSource,
              ...(dataSource === 'upload' ? { dataset } : {}),
              ...bindingFields(dataSource, submittedBinding),
            }),
          },
        );
        if (expectedOwner !== s.session?.workspace?.id) return;
        const job = response.job;
        s.runs = [
          { ...job, strategy: clone(item.strategy || submitted) },
          ...s.runs.filter((x) => x.id !== job.id),
        ];
        s.job = job;
        C.navigate('runs', job.id);
        toast('研究已提交，将先生成预测产物。');
      } catch (error) {
        ui.runError = error.message;
        goto('report');
        throw error;
      } finally {
        s.submitting = false;
        render();
      }
    }
    function mergePatch(target, patch) {
      for (const [key, value] of Object.entries(patch)) {
        if (['__proto__', 'constructor', 'prototype'].includes(key))
          throw Error('模块包含无效字段');
        if (value && typeof value === 'object' && !Array.isArray(value))
          mergePatch((target[key] ||= {}), value);
        else target[key] = clone(value);
      }
    }
    async function applyModule(item) {
      if (item.availability?.status !== 'ready')
        throw Error(item.availability?.reason || '模块暂不可运行。');
      const patch = item.configPatch;
      if (!patch) throw Error('模块未提供可核对的配置，无法自动应用。');
      if (item.patchSemantics === 'append_factors') {
        const incoming = patch.factors || [],
          ids = new Set(s.strategy.factors.map((x) => x.id));
        const unique = incoming.filter((x) => !ids.has(x.id));
        if (s.strategy.factors.length + unique.length > 32)
          throw Error('最多 32 个因子。');
        s.strategy.factors.push(...clone(unique));
      } else {
        if (patch.target?.kind === 'asset_price')
          delete s.strategy.target.basket;
        if (
          patch.target?.basket?.method &&
          s.strategy.target.basket?.method !== patch.target.basket.method
        )
          s.strategy.target.basket = {
            symbols: [],
            formationDays: 126,
            ...(patch.target.basket.method === 'pca_residual'
              ? { components: 2 }
              : patch.target.basket.method === 'fixed'
                ? { quantities: {} }
                : {}),
          };
        mergePatch(s.strategy, patch);
      }
      ui.selectedModules.push({ id: item.id, version: item.version });
      persistDraft();
      render();
      toast('已应用模块配置；需要明确填写的成员和数据仍会验证。');
    }
    function currentStepErrors() {
      const errors = stepErrors(s.strategy, step(), stepOptions());
      for (const control of document.querySelectorAll('.sq-research-body input[data-sq-config],.sq-research-body select[data-sq-config],.sq-research-body select[data-sq-pair-object]'))
        if (!control.checkValidity())
          errors.push(`${control.closest('label')?.querySelector('span')?.textContent || '参数'}需要完整填写并符合范围。`);
      return [...new Set(errors)];
    }
    async function handle(element) {
      if (element.dataset.sq?.startsWith('market-')) return market.handle(element);
      if (await reports.handle(element)) return;
      if (await catalog.handle(element)) return;
      if (industryBrowser.handle(element)) return;
      const action = element.dataset.sq,
        id = element.dataset.id;
      if (action === 'select-mode') {
        if (ui.firstDraft && !ui.activeId && !s.strategy.universe.symbols.length && !s.strategy.factors.length && id === 'easy') {
          s.strategy.preprocess.automatic = { schema: 'auto-factor-preprocess/1' };
          persistDraft();
        }
        ui.firstDraft = false;
        location.hash = `quant/${id}/universe`;
      }
      if (action === 'new') {
        C.legacy.flow.reset();
        s.strategy = defaultStrategy({ automatic: !isStudio() });
        ui.firstDraft = false;
        s.strategyId = null;
        s.strategyVersion = null;
        ui.activeId = null;
        ui.activeVersion = null;
        ui.universeScope = null;
        ui.boundSource = null;
        ui.runError = '';
        s.dataSource = 'tushare';
        s.marketDatasetBinding = null;
        s.datasetBinding = null;
        persistDraft();
        location.hash = route('universe');
        render();
      }
      if (action === 'restore-legacy-draft') {
        const saved = JSON.parse(
          localStorage.getItem('atlas-quant-draft-v1') || 'null',
        );
        if (!saved?.strategy) throw Error('这个浏览器没有旧版草稿。');
        s.strategy = C.normalizeStrategy(saved.strategy);
        s.strategyId = null;
        ui.activeId = null;
        ui.activeVersion = null;
        location.hash = 'research/universe';
        render();
      }
      if (action === 'continue') {
        location.hash = legacy() ? 'quant/history' : route('universe');
      }
      if (action === 'step') {
        if (element.dataset.direction === 'next') {
          const errors = currentStepErrors();
          if (errors.length) { showStepErrors(step(), errors); return; }
        }
        if (!admitStep(id)) return;
        ui.pageErrors = [];
        goto(id);
      }
      if (action === 'date-split') {
        const value = suggestedTestStart();
        if (!value) throw Error('先填写有效的研究日期。');
        s.strategy.validation.testStart = value;
        persistDraft();
        render();
      }
      if (action === 'save') await save();
      if (action === 'run') await run();
      if (action === 'export')
        C.download('atlas-statistical-quant-research.json', s.strategy);
      if (action === 'workspace-retry') await initialize();
      if (action === 'recipe-retry') await loadRecipes();
      if (action === 'recipe-page') {
        ui.recipePage = Number(element.dataset.page);
        await loadRecipes();
      }
      if (action === 'compare-page') {
        ui.comparePage = Number(element.dataset.page);
        await loadCompareChoices();
      }
      if (action === 'compare-detail-retry') await loadComparisonDetail(id);
      if (action === 'comparison-history-retry') await loadComparisonHistory();
      if (action === 'comparison-history-page') {
        ui.comparisonHistoryPage = Number(element.dataset.page);
        await loadComparisonHistory();
        render();
      }
      if (action === 'compare-refresh') await loadCompareChoices();
      if (action === 'compare-create') await createComparison();
      if (action === 'feature-tab') {
        ui.featureTab = id;
        render();
        if (id === 'fields') await C.legacy.loadFields();
        if (id === 'modules') await catalog.load('state');
      }
      if (action === 'remove-factor') {
        s.strategy.factors = s.strategy.factors.filter((x) => x.id !== id);
        s.strategy.portfolio.factorExposureLimits = (
          s.strategy.portfolio.factorExposureLimits || []
        ).filter((x) => x.factorId !== id);
        persistDraft();
        render();
      }
      if (action === 'target-kind') {
        s.strategy.target.kind = id;
        if (id === 'frozen_basket' && !s.strategy.target.basket)
          s.strategy.target.basket = {
            method: 'pair_ols',
            symbols: [],
            formationDays: 126,
          };
        if (id === 'asset_price') delete s.strategy.target.basket;
        persistDraft();
        render();
      }
      if (action === 'repair-asset-target') {
        if (isStudio() || boundDataset() || !canUseIndividualTarget(s.strategy)) return;
        s.strategy.target.kind = 'asset_price';
        delete s.strategy.target.basket;
        ui.pageErrors = [];
        ui.pageErrorInputs = null;
        persistDraft();
        render();
        toast('已改为逐只研究当前股票；保存后才会生成新版本。');
      }
      if (action === 'family') {
        const admission = mechanismStatus(id);
        if (!admission.selectable) { toast(admission.message, true); return; }
        if (!isStudio() && s.strategy.model.family === 'pair_reversion' && id !== 'pair_reversion') {
          s.strategy.target.kind = 'asset_price';
          delete s.strategy.target.basket;
        }
        s.strategy.model.family = id;
        if (!isStudio()) s.strategy.model.estimator = 'auto';
        if (id === 'pair_reversion') s.strategy.target = pairTarget(s.strategy, { automatic: !isStudio() });
        ui.pageErrors = [];
        persistDraft();
        render();
      }
      if (action === 'experiment-page') {
        ui.experimentPage = Number(element.dataset.page);
        await refreshExperiments();
      }
      if (action === 'refresh-experiments') await refreshExperiments();
      if (action === 'experiment-load') await loadExperiment(id, true);
      if (action === 'experiment-detail')
        location.hash = 'quant/experiment/' + encodeURIComponent(id);
      if (action === 'experiment-detail-retry') await loadExperiment(id);
      if (action === 'experiment-copy') {
        const response = await api(
          '/statistical-quant/experiments/' + encodeURIComponent(id) + '/copy',
          { method: 'POST', body: '{}' },
        );
        const item = response.experiment || response.item || response;
        await refreshExperiments();
        await loadExperiment(item.id, true);
      }
      if (action === 'recipe') {
        const recipe = ui.recipes.find((x) => x.id === id);
        if (!recipe) throw Error('配方已失效，请刷新。');
        if (recipe.strategy) {
          const universe = clone(s.strategy.universe);
          s.strategy = normalizeStrategy(recipe.strategy);
          s.strategy.universe = universe;
          persistDraft();
          goto('target');
        } else if (recipe.configPatch) {
          s.strategy.target = {
            horizonSessions: 5,
            ...clone(recipe.configPatch.target),
          };
          if (s.strategy.target.kind === 'frozen_basket')
            s.strategy.target.basket = {
              symbols: [],
              formationDays: 126,
              ...s.strategy.target.basket,
            };
          mergePatch(s.strategy, recipe.configPatch);
          persistDraft();
          goto('target');
        } else throw Error('这个配方尚未返回可执行配置。');
        render();
      }
    }
    function commitInput(element) {
      const path = element.dataset.sqConfig;
      if (!path) return;
      let value =
        element.type === 'checkbox'
          ? element.checked
          : element.type === 'number'
            ? element.value === ''
              ? null
              : Number(element.value)
            : element.type === 'date' ? element.value.replaceAll('-', '') : element.value;
      setPath(s.strategy, path, value);
      persistDraft();
    }
    let recipeTimer;
    function onInput(element) {
      if (element.id === 'sq-recipe-search') {
        ui.recipeQuery = element.value;
        ui.recipePage = 1;
        clearTimeout(recipeTimer);
        recipeTimer = setTimeout(() => loadRecipes(), 250);
      }
      industryBrowser.onInput(element);
      reports.onInput(element);
      catalog.onInput(element);
      if (element.id === 'sq-research-name') {
        s.strategy.name = element.value;
        persistDraft();
      }
      commitInput(element);
      if (element.dataset.sqQuantity) {
        s.strategy.target.basket.quantities ||= {};
        s.strategy.target.basket.quantities[element.dataset.sqQuantity] =
          element.value === '' ? null : Number(element.value);
        persistDraft();
      }
    }
    function onChange(element) {
      if (industryBrowser.onChange(element)) return;
      if (market.onChange(element)) return;
      if (element.id === 'sq-compare-kind') {
        ui.compareKind = element.value;
        ui.compareLoaded = false;
        ui.compareIds = [];
        ui.compare = null;
        ui.comparePage = 1;
        loadCompareChoices().catch((err) => toast(err.message, true));
      }
      if (element.dataset.sqCompare) {
        const id = element.dataset.sqCompare;
        ui.compareIds = element.checked
          ? [...new Set([...ui.compareIds, id])]
          : ui.compareIds.filter((x) => x !== id);
        render();
      }
      reports.onChange(element);
      catalog.onChange(element);
      commitInput(element);
      if (element.id === 'sq-step-picker' && admitStep(element.value)) goto(element.value);
      if (element.dataset.sqPairObject !== undefined) {
        if (!isPairTarget(s.strategy)) s.strategy.target = pairTarget(s.strategy);
        const previous = s.strategy.target.basket.symbols;
        s.strategy.target.basket.symbols = [0, 1].map(index => index === Number(element.dataset.sqPairObject) ? element.value : previous[index] || '');
        ui.pageErrors = [];
        persistDraft();
        render();
      }
      if (element.dataset.sqFactorRole) {
        s.strategy.factors.find(
          (x) => x.id === element.dataset.sqFactorRole,
        ).role = element.value;
        persistDraft();
      }
      if (element.dataset.sqBasketSymbol) {
        const b = s.strategy.target.basket,
          id = element.dataset.sqBasketSymbol;
        b.symbols = element.checked
          ? [...new Set([...b.symbols, id])]
          : b.symbols.filter((x) => x !== id);
        if (b.quantities && !element.checked) delete b.quantities[id];
        persistDraft();
      }
      if (element.dataset.sqConfig === 'target.basket.method') {
        const b = s.strategy.target.basket;
        if (b.method === 'pca_residual') b.components ??= 2;
        else delete b.components;
        if (b.method === 'fixed') b.quantities ||= {};
        else delete b.quantities;
        persistDraft();
      }
      if (
        element.dataset.sqConfig ||
        element.dataset.sqBasketSymbol ||
        element.dataset.sqFactorRole
      )
        setTimeout(render, 0);
    }
    async function routeChanged() {
      if (s.view === 'quant' && STEPS.some(x => x.id === s.quantStep) && isStatistical(s.strategy) && !admitStep(s.quantStep)) return;
      market.routeChanged();
      ui.pageErrors = [];
      if (s.view === 'quant' && STEPS.some(x => x.id === s.quantStep) && isStatistical(s.strategy)) {
        const before = JSON.stringify([s.strategy.execution.enabled, s.strategy.model.estimator]);
        prepareFactorProtocol();
        if (JSON.stringify([s.strategy.execution.enabled, s.strategy.model.estimator]) !== before) persistDraft();
      }
      if (s.view === 'quant' && s.quantStep === 'datasets') {
        datasets.routeChanged();
        return;
      }
      datasets.dispose();
      if (s.view === 'quant' && s.quantStep === 'financial') {
        await financial.routeChanged();
        return;
      }
      financial.dispose();
      if (s.view === 'dashboard') {
        const summary = await api('/statistical-quant/summary');
        ui.summary = summary.counts || summary;
        render();
        return;
      }
      if (s.view === 'quant' && s.quantStep === 'researches') {
        await refreshExperiments();
        return;
      }
      if (
        s.quantStep === 'compare' &&
        s.quantEntityId &&
        ui.compare?.id !== s.quantEntityId
      )
        await loadComparisonDetail(s.quantEntityId);
      if (s.quantStep === 'compare' && !ui.compareLoaded && !ui.compareLoading)
        await loadCompareChoices();
      const stageMap = {
        state: 'state',
        model: 'target',
        risk: 'risk',
        execution: 'execution',
      };
      if (
        s.quantStep === 'experiment' &&
        s.quantEntityId &&
        (!ui.viewedExperiment ||
          (
            ui.viewedExperiment.experiment ||
            ui.viewedExperiment.item ||
            ui.viewedExperiment
          ).id !== s.quantEntityId)
      ) {
        await loadExperiment(s.quantEntityId);
        return;
      }
      const selected = stageMap[step()];
      if (selected && catalog.state.stage !== selected) {
        catalog.state.page = 1;
        catalog.state.q = '';
        await catalog.load(selected);
      }
    }
    function bind() {
      C.legacy.bind();
      document.querySelectorAll('[data-sq-drag]').forEach((element) =>
        element.addEventListener('pointerdown', (event) => {
          if (
            element.draggable ||
            event.button !== 0 ||
            event.pointerType === 'touch' ||
            event.target.closest('button,a,input,select,textarea')
          )
            return;
          const initial = { x: event.clientX, y: event.clientY };
          let ghost, drop;
          const move = (ev) => {
            if (
              !ghost &&
              Math.hypot(ev.clientX - initial.x, ev.clientY - initial.y) > 8
            ) {
              ghost = document.createElement('div');
              ghost.className = 'sq-drag-ghost';
              ghost.textContent =
                element.querySelector('h3,strong')?.textContent || '研究模块';
              document.body.append(ghost);
              element.setPointerCapture?.(event.pointerId);
            }
            if (!ghost) return;
            ev.preventDefault();
            ghost.style.left = ev.clientX + 12 + 'px';
            ghost.style.top = ev.clientY + 12 + 'px';
            document
              .querySelectorAll('.sq-drop-active')
              .forEach((x) => x.classList.remove('sq-drop-active'));
            drop = document
              .elementFromPoint(ev.clientX, ev.clientY)
              ?.closest('[data-sq-drop]');
            drop?.classList.add('sq-drop-active');
          };
          const done = (ev) => {
            element.removeEventListener('pointermove', move);
            element.removeEventListener('pointerup', done);
            element.removeEventListener('pointercancel', done);
            ghost?.remove();
            document
              .querySelectorAll('.sq-drop-active')
              .forEach((x) => x.classList.remove('sq-drop-active'));
            if (!drop || ev.type === 'pointercancel') return;
            const payload = element.dataset.sqDrag;
            if (payload.startsWith('module:')) {
              const module = catalog.state.items.find(
                (x) => x.id === payload.slice(7),
              );
              if (module && drop.dataset.sqDrop === module.stage)
                applyModule(module).catch((err) => toast(err.message, true));
            }
          };
          element.addEventListener('pointermove', move, { passive: false });
          element.addEventListener('pointerup', done);
          element.addEventListener('pointercancel', done);
        }),
      );
    }
    let nativeModule = null;
    document.addEventListener('dragstart', (event) => {
      const el = event.target.closest('[data-sq-drag][draggable="true"]');
      if (!el) return;
      nativeModule = el.dataset.sqDrag.slice(7);
      event.dataTransfer?.setData('application/x-atlas-module', nativeModule);
      if (event.dataTransfer) event.dataTransfer.effectAllowed = 'copy';
      el.classList.add('sq-native-dragging');
    });
    document.addEventListener('dragover', (event) => {
      const drop = event.target.closest('[data-sq-drop]'),
        module = catalog.state.items.find((x) => x.id === nativeModule);
      if (drop && module && drop.dataset.sqDrop === module.stage) {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
        drop.classList.add('sq-drop-active');
      }
    });
    document.addEventListener('drop', (event) => {
      const drop = event.target.closest('[data-sq-drop]'),
        id =
          event.dataTransfer?.getData('application/x-atlas-module') ||
          nativeModule,
        module = catalog.state.items.find((x) => x.id === id);
      nativeModule = null;
      if (!drop || !module || drop.dataset.sqDrop !== module.stage) return;
      event.preventDefault();
      applyModule(module).catch((err) => toast(err.message, true));
    });
    document.addEventListener('dragend', () => {
      nativeModule = null;
      document
        .querySelectorAll('.sq-drop-active,.sq-native-dragging')
        .forEach((x) =>
          x.classList.remove('sq-drop-active', 'sq-native-dragging'),
        );
    });
    document.addEventListener('click', (event) => {
      const button = event.target.closest('[data-sq]');
      if (!button || button.disabled) return;
      event.preventDefault();
      Promise.resolve(handle(button)).catch((err) => toast(err.message, true));
    });
    document.addEventListener('input', (event) => onInput(event.target));
    document.addEventListener('change', (event) => {
      const id = event.target.dataset.sqDatasetState;
      if (
        id &&
        boundDataset() &&
        s.datasetBinding.selectedStateIds.includes(id)
      ) {
        const others = s.strategy.factors.filter((f) => f.expression !== id);
        s.strategy.factors = event.target.checked
          ? [
              ...others,
              {
                id,
                expression: id,
                direction: 1,
                role: 'predictor',
              },
            ]
          : others;
        persistDraft();
        render();
        return;
      }
      onChange(event.target);
    });
    document.addEventListener('click', (event) => {
      const summary = event.target.closest('#sq-summary > summary');
      if (summary) {
        ui.summaryOpen = !summary.parentElement.open;
        ui.summaryUserChoice = ui.summaryOpen;
      }
    });
    summaryMedia?.addEventListener?.('change', (event) => {
      if (ui.summaryUserChoice === null) {
        ui.summaryOpen = event.matches;
        render();
      }
    });
    return {
      render: renderWorkspace,
      bind,
      initialize,
      routeChanged,
      save,
      run,
      reportBody: reports.render,
      ui,
      catalog,
      reports,
      financial,
      datasets,
      market,
      applyModule,
      validate: () =>
        validateStrategy(s.strategy, {
          includeData: true,
          dataSource: s.dataSource,
          dataset: s.dataset,
          session: s.session,
        }),
    };
  },
};
