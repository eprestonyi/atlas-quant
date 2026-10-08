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
import { createForms, getPath, setPath } from './forms.js';
import { createModuleCatalog } from './catalog.js';
import { createForecastReports } from './reports.js';
import { createFinancialWorkspace } from './financial/workspace.js';

window.AtlasQuantV4 = {
  describeFactor,
  defaultStrategy,
  normalizeStrategy,
  validateStrategy,
  isStatistical,
  create(C) {
    const { state: s, esc: e, icon: i, api, render, toast, persistDraft, clone } = C;
    const F = createForms(C),
      { button, input, select, toggle, panel, note, empty, advanced } = F;
    const summaryMedia =
      typeof matchMedia === 'function' ? matchMedia('(min-width: 1250px)') : null;
    const ui = {
      ready: false,
      pageErrors: [],
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
    try {
      const saved = JSON.parse(localStorage.getItem('atlas-quant-statistical-draft-v2') || 'null');
      ui.activeId = saved?.experimentId || null;
      ui.activeVersion = saved?.experimentVersion || null;
    } catch {}
    const reports = createForecastReports(C, F, { onExecution: runExecution });
    const isStudio = () => s.quantMode === 'studio';
    const step = () =>
      s.view === 'runs'
        ? 'report'
        : STEPS.some((x) => x.id === s.quantStep)
          ? s.quantStep
          : 'universe';
    const route = (id = step(), studio = isStudio()) => 'quant/' + (studio ? 'studio/' : '') + id;
    const goto = (id) => {
      location.hash = route(id);
    };
    const targetLabel = () =>
      s.strategy.target?.kind === 'frozen_basket' ? '冻结数量篮子' : '单资产价格';
    const legacy = () => !isStatistical(s.strategy);
    const sourceLabel = () =>
      ({
        tushare: 'Tushare 实际数据',
        upload: '当前导入数据',
        demo: '合成教学数据',
      })[s.dataSource] || '待选择';

    function sidebar() {
      const current = step();
      return `<aside class="sq-sidebar"><a href="#dashboard" class="sq-brand"><span class="atlas-mark">A</span><span>atlas <b>quant</b><small>OPEN QUANTITATIVE RESEARCH</small></span></a><div class="sq-workspace-label">统计量化交易<span>STATISTICAL QUANT</span></div><nav aria-label="工作区"><a href="#dashboard" class="sq-nav ${s.view === 'dashboard' ? 'active' : ''}">${i('grid')}研究概览</a><a href="#quant/researches" class="sq-nav ${s.quantStep === 'researches' ? 'active' : ''}">${i('save')}我的研究<span>${ui.experimentsLoaded ? ui.experimentTotal || '' : ''}</span></a></nav><div class="sq-nav-caption">研究流程 <span>01 — 08</span></div><nav aria-label="统计量化研究步骤">${STEPS.map((x, n) => `<a class="sq-step ${s.view === 'quant' && s.quantStep === x.id ? 'active' : ''}" href="#${route(x.id)}" ${s.view === 'quant' && s.quantStep === x.id ? 'aria-current="step"' : ''}><b>${String(n + 1).padStart(2, '0')}</b><span>${x.name}</span>${i(x.icon)}</a>`).join('')}</nav><div class="sq-nav-caption">研究工具</div><nav><a href="#quant/studio/financial" class="sq-nav ${s.quantStep === 'financial' ? 'active' : ''}">${i('database')}财务输入与状态</a><a href="#quant/studio/code" class="sq-nav ${s.quantStep === 'code' ? 'active' : ''}">${i('code')}代码与 AI 审阅</a><a href="#quant/community" class="sq-nav ${s.quantStep === 'community' ? 'active' : ''}">${i('users')}因子社区</a><a href="#quant/recipes" class="sq-nav">${i('layers')}模块配方目录</a><a href="#quant/compare" class="sq-nav">${i('chart')}研究比较</a><a href="#quant/history" class="sq-nav ${s.quantStep === 'history' ? 'active' : ''}">${i('clock')}历史版本研究</a></nav><div class="sq-sidebar-bottom"><span><i class="dot ${s.session?.runner?.online ? 'online' : ''}"></i>${s.session?.runner?.online ? '计算节点在线' : '计算节点状态待确认'}</span><a href="https://github.com/eprestonyi/atlas-quant" target="_blank" rel="noopener noreferrer">${i('code')} GitHub 开源代码</a><a href="/cn/terminal">${i('external')} Atlas Terminal</a></div></aside>`;
    }
    function topbar() {
      return `<header class="sq-topbar"><a href="#dashboard" class="sq-mobile-brand">atlas <b>quant</b></a><div class="sq-breadcrumb">STATISTICAL QUANT <span>/</span> <strong>${e(s.view === 'dashboard' ? '研究概览' : STEPS.find((x) => x.id === s.quantStep)?.name || { researches: '我的研究', code: '代码与 AI', financial: '财务输入与状态', history: '历史版本', community: '因子社区', compare: '研究比较' }[s.quantStep] || '报告')}</strong></div><div class="sq-topbar-actions"><span class="sq-version">FORECAST FIRST</span><a class="sq-source-link" href="https://github.com/eprestonyi/atlas-quant" target="_blank" rel="noopener noreferrer" aria-label="查看 Atlas Quant GitHub 开源代码">${i('code')}<span>源码</span></a><a class="sq-window-link" href="#quant/universe" target="_blank" rel="noopener" title="在独立窗口中打开统计量化工作区" aria-label="独立打开统计量化工作区">${i('external')}</a><a class="sq-mode-switch" href="#${route(step(), !isStudio())}">${i(isStudio() ? 'workflow' : 'code')}${isStudio() ? '引导模式' : 'Quant Studio'}</a></div></header>`;
    }
    function heading(kicker, title, description, actions = '') {
      return `<div class="sq-page-heading"><div><span class="sq-kicker">${e(kicker)}</span><h1>${e(title)}</h1><p>${e(description)}</p></div>${actions ? `<div class="sq-actions">${actions}</div>` : ''}</div>`;
    }
    function researchName() {
      return `<div class="sq-research-name"><label><span>当前研究</span><input id="sq-research-name" maxlength="80" value="${e(s.strategy.name)}" aria-label="研究名称"></label><span>${ui.activeId ? `版本 ${ui.activeVersion}` : '未保存草稿'} · ${s.dirty ? '有更改' : '已保存'}</span></div>`;
    }
    function summary() {
      if (legacy()) return '';
      const st = s.strategy;
      return `<aside class="sq-summary"><details ${ui.summaryOpen ? 'open' : ''} id="sq-summary"><summary>当前研究协议 <span>${st.universe.symbols.length} 标的 · ${st.factors.length} 因子</span>${i('sliders')}</summary><div><span class="sq-kicker">CONFIGURATION SNAPSHOT</span><h3>${e(st.name)}</h3><dl><dt>研究范围</dt><dd>${st.universe.symbols.length} 个明确成员</dd><dt>目标</dt><dd>${targetLabel()}</dd><dt>预测期限</dt><dd>${e(st.target.horizonSessions)} 个交易日</dd><dt>模型族</dt><dd>${e(FAMILIES[st.model.family]?.name || st.model.family)}</dd><dt>估计器</dt><dd>${e(ESTIMATORS[st.model.estimator] || st.model.estimator)}</dd><dt>观察 / 重拟合</dt><dd>${st.research.observationDays} / ${st.model.refitDays} 日</dd><dt>研究类型</dt><dd>${st.execution.enabled ? '预测 + 独立执行' : '仅预测研究'}</dd><dt>数据</dt><dd>${e(sourceLabel())}</dd></dl><div class="sq-summary-equation">V̂ = F<sub>h</sub>(X<sub>t</sub>)<br><small>e = 当前状态 − 预期未来状态</small></div>${button('save', '保存研究', { icon: 'save', disabled: s.saving })}<p>模型估计与交易结果分别检验。配置版本与数据来源随运行固定。</p></div></details></aside>`;
    }
    function frame(body) {
      return `<div class="sq-shell">${sidebar()}<main class="sq-main" id="main-content" tabindex="-1">${topbar()}${s.quantStep === 'financial' ? '<div class="sq-mobile-step"><a href="#quant/studio/financial">财务输入列表</a><a href="#quant/studio/state">返回研究</a></div>' : `<div class="sq-mobile-step"><label for="sq-step-picker">研究步骤</label><select id="sq-step-picker">${STEPS.map((x, n) => `<option value="${x.id}" ${step() === x.id ? 'selected' : ''}>${n + 1}. ${x.name}</option>`).join('')}</select><a href="#quant/researches">研究列表</a></div>`}<div class="sq-content">${s.error ? note(s.error, 'error') + '<button class="sq-button small" data-action="refresh">重新连接服务</button>' : ''}${ui.errors.length ? note(ui.errors.join('；'), 'warning') + button('workspace-retry', '重新读取工作区', { small: true }) : ''}${body}</div><footer class="sq-footer"><span>ATLAS QUANT · OPEN RESEARCH</span><span>预测有据 · 目标固定 · 执行可核对</span></footer></main></div>`;
    }
    function home() {
      return `${heading('AN INDEPENDENT RESEARCH WORKSPACE', '先预测，再检验，再交易。', '用明确的模型估计未来价格或价差。保留每一次预测，再研究它是否值得执行。', button('new', '新建统计研究', { icon: 'plus', primary: true }))}<div class="sq-summary-counts">${[
        ['experiments', '已保存研究'],
        ['forecastArtifacts', '预测产物'],
        ['completedExecutions', '已完成独立执行'],
        ['comparisons', '已保存比较'],
      ]
        .map(
          ([key, label]) =>
            `<article><span>${label}</span><strong>${ui.summary ? C.fmt(ui.summary[key], 0) : '—'}</strong><small>当前私有工作区 · 实际记录</small></article>`
        )
        .join(
          ''
        )}</div><section class="sq-home-hero"><div><span class="sq-status ready">STATISTICAL QUANTITATIVE TRADING</span><h2>一个有定义的状态。<br>一个可以检验的未来。</h2><p>股票池与因子定义信息；F 模型输出未来状态与误差；对冲、仓位和成本负责独立执行。</p><div class="sq-actions">${button('continue', '继续当前研究', { icon: 'arrow', primary: true })}<a class="sq-button" href="#quant/researches">打开研究列表</a></div></div><div class="sq-forecast-visual" aria-label="研究公式示意，没有实际预测数据"><div><span>观察状态</span><strong>P<sub>t</sub></strong><small>当前已知</small></div><b>${i('arrow')}</b><div><span>F 模型 · 期限 h</span><strong>V̂<sub>t,h</sub></strong><small>未来状态的估计</small></div><section><span>可检验的差距</span><strong>e = P<sub>t</sub> − V̂<sub>t,h</sub></strong><small>真实未来值到达后，单独评价预测误差。</small></section></div></section><div class="sq-section-heading"><div><h2>八个独立步骤，一份研究协议</h2><p>可以逐步完成，也可以进入 Studio 展开完整配置。</p></div></div><div class="sq-overview-steps">${STEPS.map((x, n) => `<a href="#${route(x.id, false)}"><b>${String(n + 1).padStart(2, '0')}</b>${i(x.icon)}<h3>${x.name}</h3><p>${x.hint}</p></a>`).join('')}</div><div class="sq-section-heading"><div><h2>研究配方</h2><p>版本化模块组合；不是已经验证有效的策略。</p></div><a class="sq-button small" href="#quant/recipes">搜索全部配方</a></div>${recipesView()}<div class="sq-future-workspaces"><h2>独立工作区边界</h2><div>${[
        ['统计量化交易', '本轮研究工作区', 'active'],
        ['做市与微观结构', '规划中 · 尚不运行', ''],
        ['波动率与衍生品', '规划中 · 尚不运行', ''],
        ['复制关系与结构套利', '规划中 · 尚不运行', ''],
      ]
        .map(
          ([name, status, active]) =>
            `<article class="${active}"><span>${i(active ? 'check' : 'layers')}</span><strong>${name}</strong><small>${status}</small></article>`
        )
        .join('')}</div></div>`;
    }
    function recipesView() {
      if (ui.recipeError)
        return (
          note(ui.recipeError, 'error') + button('recipe-retry', '重新加载配方', { small: true })
        );
      if (ui.recipeLoading || !ui.recipesLoaded)
        return '<div class="sq-loading" role="status">正在读取配方目录…</div>';
      return `<div class="sq-recipe-grid">${
        ui.recipes
          .slice(0, s.quantStep === 'recipes' ? 12 : 6)
          .map(
            (item) =>
              `<article class="sq-recipe-card"><span class="sq-kicker">RESEARCH RECIPE · v${e(item.version || 1)}</span><h3>${e(item.name)}</h3><p>${e(item.description || item.mechanism || '组合公开模块来检验一个研究假设。')}</p><small>${e(item.availability?.reason || '有效性由实际运行检验')}</small>${button('recipe', '查看并应用配方', { icon: 'fork', id: item.id, disabled: item.availability?.status && item.availability.status !== 'ready' })}</article>`
          )
          .join('') || empty('配方目录尚未就绪', '可以从明确的目标与模型配置创建研究。')
      }</div>`;
    }
    function page() {
      const st = step(),
        index = STEPS.findIndex((x) => x.id === st),
        meta = STEPS[index];
      if (legacy())
        return `${heading('HISTORICAL CONFIGURATION', '这是历史版本研究', '原始配置和报告保持其当时的语义。新研究采用独立预测协议。', button('new', '创建新的预测研究', { primary: true, icon: 'plus' }))}<div class="sq-legacy">${C.legacy.renderScreen('code')}</div>`;
      return `${heading(`${isStudio() ? 'QUANT STUDIO' : 'GUIDED RESEARCH'} / ${String(index + 1).padStart(2, '0')}`, meta.name, meta.hint, button('save', '保存', { icon: 'save', disabled: s.saving }))}${researchName()}${ui.pageErrors.length ? `<div id="sq-page-errors" tabindex="-1" role="alert">${note(ui.pageErrors.join('；'), 'warning')}</div>` : ''}<div class="sq-progress"><span style="width:${((index + 1) / STEPS.length) * 100}%"></span></div><div class="sq-research-layout"><div class="sq-research-body">${{ universe: universePage, state: statePage, target: targetPage, model: modelPage, validation: validationPage, risk: riskPage, execution: executionPage, report: reviewPage }[st]()}<div class="sq-page-navigation">${index ? button('step', `上一步 · ${STEPS[index - 1].short}`, { id: STEPS[index - 1].id }) : '<a class="sq-button" href="#dashboard">研究概览</a>'}<span>STEP ${index + 1} OF ${STEPS.length}</span>${index < STEPS.length - 1 ? button('step', `下一步 · ${STEPS[index + 1].short}`, { id: STEPS[index + 1].id, direction: 'next', primary: true, icon: 'arrow' }) : button('run', s.submitting ? '正在提交' : '运行本次研究', { primary: true, icon: 'play', disabled: s.submitting })}</div></div>${summary()}</div>`;
    }
    function universePage() {
      return `${note('从完整集合逐层筛选。本次最多 50 个研究成员；选择子集须明确确认。')}<div class="sq-legacy sq-universe">${C.legacy.flow.universePage()}</div>${isStudio() ? advanced('跨数据库时点映射', C.legacy.mappingEditor(), false) : ''}`;
    }
    function statePage() {
      const factors = s.strategy.factors;
      return `${panel('把信息定义为可审计的状态', `<div class="sq-equation-strip"><span>点时数据</span>${i('arrow')}<span>因果变换</span>${i('arrow')}<span>X<sub>t</sub> 状态与因子</span>${i('arrow')}<span>F<sub>h</sub> 的输入</span></div><p>预测因子是 F 模型的数值输入；正负方向仅改变特征编码，不决定交易方向或仓位。对冲暴露用于构造 PCA 篮子；事件因子必须具有实际可用时间。</p>`, { kicker: 'INFORMATION SET' })}<div class="sq-selected-factors" data-sq-drop="state" data-v2-drop="recipe"><div class="sq-section-heading"><h2>当前状态输入 <span>${factors.length} / 32</span></h2><a class="sq-button small" href="#quant/studio/state">展开 Studio</a></div>${factors.map((f) => `<article class="sq-selected-factor"><span class="sq-drag-grip">⠿</span><div><strong>${e(C.findFactor(f.id)?.name || f.id)}</strong><code>${e(f.expression)}</code></div><label><span class="sr-only">${e(C.findFactor(f.id)?.name || f.id)} 的因子角色</span><select data-sq-factor-role="${e(f.id)}"><option value="predictor" ${(f.role || 'predictor') === 'predictor' ? 'selected' : ''}>预测因子</option><option value="hedge" ${f.role === 'hedge' ? 'selected' : ''}>PCA 对冲暴露</option><option value="event" ${f.role === 'event' ? 'selected' : ''}>事件输入 · 需 PIT</option></select></label>${button('remove-factor', '移除', { icon: 'close', small: true, id: f.id, ariaLabel: '移除 ' + (C.findFactor(f.id)?.name || f.id) })}</article>`).join('') || empty('尚未添加额外因子', '模型仍可使用其明确声明的内置状态。拖入模块或点击目录中的“加入”。')}<div class="sq-drop-caption">${i('plus')}拖入状态模块，或用键盘选择“加入”</div></div><div class="sq-tabs" role="group" aria-label="状态输入编辑方式">${[['catalog', '因子目录'], ['modules', '状态模块'], ['builder', '构建因子'], ...(isStudio() ? [['fields', '数据库字段']] : [])].map(([id, label]) => button('feature-tab', label, { id, primary: ui.featureTab === id, pressed: ui.featureTab === id, small: true })).join('')}</div>${ui.featureTab === 'modules' ? catalog.view('state') : `<div class="sq-legacy">${ui.featureTab === 'builder' ? C.legacy.builder() : ui.featureTab === 'fields' ? C.legacy.fieldBrowser() : C.legacy.catalogBrowser()}</div>`}${advanced('预处理与输入冗余', `<div class="sq-form-grid">${toggle('训练期截尾', 'preprocess.winsorize', '阈值只由拟合数据确定。')}${toggle('训练期标准化', 'preprocess.standardize', '验证与预测复用训练参数。')}${select('冗余处理', 'preprocess.decorrelation', { none: '保留全部输入', drop_correlated: '剔除高度相关输入' })}${input('绝对相关阈值', 'preprocess.correlationThreshold', { min: 0.5, max: 1, step: 0.01 })}</div>`, isStudio())}`;
    }
    function targetPage() {
      const t = s.strategy.target,
        b = t.basket || {};
      return `${panel(
        '预测什么？',
        `<div class="sq-choice-grid">${[
          ['asset_price', '单资产价格', '每个选定标的独立预测，目标数量为 1。'],
          ['frozen_basket', '冻结数量篮子', '每次预测保存固定数量，当前值和未来标签使用同一篮子。'],
        ]
          .map(
            ([id, name, description]) =>
              `<button class="sq-choice ${t.kind === id ? 'selected' : ''}" data-sq="target-kind" data-id="${id}" aria-pressed="${t.kind === id}">${i(id === 'asset_price' ? 'chart' : 'layers')}<strong>${name}</strong><p>${description}</p><span>${t.kind === id ? '已选择' : '选择目标'}</span></button>`
          )
          .join(
            ''
          )}</div><div class="sq-form-grid">${input('预测持有期限 h', 'target.horizonSessions', { min: 1, max: 60, unit: '交易日', help: '从下一官方交易日开盘，到其后 h 个交易日开盘。' })}${input('观察间隔', 'research.observationDays', { min: 1, max: 60, unit: '交易日', help: '与预测期限、重拟合及调仓间隔分别定义。' })}</div>`,
        { kicker: 'TARGET DEFINITION' }
      )}${t.kind === 'frozen_basket' ? basketDefinition() : ''}${panel('参考、入场与目标时点', `<div class="sq-timeline"><div><span>t · 收盘</span><strong>当前状态 P</strong><small>信息截止；尚未假设成交</small></div><div><span>t + 1 · 开盘</span><strong>预期入场 V̂<sub>entry</sub></strong><small>第一个允许入场的时点</small></div><div><span>t + 1 + h · 开盘</span><strong>预期未来 V̂<sub>future</sub></strong><small>成熟后记录实际状态与误差</small></div></div><p class="sq-subtle">可交易的剩余预期变化为 V̂<sub>future</sub> − V̂<sub>entry</sub>。当前收盘参考价并不等于可以获得的入场价。</p>`)}${isStudio() ? catalog.view('target') : advanced('浏览版本化目标模块', catalog.view('target'))}`;
    }
    function basketDefinition() {
      const b = s.strategy.target.basket || {};
      return panel(
        '冻结数量篮子定义',
        `${note(b.method === 'pair_ols' ? `已选择 ${(b.symbols || []).length} / 2 条腿。请明确勾选两只股票再继续。` : b.method === 'pca_residual' ? `已选择 ${(b.symbols || []).length} 条腿。PCA 目标需要 3–20 个明确成员。` : `已选择 ${(b.symbols || []).length} 条腿。请填写每条腿的固定数量。`)}${select('构造方法', 'target.basket.method', { pair_ols: '两腿价格 OLS 配对', pca_residual: 'PCA 投影状态篮子', fixed: '明确的固定数量' })}<div class="sq-basket-members">${s.strategy.universe.symbols.map((code) => `<label><input type="checkbox" data-sq-basket-symbol="${e(code)}" ${(b.symbols || []).includes(code) ? 'checked' : ''}><span>${e(code)}</span>${b.method === 'fixed' ? `<input type="number" data-sq-quantity="${e(code)}" aria-label="${e(code)} 固定数量" value="${e(b.quantities?.[code] ?? '')}" min="-1000000" max="1000000" step="any" ${(b.symbols || []).includes(code) ? '' : 'disabled'}>` : ''}</label>`).join('') || '<p>先在研究范围页明确选择成员。</p>'}</div><div class="sq-form-grid">${b.method !== 'fixed' ? input('形成窗口', 'target.basket.formationDays', { min: 60, max: 504, unit: '交易日' }) : ''}${b.method === 'pca_residual' ? input('共同主成分数量', 'target.basket.components', { min: 1, max: Math.min(10, Math.max(1, (b.symbols || []).length - 2)) }) : ''}</div>${note(b.method === 'pair_ols' ? '明确选择两只股票。OLS 截距是模型状态，不是可交易的一条腿；本身不构成协整证明。' : b.method === 'pca_residual' ? 'PCA 只构造共同成分与状态。未来变化由下一步的 F 模型预测，PCA 不直接生成仓位。' : '明确数量须与选定成员一一对应、非全零；零或负篮子价值仍使用正的总名义尺度进行归一。')}`,
        { kicker: 'FROZEN TARGET · SAME QUANTITIES' }
      );
    }
    function modelPage() {
      return `${panel(
        '先声明模型机制',
        `<div class="sq-family-grid">${Object.entries(FAMILIES)
          .map(
            ([id, x]) =>
              `<button class="sq-family ${s.strategy.model.family === id ? 'selected' : ''}" data-sq="family" data-id="${id}"><span>${i(id === 'pair_reversion' ? 'link' : id === 'event' ? 'spark' : 'model')}</span><strong>${e(x.name)}</strong><p>${e(x.description)}</p></button>`
          )
          .join(
            ''
          )}</div>${s.strategy.model.family === 'fundamental' ? note('需要真实、已披露的基本面预测字段。仅有目录定义不构成可用数据。') : s.strategy.model.family === 'event' ? note('需要 role:event 的 PIT 外部数值输入及足够已观测事件。名称和日收益本身不提供事件数据。') : ''}<div class="sq-form-grid">${select('函数估计方式', 'model.estimator', ESTIMATORS, 'auto 只比较有限候选与参数，使用按时间清除跨界标签的验证。')}${input('训练窗口', 'model.trainWindow', { min: 120, max: 1260, unit: '过去观测日期' })}${input('重新拟合间隔', 'model.refitDays', { min: 1, max: 126, unit: '交易日' })}</div>`,
        { kicker: 'F_h · CONDITIONAL FORECAST' }
      )}${panel('每次预测必须输出', `<div class="sq-contract-values"><div><span>当前状态</span><strong>P<sub>t</sub></strong></div><div><span>预期入场</span><strong>V̂<sub>entry</sub></strong></div><div><span>预期未来</span><strong>V̂<sub>future</sub></strong></div><div><span>预测价差</span><strong>e = P − V̂</strong></div></div><p>模型同时拟合入场与目标状态。无变化预测提供明确的零剩余 edge 基准；因子分数、z-score 和 PCA 载荷不会作为预测值直接送入执行。</p>`)}${catalog.view('model')}`;
    }
    function validationPage() {
      return `${panel('预测研究可以独立完成', `${toggle('启用后续交易模拟', 'execution.enabled', '关闭时仍生成完整预测、无变化基准比较与误差诊断，不要求发生交易。')}<div class="sq-validation-flow"><span>按日期排序</span>${i('arrow')}<span>成熟标签训练</span>${i('arrow')}<span>清除跨界标签</span>${i('arrow')}<span>顺序样本外预测</span></div><div class="sq-form-grid">${input('最终报告区间占比', 'validation.holdoutFraction', { min: 0.1, max: 0.4, step: 0.05, help: '0.2 表示最后 20% 报告日期。边界先按日历固定。' })}${input('最少训练日期', 'validation.minTrainDates', { min: 40, max: 252 })}</div>${advanced('完整时间验证参数', `<div class="sq-form-grid">${input('内层时间折数', 'validation.innerFolds', { min: 2, max: 3 })}${input('外层时间折数', 'validation.outerFolds', { min: 2, max: 3 })}</div>`, isStudio())}`, { kicker: 'FORECAST VALIDATION' })}${panel('预测诊断与执行评分分开', `<div class="sq-diagnostic-grid"><div><h3>预测误差</h3><p>归一化 MAE、RMSE、偏差；每个目标的价格单位误差单独展示。</p></div><div><h3>无变化基准</h3><p>在相同目标和时间范围内比较，保留无改善和失效结果。</p></div><div><h3>因子增量</h3><p>只有实际生成的同条件对照才作为证据；配置组合不等于跑过的实验。</p></div><div><h3>适用边界</h3><p>滚动重拟合可使用此前已成熟的报告期标签，属于顺序样本外；未实现依赖感知推断时不展示显著性。</p></div></div>`)}${isStudio() ? catalog.view('validation') : ''}`;
    }
    function riskPage() {
      return `${!s.strategy.execution.enabled ? note('当前为仅预测研究。这里保留独立执行假设，预测运行不会据此创建持仓。') : ''}${panel('独立组合约束', `<div class="sq-form-grid">${select('方向与借券边界', 'execution.side', { long_only: '仅多头', long_short: '理论多空' })}${input('目标总敞口', 'portfolio.grossExposure', { min: 0.1, max: 2, step: 0.1, help: '多空绝对名义金额之和 / 研究资金。' })}${input('单标的目标权重上限', 'portfolio.maxWeight', { min: 0.01, max: 1, step: 0.01 })}${input('净敞口绝对上限', 'portfolio.netExposureLimit', { min: 0, max: 2, step: 0.1, value: s.strategy.portfolio.netExposureLimit ?? 2, help: '总多头减总空头 / 净值的绝对值。' })}${input('最多并行目标持仓', 'execution.maxPositions', { min: 1, max: 50 })}${input('初始研究资金', 'portfolio.initialCapital', { min: 10000, max: 1e9, step: 10000, unit: '元' })}</div>${note('风险约束只决定如何执行有效预测。理论空头尚未核验实际券源；目标权重上限与实际价格漂移分别记录。')}`, { kicker: 'PORTFOLIO POLICY' })}${advanced('仓位缩放与因子暴露约束', riskControls(), isStudio())}${panel('对冲定义与预测角色', `<p>冻结篮子的构造放在“目标与期限”，PCA 对冲暴露放在“因子与状态”。预测因子与风险暴露分别标识。</p><div class="sq-actions">${button('step', '调整目标篮子', { id: 'target', icon: 'layers' })}${button('step', '调整因子角色', { id: 'state', icon: 'sliders' })}</div>`)}${isStudio() ? catalog.view('risk') : ''}`;
    }
    function riskControls() {
      const p = s.strategy.portfolio,
        limits = p.factorExposureLimits || [];
      return `<div class="sq-form-grid">${select('仓位缩放方式', 'portfolio.sizingMode', { fixed: '固定名义敞口', volatility_target: '历史协方差波动目标' })}${input('目标年化波动', 'portfolio.targetAnnualVolatility', { min: 0.01, max: 1, step: 0.01, value: p.targetAnnualVolatility ?? 0.1, help: '例如 0.10 表示 10%；历史估计不保证未来波动。' })}${input('波动估计窗口', 'portfolio.volatilityLookback', { min: 20, max: 252, value: p.volatilityLookback ?? 60, unit: '过去交易日' })}</div>${note('风险估计只使用前一收盘已知数据。因子暴露是研究池内 z-score 暴露 |wᵀz|，不是市场 beta。新入场按相同比例缩放所有篮子腿；已有超限退出仍受 T+1 和整体可成交性约束。')}<h3>因子暴露限制</h3><div class="sq-factor-risk">${
        s.strategy.factors
          .map((f) => {
            const limit = limits.find((x) => x.factorId === f.id);
            return `<label><input type="checkbox" data-sq-risk-factor="${e(f.id)}" ${limit ? 'checked' : ''}><span>${e(C.findFactor(f.id)?.name || f.id)}</span><input type="number" data-sq-risk-max="${e(f.id)}" aria-label="${e(f.id)} 最大绝对暴露" value="${e(limit?.maxAbsExposure ?? 0.5)}" min="0" max="5" step=".05" ${!limit ? 'disabled' : ''}></label>`;
          })
          .join('') || '<p class="sq-subtle">加入有实际观测值的因子后，才能明确选择暴露约束。</p>'
      }</div>`;
    }
    function executionPage() {
      return `${panel('只执行可追踪的预测', `${toggle('启用交易模拟', 'execution.enabled', '关闭仍可完成一项预测研究，随后复用其预测产物比较执行方案。')}<div class="sq-form-grid">${input('最小剩余预期 edge', 'execution.minEdgeBps', { min: 0, max: 10000, unit: 'bps', help: '使用预期未来状态减预期入场状态，结合声明费用判断。' })}${input('调仓检查间隔', 'portfolio.rebalanceDays', { min: 1, max: 60, unit: '交易日' })}${input('最小目标权重变化', 'portfolio.rebalanceThresholdBps', { min: 0, max: 10000, unit: 'bps' })}</div><div class="sq-equation-strip"><span>forecastId</span>${i('arrow')}<span>剩余 edge / 预计费用</span>${i('arrow')}<span>风险约束</span>${i('arrow')}<span>真实成交引用</span></div>`, { kicker: 'FORECAST → POLICY → LEDGER' })}${panel('成本假设', `<div class="sq-form-grid three">${input('佣金', 'costs.commissionBps', { min: 0, max: 100, step: 0.1, unit: 'bps' })}${input('单笔最低佣金', 'costs.minCommission', { min: 0, max: 1000, step: 0.1, unit: '元' })}${input('单边滑点', 'costs.slippageBps', { min: 0, max: 200, step: 0.1, unit: 'bps' })}${input('卖出税费', 'costs.sellTaxBps', { min: 0, max: 100, step: 0.1, unit: 'bps' })}${input('过户费', 'costs.transferBps', { min: 0, max: 100, step: 0.01, unit: 'bps' })}${input('年化理论借券费', 'costs.borrowAnnualBps', { min: 0, max: 10000, unit: 'bps' })}</div><p class="sq-subtle">1 bp = 0.01%。固定费率假设与实际支付金额分别记录；不声称重建全部历史费率。</p>`)}${panel('成交与退出规则', `<div class="sq-diagnostic-grid"><div><h3>明确入场日期</h3><p>当日不可成交则取消入场，不自动顺延预测目标。</p></div><div><h3>A 股多头 T+1</h3><p>新取得的多头数量不能在当日卖出，多腿可成交性整体检查。</p></div><div><h3>到期与风险退出</h3><p>失效预测不能开仓或增加风险；安全退出记录独立原因。</p></div><div><h3>同预测比较</h3><p>执行重跑引用同一个 forecastArtifactId 和冻结数据，不重新取数或拟合。</p></div></div>`)}${isStudio() ? catalog.view('execution') : ''}`;
    }
    function reviewPage() {
      const errors = validateStrategy(s.strategy, {
        includeData: true,
        dataSource: s.dataSource,
        dataset: s.dataset,
        session: s.session,
      });
      return `${panel('冻结本次研究协议', `${errors.length ? errors.map((x) => note(x, 'warning')).join('') : note('静态配置通过检查。真实字段覆盖、成熟标签与拟合条件会在运行时验证。')}<div class="sq-review-grid"><div><span>目标与期限</span><strong>${targetLabel()} · ${s.strategy.target.horizonSessions} 日</strong><p>参考、入场和未来目标使用同一计量定义。</p></div><div><span>模型协议</span><strong>${e(FAMILIES[s.strategy.model.family]?.name)} / ${e(ESTIMATORS[s.strategy.model.estimator])}</strong><p>训练窗口 ${s.strategy.model.trainWindow} 日期 · 每 ${s.strategy.model.refitDays} 日允许重拟合</p></div><div><span>研究范围</span><strong>${s.strategy.universe.symbols.length} 个明确成员</strong><p>${e(C.dateText(s.strategy.universe.start))} — ${e(C.dateText(s.strategy.universe.end))}</p></div><div><span>运行内容</span><strong>${s.strategy.execution.enabled ? '预测产物 + 独立执行账本' : '完整预测产物与诊断'}</strong><p>${e(sourceLabel())}</p></div></div><div class="sq-actions">${button('save', '保存版本', { icon: 'save' })}${button('export', '导出配置', { icon: 'download' })}</div>`, { kicker: 'IMMUTABLE RESEARCH RUN' })}${panel('研究的核心输出', `<div class="sq-contract-values"><div><span>当前状态</span><strong>P</strong></div><div><span>预期未来</span><strong>V̂</strong></div><div><span>预测价差</span><strong>e</strong></div><div><span>成熟后</span><strong>实现值 / 误差</strong></div></div><p>包括未交易、失效、尾部未成熟的预测。预览与完整私有产物分别展示，运行结果不以是否盈利决定保留。</p>`)}${ui.activeId ? button('experiment-detail', '查看这个研究的历史运行', { id: ui.activeId, icon: 'clock' }) : ''}`;
    }
    function latestRunStatus(item) {
      if (!Object.hasOwn(item, 'latestRun')) return '<span>运行状态尚未返回</span>';
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
                  button('new', '新建研究', { primary: true })
                ),
        {
          actions: button('refresh-experiments', '刷新', {
            small: true,
            icon: 'refresh',
          }),
        }
      )}<div class="sq-catalog-pagination"><span>${ui.loading || !ui.experimentsLoaded ? '研究数量读取中' : `共 ${ui.experimentTotal} 项 · 第 ${ui.experimentPage} 页`}</span><div>${button('experiment-page', '上一页', { small: true, page: ui.experimentPage - 1, disabled: ui.loading || !ui.experimentsLoaded || ui.experimentPage <= 1 })}${button('experiment-page', '下一页', { small: true, page: ui.experimentPage + 1, disabled: ui.loading || !ui.experimentsLoaded || ui.experimentPage * 100 >= ui.experimentTotal })}</div></div>`;
    }
    function experimentDetail() {
      const record = ui.viewedExperiment,
        item = record && (record.experiment || record.item || record);
      if (!item || item.id !== s.quantEntityId)
        return (
          heading('RESEARCH DETAIL', '读取研究详情', '配置、运行和预测产物将在这里关联展示。') +
          panel(
            '私有研究',
            ui.experimentDetailId === s.quantEntityId && ui.experimentDetailError
              ? note(ui.experimentDetailError, 'error') +
                  button('experiment-detail-retry', '重试读取研究', {
                    id: s.quantEntityId,
                    small: true,
                  })
              : '<div class="sq-loading" role="status">正在读取这份研究…</div>'
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
      if (s.view === 'runs') body = `<div class="sq-report-wrap">${C.runsView()}</div>`;
      else if (s.view === 'dashboard') body = home();
      else if (s.quantStep === 'financial') body = financial.render();
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
            })
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
                new URLSearchParams({ page: ui.comparePage, pageSize: 30 })
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
            })
        );
        if (request !== ui.comparisonHistoryRequest) return;
        ui.comparisons = response.items || [];
        ui.comparisonHistoryTotal = response.total || 0;
        ui.comparisonHistoryLoaded = true;
      } catch (err) {
        if (request === ui.comparisonHistoryRequest) ui.comparisonHistoryError = err.message;
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
        const response = await api('/statistical-quant/comparisons/' + encodeURIComponent(id));
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
        if (request === ui.comparisonDetailRequest) ui.comparisonDetailError = err.message;
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
              : empty('尚未保存比较', '比较产物会保留到私有工作区，可在重新打开页面后继续读取。')
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
          name: ui.compareKind === 'forecast' ? '预测证据比较' : '同预测执行比较',
        }),
      });
      ui.compare = response.comparison;
      await loadComparisonHistory();
      location.hash = 'quant/compare/' + encodeURIComponent(ui.compare.id);
      render();
    }
    function comparisonPage() {
      const choices = ui.compareChoices.filter((x) => x.kind === ui.compareKind),
        r = s.quantEntityId && ui.compare?.id !== s.quantEntityId ? null : ui.compare;
      return `${heading('CONTROLLED RESEARCH COMPARISON', '比较已生成的研究产物', '并排保留好坏结果；只有数据、目标与范围可比时，才称为受控比较。', button('compare-refresh', '刷新产物', { icon: 'refresh' }))}${panel('选择 2–8 项产物', `<label class="sq-field"><span>比较类型</span><select id="sq-compare-kind"><option value="forecast" ${ui.compareKind === 'forecast' ? 'selected' : ''}>预测产物与误差</option><option value="execution" ${ui.compareKind === 'execution' ? 'selected' : ''}>同预测的独立执行</option></select></label>${ui.compareError ? note(ui.compareError, 'error') + button('compare-refresh', '重试读取产物', { small: true }) : ui.compareLoading || !ui.compareLoaded ? '<div class="sq-loading" role="status">读取当前研究产物…</div>' : `<div class="sq-comparison-picker">${choices.map((x) => `<label><input type="checkbox" data-sq-compare="${e(x.id)}" ${ui.compareIds.includes(x.id) ? 'checked' : ''} ${(ui.compareIds.length >= 8 && !ui.compareIds.includes(x.id)) || (x.kind === 'execution' && x.status !== 'completed') ? 'disabled' : ''}><span>${e(x.label)}<small>${e(x.id)} · ${e(C.dateText(x.createdAt))}</small></span></label>`).join('') || empty('尚无可比较产物', '先完成至少两次预测研究，或在同一产物上完成两次独立执行。')}</div>`}<div class="sq-catalog-pagination"><span>${ui.compareLoading || !ui.compareLoaded ? '产物数量读取中' : `共 ${ui.compareTotal} 项 · 第 ${ui.comparePage} 页`}</span><div>${button('compare-page', '上一页', { small: true, page: ui.comparePage - 1, disabled: ui.compareLoading || !ui.compareLoaded || ui.comparePage <= 1 })}${button('compare-page', '下一页', { small: true, page: ui.comparePage + 1, disabled: ui.compareLoading || !ui.compareLoaded || ui.comparePage * 30 >= ui.compareTotal })}</div></div>${button('compare-create', '保存并生成比较', { primary: true, disabled: ui.compareIds.length < 2 || ui.compareLoading || !ui.compareLoaded || Boolean(ui.compareError) })}`)}${s.quantEntityId && !r ? panel('读取已保存比较', ui.comparisonDetailId === s.quantEntityId && ui.comparisonDetailError ? note(ui.comparisonDetailError, 'error') + button('compare-detail-retry', '重试读取比较', { id: s.quantEntityId, small: true }) : '<div class="sq-loading" role="status">正在读取这份比较的成员和结果…</div>') : ''}${
        r
          ? panel(
              '比较结果',
              `${note(r.controlledComparison ? '后台已核对本次比较所需的一致条件。预测或执行差异仍不代表显著或稳定优势。' : '这些产物的目标、数据、范围或预测身份并不一致。仅作并列展示，不能解释为控制其他条件后的增量。', r.controlledComparison ? 'info' : 'warning')}<div class="sq-table-scroll"><table class="sq-table"><thead><tr><th>产物</th>${r.kind === 'forecast' ? '<th>归一化 RMSE</th><th>无变化 MSE</th><th>MSE 改善</th>' : '<th>净收益</th><th>最大回撤</th><th>总成本</th>'}</tr></thead><tbody>${r.items
                .map((x) => {
                  const m = r.kind === 'forecast' ? x.diagnostics?.metrics || {} : x.metrics || {};
                  return `<tr><td><code>${e(x.id)}</code></td>${r.kind === 'forecast' ? `<td>${C.fmt(m.rmse, 6)}</td><td>${C.fmt(m.noChangeMse, 6)}</td><td>${C.fmt(m.mseImprovement, 6)}</td>` : `<td>${C.pct(m.totalReturn)}</td><td>${C.pct(m.maxDrawdown)}</td><td>${C.fmt(m.totalCosts)}</td>`}</tr>`;
                })
                .join(
                  ''
                )}</tbody></table></div>${advanced('可比性与逐项配置证据', `<pre class="sq-report-code">${e(JSON.stringify(r, null, 2))}</pre>`)}<a class="sq-button" href="/quant/api/statistical-quant/comparisons/${encodeURIComponent(r.id)}/download" download>导出比较</a>`
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
            new URLSearchParams({ page: ui.experimentPage, pageSize: 100 })
        );
        if (request !== ui.experimentsRequest) return;
        ui.experiments = result.items || [];
        ui.experimentTotal = result.total || 0;
        ui.experimentsLoaded = true;
      } catch (err) {
        if (request === ui.experimentsRequest) ui.experimentsError = err.message;
      } finally {
        if (request === ui.experimentsRequest) {
          ui.loading = false;
          render();
        }
      }
    }
    async function loadExperiment(id, edit = false) {
      const request = ++ui.experimentDetailRequest,
        routeAtStart = location.hash;
      ui.experimentDetailId = id;
      ui.experimentDetailLoading = true;
      ui.experimentDetailError = '';
      render();
      try {
        const response = await api('/statistical-quant/experiments/' + encodeURIComponent(id));
        if (request !== ui.experimentDetailRequest) return;
        ui.viewedExperiment = response;
        const item = response.experiment || response.item || response;
        if (edit && location.hash === routeAtStart) {
          s.strategy = normalizeStrategy(item.strategy || item.spec);
          ui.activeId = item.id;
          ui.activeVersion = item.version;
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
    async function save() {
      if (s.saving) {
        toast('当前版本正在保存，请等待完成。');
        return null;
      }
      const errors = validateStrategy(s.strategy);
      if (errors.length) {
        ui.pageErrors = errors;
        render();
        document.querySelector('#sq-page-errors')?.focus();
        toast(errors[0], true);
        return null;
      }
      ui.pageErrors = []; // Keep the submitted version separate from inputs that change while the request is in flight.
      const draft = s.strategy,
        submitted = clone(draft),
        id = ui.activeId,
        version = ui.activeVersion;
      s.saving = true;
      render();
      try {
        const response = await api(
          id
            ? '/statistical-quant/experiments/' + encodeURIComponent(id)
            : '/statistical-quant/experiments',
          {
            method: id ? 'PUT' : 'POST',
            body: JSON.stringify({
              strategy: submitted,
              ...(id ? { version } : {}),
            }),
          }
        );
        const item = response.experiment || response.item || response;
        const sameDraft = s.strategy === draft && ui.activeId === id;
        if (sameDraft) {
          ui.activeId = item.id;
          ui.activeVersion = item.version;
          const unchanged = JSON.stringify(s.strategy) === JSON.stringify(submitted);
          if (unchanged) {
            s.strategy = normalizeStrategy(item.strategy || submitted);
            persistDraft(false);
          } else persistDraft();
          toast(
            unchanged ? '研究版本已保存。' : '提交时的版本已保存；之后的修改保留为未保存草稿。'
          );
        } else toast('此前提交的研究版本已保存；当前研究保持不变。');
        ui.experiments = [item, ...ui.experiments.filter((x) => x.id !== item.id)];
        return item;
      } finally {
        s.saving = false;
        render();
      }
    }
    async function run() {
      if (s.submitting) return;
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
        dataSource = s.dataSource,
        dataset = s.dataset;
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
        const response = await api(
          '/statistical-quant/experiments/' + encodeURIComponent(item.id) + '/run',
          {
            method: 'POST',
            body: JSON.stringify({
              version: item.version,
              dataSource,
              ...(dataSource === 'upload' ? { dataset } : {}),
            }),
          }
        );
        const job = response.job;
        s.runs = [
          { ...job, strategy: clone(item.strategy || submitted) },
          ...s.runs.filter((x) => x.id !== job.id),
        ];
        s.job = job;
        C.navigate('runs', job.id);
        toast('研究已提交，将先生成预测产物。');
      } finally {
        s.submitting = false;
        render();
      }
    }
    async function runExecution(artifactId, configuration) {
      const response = await api('/statistical-quant/executions', {
        method: 'POST',
        body: JSON.stringify({
          forecastArtifactId: artifactId,
          ...configuration,
        }),
      });
      const job = response.job;
      s.runs = [job, ...s.runs.filter((x) => x.id !== job.id)];
      C.navigate('runs', job.id);
      return response;
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
        if (s.strategy.factors.length + unique.length > 32) throw Error('最多 32 个因子。');
        s.strategy.factors.push(...clone(unique));
      } else {
        if (patch.target?.kind === 'asset_price') delete s.strategy.target.basket;
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
      const errors = [];
      const controls = [
        ...document.querySelectorAll(
          '.sq-research-body input[data-sq-config],.sq-research-body select[data-sq-config]'
        ),
      ];
      for (const control of controls)
        if (!control.checkValidity())
          errors.push(
            `${control.closest('label')?.querySelector('span')?.textContent || '参数'}需要完整填写并符合范围。`
          );
      const st = step(),
        spec = s.strategy;
      if (st === 'universe' && (!spec.universe.symbols.length || spec.universe.symbols.length > 50))
        errors.push('先明确确认 1–50 个研究成员。');
      if (st === 'target' && spec.target.kind === 'frozen_basket') {
        const b = spec.target.basket || {},
          legs = b.symbols || [];
        if (b.method === 'pair_ols' && legs.length !== 2)
          errors.push('请明确选择两条配对篮子腿，再继续模型配置。');
        if (b.method === 'pca_residual' && (legs.length < 3 || legs.length > 20))
          errors.push('请明确选择 3–20 条 PCA 篮子腿。');
        if (
          b.method === 'fixed' &&
          (!legs.length ||
            legs.length > 20 ||
            legs.some((x) => !Number.isFinite(b.quantities?.[x])) ||
            !legs.some((x) => b.quantities?.[x] !== 0))
        )
          errors.push('请为 1–20 条固定篮子腿填写非全零的有限数量。');
      }
      if (st === 'model') {
        if (
          spec.model.family === 'pair_reversion' &&
          (spec.target.kind !== 'frozen_basket' || spec.target.basket?.method !== 'pair_ols')
        )
          errors.push('配对模型需要先在目标页面定义两腿 OLS 篮子。');
        if (spec.model.family === 'event' && !spec.factors.some((f) => f.role === 'event'))
          errors.push('事件模型需要带真实 PIT 字段的事件角色输入。');
      }
      return errors;
    }
    async function handle(element) {
      if (await reports.handle(element)) return;
      if (await catalog.handle(element)) return;
      const action = element.dataset.sq,
        id = element.dataset.id;
      if (action === 'new') {
        C.legacy.flow.reset();
        s.strategy = defaultStrategy();
        s.strategyId = null;
        s.strategyVersion = null;
        ui.activeId = null;
        ui.activeVersion = null;
        s.dataSource = 'tushare';
        persistDraft();
        location.hash = 'quant/universe';
        render();
      }
      if (action === 'restore-legacy-draft') {
        const saved = JSON.parse(localStorage.getItem('atlas-quant-draft-v1') || 'null');
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
          ui.pageErrors = currentStepErrors();
          if (ui.pageErrors.length) {
            render();
            const error = document.querySelector('#sq-page-errors');
            error?.focus({ preventScroll: true });
            error?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
            return;
          }
        }
        ui.pageErrors = [];
        goto(id);
      }
      if (action === 'save') await save();
      if (action === 'run') await run();
      if (action === 'export') C.download('atlas-statistical-quant-research.json', s.strategy);
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
      if (action === 'family') {
        s.strategy.model.family = id;
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
          { method: 'POST', body: '{}' }
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
            : element.value;
      setPath(s.strategy, path, value);
      persistDraft();
    }
    let recipeTimer;
    function onInput(element) {
      if (element.dataset.sqRiskMax) {
        const limit = s.strategy.portfolio.factorExposureLimits?.find(
          (x) => x.factorId === element.dataset.sqRiskMax
        );
        if (limit) {
          limit.maxAbsExposure = element.value === '' ? null : Number(element.value);
          persistDraft();
        }
      }
      if (element.id === 'sq-recipe-search') {
        ui.recipeQuery = element.value;
        ui.recipePage = 1;
        clearTimeout(recipeTimer);
        recipeTimer = setTimeout(() => loadRecipes(), 250);
      }
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
      if (element.dataset.sqRiskFactor) {
        const id = element.dataset.sqRiskFactor;
        s.strategy.portfolio.factorExposureLimits = (
          s.strategy.portfolio.factorExposureLimits || []
        ).filter((x) => x.factorId !== id);
        if (element.checked)
          s.strategy.portfolio.factorExposureLimits.push({
            factorId: id,
            maxAbsExposure: 0.5,
          });
        persistDraft();
        render();
      }
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
      if (element.id === 'sq-step-picker') goto(element.value);
      if (element.dataset.sqFactorRole) {
        s.strategy.factors.find((x) => x.id === element.dataset.sqFactorRole).role = element.value;
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
      ui.pageErrors = [];
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
      if (s.quantStep === 'compare' && s.quantEntityId && ui.compare?.id !== s.quantEntityId)
        await loadComparisonDetail(s.quantEntityId);
      if (s.quantStep === 'compare' && !ui.compareLoaded && !ui.compareLoading)
        await loadCompareChoices();
      const stageMap = {
        state: 'state',
        target: 'target',
        model: 'model',
        validation: 'validation',
        risk: 'risk',
        execution: 'execution',
      };
      if (
        s.quantStep === 'experiment' &&
        s.quantEntityId &&
        (!ui.viewedExperiment ||
          (ui.viewedExperiment.experiment || ui.viewedExperiment.item || ui.viewedExperiment).id !==
            s.quantEntityId)
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
            if (!ghost && Math.hypot(ev.clientX - initial.x, ev.clientY - initial.y) > 8) {
              ghost = document.createElement('div');
              ghost.className = 'sq-drag-ghost';
              ghost.textContent = element.querySelector('h3,strong')?.textContent || '研究模块';
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
            drop = document.elementFromPoint(ev.clientX, ev.clientY)?.closest('[data-sq-drop]');
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
              const module = catalog.state.items.find((x) => x.id === payload.slice(7));
              if (module && drop.dataset.sqDrop === module.stage)
                applyModule(module).catch((err) => toast(err.message, true));
            }
          };
          element.addEventListener('pointermove', move, { passive: false });
          element.addEventListener('pointerup', done);
          element.addEventListener('pointercancel', done);
        })
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
        id = event.dataTransfer?.getData('application/x-atlas-module') || nativeModule,
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
        .forEach((x) => x.classList.remove('sq-drop-active', 'sq-native-dragging'));
    });
    document.addEventListener('click', (event) => {
      const button = event.target.closest('[data-sq]');
      if (!button || button.disabled) return;
      event.preventDefault();
      Promise.resolve(handle(button)).catch((err) => toast(err.message, true));
    });
    document.addEventListener('input', (event) => onInput(event.target));
    document.addEventListener('change', (event) => onChange(event.target));
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
