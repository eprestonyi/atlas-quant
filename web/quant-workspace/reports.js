import { reportFeatureLabeler, createFeatureLabeler } from './feature-labels.js';
import { marketDatasetDownload } from './source-downloads.js';
import { financialTransportSource, datasetLocation } from './datasets/protocol.js';
import { createModelFunctionEditor } from './model-function-editor.js';
import { createFactorDiagnostics } from './factor-diagnostics.js';
import { renderSavedModel } from './report-model.js';
import { createModelCandidates } from './model-candidates.js';
import { forecastCharts } from './report-charts.js';
import { renderContextSources } from './context-source-view.js';
// Read-only views of immutable forecast artifacts; execution overrides live in a separate UI draft.
import { ESTIMATORS } from './defaults.js';
import { createReportSource } from './report-source.js';
export function createForecastReports(C, F) {
  const { esc: e, fmt, pct, dateText: d, render, api, toast, openModal, closeModal } = C;
  const ui = {
    artifactId: null,
    runIdentity: null,
    tab: 'forecasts',
    page: 1,
    query: '',
    scope: 'latest',
    status: 'all',
    target: '',
    targetLabel: '',
    tradePage: 1,
    riskPage: 1,
    riskFilter: 'events',
    busy: false,
    result: null,
    dateFrom: '',
    dateTo: ''
  };
  const remote = createReportSource(C);
  const functionEditor = createModelFunctionEditor(C, F);
  let selectedFit = null, fitRequest = 0;
  const financialReport = () => remote.transport?.format === 'atlas.quant.financial_bundle';
  const marketReport = r => !!r.provenance?.marketSource;
  const factorOnlyReport = r => financialReport() || marketReport(r) || r.execution?.enabled === false;
  function financialSourceRef() {
    return financialTransportSource(remote.transport);
  }
  const reasonLabel = (value) => {
    if (!value) return '按预测入场';
    const [key, ...rest] = String(value).split(':');
    const name =
      {
        risk_limit_exit: '风险约束退出',
        target_expiry: '预测目标到期退出',
        delayed_target_expiry: '目标到期后延迟成交退出',
        basket_leg_unavailable: '部分篮子腿不可成交',
        a_share_t1_lock: 'A 股 T+1 锁定',
        long_t_plus_one: '多头 T+1 尚不可卖',
        adverse_one_price_bar: '不利方向单一价格行情，拒绝假定成交',
        net_exposure_limit: '净敞口超限',
        volatility_target_limit: '估计波动超过目标',
        volatility_history_unavailable: '波动估计历史不足',
        factor_exposure_limit: '因子暴露超限',
        factor_exposure_unavailable: '因子风险数据不可用',
        nonpositive_equity: '净值非正',
        entry_clock_not_due: '尚未到入场检查日',
        position_count_limit: '达到目标持仓数量上限',
        insufficient_predicted_gross_edge: '预期剩余变化不足',
        insufficient_predicted_edge_after_estimated_cost: '扣除估计成本后 edge 不足',
        short_leg_forbidden: '当前方案不允许空头腿',
        entry_unavailable_forecast_not_delayed: '入场日不可成交，取消本次入场',
        basket_rebalance_band: '小于权重变化阈值',
        exposure_limit: '敞口限额不足',
        risk_inputs_unavailable: '风险输入不可用',
        invalid_or_expired_forecast: '预测失效或到期',
        target_outside_available_calendar: '目标超出已知交易日历',
        forecast_edge_after_cost: '剩余预测变化覆盖预计费用'
      }[key] || key;
    return name + (rest.length ? '：' + rest.join(':') : '');
  };
  const reportDiagnostics = (r) => r.forecasts?.diagnostics || r.validation || {};
  const tags = {
    models: 'F 模型与参数',
    forecasts: '拟合数据',
    validation: '检验',
    factorDiagnostics: '因子统计',
    correlations: '相关矩阵',
    exposures: '同期关联',
    joints: '联合分布',
    targets: '研究目标',
    execution: '独立执行',
    provenance: '来源与复现'
  };
  const JSONView = (value) =>
    `<pre class="sq-report-code">${e(JSON.stringify(value, null, 2))}</pre>`;
  const table = (head, rows) =>
    `<div class="sq-table-scroll"><table class="sq-table"><thead><tr>${head.map((x) => `<th>${e(x)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
  const cell = (v, n = 4) => `<td class="numeric">${fmt(v, n)}</td>`;
  const stat = (label, value, note) =>
    `<article class="sq-report-stat"><span>${e(label)}</span><strong>${e(value)}</strong><small>${e(note)}</small></article>`;
  const targetName = (id, r = ui.result) => {
    const def = r?.forecasts?.targetDefinitions?.find((x) => x.id === id);
    return def
      ? def.symbols.join(' / ') +
          (def.construction === 'pca_residual'
            ? ` · 投影列 ${Number(def.hedgeAudit?.projectionColumn) + 1}`
            : '')
      : id;
  };
  function pageTargetName(id, page, r) {
    const label = page.related?.targetLabels?.[id];
    if (label) return label;
    const definition = page.related?.targets?.find((x) => x.id === id);
    return definition
      ? targetName(id, { forecasts: { targetDefinitions: [definition] } })
      : targetName(id, r);
  }
  function remoteState(page, body, emptyMessage = '没有匹配记录') {
    if (page.unavailable)
      return F.note('本次产物未包含这类记录；不将缺席的证据视为已计算的空结果。');
    if (page.loading || (!page.loaded && !page.error))
      return '<div class="sq-loading" role="status" aria-live="polite">正在读取这一页…</div>';
    if (page.error)
      return (
        F.note(page.error, 'error') +
        F.button('forecast-remote-retry', '重试这一页', {
          id: page.key,
          small: true
        })
      );
    return (
      body +
      (!page.items.length
        ? F.empty(emptyMessage, '当前筛选下没有记录；这不表示完整产物缺失。')
        : '') +
      remotePages(page)
    );
  }
  function remotePages(page) {
    return `<div class="sq-catalog-pagination"><span>匹配 ${page.total.toLocaleString()} 条 · 当前 ${page.items.length ? page.offset + 1 : 0}–${page.offset + page.items.length} 条 · 按需读取</span><div>${F.button('forecast-remote-page', '上一页', { id: page.queryKey, direction: 'previous', small: true, disabled: !page.previous })}${F.button('forecast-remote-page', '下一页', { id: page.queryKey, direction: 'next', small: true, disabled: !page.hasMore })}</div></div>`;
  }
  function remoteRawCollection(collection, title) {
    const page = remote.page(collection);
    return F.panel(
      title,
      remoteState(
        page,
        page.items
          .map((item) => F.advanced(item.id || item.date || '证据记录', JSONView(item)))
          .join('')
      )
    );
  }
  const pages = (page, total, action) =>
    `<div class="sq-catalog-pagination"><span>匹配 ${total.toLocaleString()} 条 · 第 ${page} / ${Math.max(1, Math.ceil(total / 25))} 页 · 每页 25 条</span><div>${F.button(action, '上一页', { page: page - 1, small: true, disabled: page <= 1 })}${F.button(action, '下一页', { page: page + 1, small: true, disabled: page * 25 >= total })}</div></div>`;
  const factorDiagnostics = createFactorDiagnostics(C, F, { remote, remoteState, table });
  const modelCandidates = createModelCandidates(C, F, { remote, remotePages, table, functionEditor });
  function renderReport(r) {
    remote.bind(C.state.reportTransport, C.state.runId);
    const f = r.forecasts;
    if (!f) return F.note('此历史报告没有新协议的预测产物；不会补造 P、V 或预测误差。', 'warning');
    if (C.state.reportTransport && !remote.enabled())
      return F.note('此报告使用尚未支持的传输版本，请更新客户端后读取。', 'error');
    if (remote.enabled() && remote.transport.logicalArtifactId !== f.artifactId)
      return F.note('预测身份与报告传输身份不匹配，请重新读取报告。', 'error');
    if (remote.enabled() && remote.transport.complete !== true)
      return F.note('研究产物尚未完整提交，不能把分片预览当作完整研究或用于执行。', 'warning');
    const runIdentity =
      C.state.runId ||
      JSON.stringify([
        f.artifactId,
        r.strategy?.execution,
        r.strategy?.portfolio,
        r.strategy?.costs
      ]);
    const transportIdentity = JSON.stringify([remote.transport?.format, remote.transport?.version, remote.transport?.bundleId]);
    if (ui.artifactId !== f.artifactId || ui.runIdentity !== runIdentity || ui.transportIdentity !== transportIdentity) {
      selectedFit = null;
      modelCandidates.reset();
      fitRequest++;
      Object.assign(ui, {
        artifactId: f.artifactId,
        runIdentity,
        transportIdentity,
        tab: r.forecasts.factorResearch || factorOnlyReport(r) ? 'models' : 'forecasts',
        page: 1,
        query: '',
        target: '',
        targetLabel: '',
        status: r.forecasts.factorResearch || factorOnlyReport(r) ? 'mature' : 'all',
        scope: r.forecasts.factorResearch || factorOnlyReport(r) ? 'all' : 'latest',
        tradePage: 1,
        riskPage: 1,
        riskFilter: 'events',
        dateFrom: '',
        dateTo: '',
        baselineOpen: false,
        fitId: '', treeOutput: 1, treeIndex: 0
      });
    }
    ui.result = r;
    const v = reportDiagnostics(r),
      m = v.metrics || {};
    const evidence =
      r.selection?.evidenceStatus === 'NO_VALIDATED_FORECAST_EDGE'
        ? '当前没有验证出预测改善'
        : '预测检验已生成';
    const downloadUrl = remote.enabled()
      ? remote.transport.downloadUrl
      : `/quant/api/statistical-quant/forecasts/${encodeURIComponent(f.artifactId)}/download`;
    const bundleUrl = remote.enabled() ? remote.transport.bundleDownloadUrl : null;
    const sourceRef = financialSourceRef();
    const datasetArchiveUrl = sourceRef
      ? datasetLocation(sourceRef).archive
      : marketDatasetDownload(r.provenance?.marketSource?.marketDatasetRef);
    const packLabel = financialReport()
      ? '下载财务预测结果包'
      : marketReport(r) ? '下载市场预测结果包'
      : remote.transport?.hasFrozenInputs === true
        ? '下载私有复现包'
        : '下载私有执行记录包';
    const packNote = financialReport()
      ? '完整来源核验需要同时保留预测结果包与数据集闭包包；当前不支持交易执行或执行重放。'
      : marketReport(r) ? '完整来源核验需要同时保留市场预测结果包与完整行情来源包；当前不支持交易执行或执行重放。'
      : remote.transport?.hasFrozenInputs === true
        ? '包含冻结行情、预测与来源；保存在你的设备，不会公开分享。'
        : '包含执行与冻结预测；重放还需要来源预测包中的原始行情。';
    const downloads = `<div class="sq-report-downloads"><a class="sq-button small" href="${e(downloadUrl)}" download>${remote.enabled() ? '流式下载完整私有报告' : '下载完整私有产物'}</a>${bundleUrl ? `<a class="sq-button small" href="${e(bundleUrl)}" download>${packLabel}</a><small>${packNote}</small>` : ''}${datasetArchiveUrl ? `<a class="sq-button small" href="${e(datasetArchiveUrl)}" download>${financialReport() ? '下载数据集完整闭包' : '下载完整行情来源包'}</a>` : financialReport() || marketReport(r) ? F.note('未返回完整数据集引用，不能宣称来源闭包已齐备。', 'warning') : ''}</div>`;
    ui.downloads = downloads;
    const views = {
      models, forecasts: forecastRows,
      validation: result => `<div class="sq-report-stats">${stat('成熟预测观测', fmt(m.observations, 0), '')}${stat('联合 RMSE', fmt(m.rmse, 6), '')}${stat('相对无变化 MSE 改善', pct(m.relativeMseImprovement), '')}${stat('剩余变化 RMSE', fmt(m.remainingChangeRmse, 6), '')}</div>` + validation(result),
      factorDiagnostics: result => factorDiagnostics.render(result, 'features'),
      correlations: result => factorDiagnostics.render(result, 'correlations'),
      exposures: result => factorDiagnostics.render(result, 'exposures'),
      joints: result => factorDiagnostics.render(result, 'joints'),
      targets, execution, provenance
    };
    return `<div class="sq-report-heading"><h2>F 模型与报告</h2><span class="sq-status ${r.selection?.evidenceStatus === 'NO_VALIDATED_FORECAST_EDGE' ? 'warning' : ''}">${e(evidence)}</span>${r.provenance?.synthetic || r.provenance?.dataSource === 'demo' ? '<span class="sq-status warning">合成数据</span>' : ''}${F.advanced('下载', `${remote.enabled() ? '<span class="sq-status">完整产物已提交</span>' : ''}${downloads}`)}</div><div class="sq-report-tabs" role="group" aria-label="预测报告章节">${Object.entries(tags)
      .filter(([id]) => id !== 'execution' || !factorOnlyReport(r))
      .map(([id, label]) => F.button('forecast-tab', label, { id, primary: ui.tab === id, pressed: ui.tab === id, small: true })).join('')}</div>${(views[ui.tab] || models)(r)}`;
  }

  function selectedRows(r) {
    let rows = [...(r.forecasts.rows || [])];
    if (ui.scope === 'latest') {
      const latest = new Map();
      for (const row of rows) {
        const key = targetName(row.targetId, r);
        if (!latest.has(key) || row.date > latest.get(key).date) latest.set(key, row);
      }
      rows = [...latest.values()];
    }
    if (ui.status === 'mature') rows = rows.filter((x) => x.labelMaturedAt);
    else if (ui.status === 'unmatured') rows = rows.filter((x) => !x.labelMaturedAt);
    else if (ui.status !== 'all') rows = rows.filter((x) => x.status === ui.status);
    if (ui.target) rows = rows.filter((x) => targetName(x.targetId, r) === ui.target);
    const q = ui.query.toLowerCase().trim();
    if (q)
      rows = rows.filter((x) =>
        [x.forecastId, x.date, x.targetId, targetName(x.targetId, r), x.invalidReason || '']
          .join(' ')
          .toLowerCase()
          .includes(q)
      );
    return rows.sort(
      (a, b) => b.date.localeCompare(a.date) || a.targetId.localeCompare(b.targetId)
    );
  }
  function forecastRows(r) {
    if (remote.enabled()) return remoteForecastRows(r);
    const rows = selectedRows(r),
      page = Math.min(ui.page, Math.max(1, Math.ceil(rows.length / 25))),
      shown = rows.slice((page - 1) * 25, page * 25),
      names = [...new Set(r.forecasts.targetDefinitions.map((x) => targetName(x.id, r)))];
    return F.panel(
      '每一条预测都可核对',
      forecastCharts(rows, { esc: e, scope: `${ui.status === 'mature' ? '标签已成熟 · ' : ''}当前筛选全部 ${rows.length} 条记录` }) + `<div class="sq-report-controls"><label class="sq-search">${C.icon('search')}<input id="sq-forecast-search" aria-label="搜索预测记录" value="${e(ui.query)}" placeholder="日期、标的、forecastId"></label><select id="sq-forecast-scope" aria-label="预测时间范围"><option value="latest" ${ui.scope === 'latest' ? 'selected' : ''}>每组目标最新记录</option><option value="all" ${ui.scope === 'all' ? 'selected' : ''}>全部历史记录</option></select><select id="sq-forecast-status" aria-label="预测状态">${Object.entries(
        {
          all: '所有状态',
          valid: '有效预测',
          invalid: '失效预测',
          mature: '标签已成熟',
          unmatured: '标签未成熟'
        }
      )
        .map(
          ([key, label]) =>
            `<option value="${key}" ${ui.status === key ? 'selected' : ''}>${label}</option>`
        )
        .join(
          ''
        )}</select><select id="sq-forecast-target" aria-label="预测目标"><option value="">全部目标</option>${names.map((x) => `<option value="${e(x)}" ${x === ui.target ? 'selected' : ''}>${e(x)}</option>`).join('')}</select></div>${table(
        [
          '观察日 / 目标',
          '当前状态 P',
          '预期入场',
          '预期未来 V',
          'e = P − V',
          '剩余预期 bps',
          '实现未来 / 误差',
          '状态 / 记录'
        ],
        shown.map(
          (row) =>
            `<tr><td>${e(d(row.date))}<small>${e(targetName(row.targetId, r))}</small><small>h=${row.horizonSessions} · 目标 ${e(d(row.targetDate))}</small></td>${cell(row.currentState)}${cell(row.expectedEntry)}${cell(row.expectedFuture)}${cell(row.edgeGap)}${cell(row.expectedGrossBps, 2)}<td class="numeric">${fmt(row.realizedFuture, 4)}<small>${fmt(row.forecastError, 4)}</small></td><td><span class="sq-status ${row.status === 'valid' ? 'ready' : 'warning'}">${row.status === 'valid' ? '有效' : '失效'}</span><small>${row.labelMaturedAt ? '标签成熟' : '标签未成熟'}</small>${F.button('forecast-row', '查看记录', { id: row.forecastId, small: true })}</td></tr>`
        )
      )}${!shown.length ? F.empty('没有匹配预测', '更换范围、状态或搜索条件。') : ''}${pages(page, rows.length, 'forecast-page')}${F.advanced('数据口径', '<p>P／V／e 使用对应目标的价格单位；不同篮子的绝对值不能直接混比。误差 = 实现未来 − 预期未来。最新记录可能因超出已知交易日历而失效，不会隐去。未知值显示 —。</p>')}`,
      {
        kicker: 'FORECAST LEDGER',
        description: ''
      }
    );
  }
  function remoteForecastRows(r) {
    const page = remote.page('forecasts', {
      scope: ui.scope,
      status: ui.status === 'all' ? '' : ui.status,
      targetId: ui.target.trim(),
      id: ui.query.trim(),
      dateFrom: ui.dateFrom.replaceAll('-', ''),
      dateTo: ui.dateTo.replaceAll('-', '')
    });
    const lookup = F.advanced(
      '按记录编号定位',
      `<div class="sq-report-controls sq-report-filter-grid"><label class="sq-field"><span>精确 forecastId</span><input id="sq-forecast-search" aria-label="精确预测记录 ID" value="${e(ui.query)}" placeholder="完整 forecastId；不做全量模糊扫描"></label><label class="sq-field"><span>目标定义 ID</span><input id="sq-forecast-target" aria-label="精确目标定义 ID" value="${e(ui.target)}" placeholder="完整 targetId"></label></div>`
    );
    const controls = `<div class="sq-report-controls sq-report-filter-grid"><label class="sq-field"><span>观察日起</span><input type="date" id="sq-forecast-date-from" value="${e(ui.dateFrom)}"></label><label class="sq-field"><span>观察日止</span><input type="date" id="sq-forecast-date-to" value="${e(ui.dateTo)}"></label><label class="sq-field"><span>范围</span><select id="sq-forecast-scope"><option value="latest" ${ui.scope === 'latest' ? 'selected' : ''}>每组目标最新记录</option><option value="all" ${ui.scope === 'all' ? 'selected' : ''}>全部历史记录</option></select></label><label class="sq-field"><span>预测状态</span><select id="sq-forecast-status">${Object.entries(
      {
        all: '所有状态',
        valid: '有效预测',
        invalid: '失效预测',
        mature: '标签已成熟',
        unmatured: '标签未成熟'
      }
    )
      .map(
        ([id, label]) =>
          `<option value="${id}" ${ui.status === id ? 'selected' : ''}>${label}</option>`
      )
      .join(
        ''
      )}</select></label></div>${lookup}${ui.target ? `<div class="sq-actions"><span class="sq-subtle">当前目标：${e(ui.targetLabel || '已定位的目标定义')}</span>${F.button('forecast-clear-target', '清除目标筛选', { small: true })}</div>` : ''}`;
    return F.panel(
      '每一条预测都可核对',
      forecastCharts(page.items, { esc: e, scope: `${ui.status === 'mature' ? '标签已成熟 · ' : ''}当前页 ${page.items.length} / ${page.total ?? '—'} 条记录的预览` }) + F.advanced('筛选记录', controls) +
        remoteState(
          page,
          table(
            [
              '观察日 / 目标',
              '当前状态 P',
              '预期入场',
              '预期未来 V',
              'e = P − V',
              '剩余预期 bps',
              '实现未来 / 误差',
              '状态 / 记录'
            ],
            page.items.map(
              (row) =>
                `<tr><td>${e(d(row.date))}<small>${e(pageTargetName(row.targetId, page, r))}</small><small>h=${e(row.horizonSessions)} · 目标 ${e(d(row.targetDate))}</small></td>${cell(row.currentState)}${cell(row.expectedEntry)}${cell(row.expectedFuture)}${cell(row.edgeGap)}${cell(row.expectedGrossBps, 2)}<td class="numeric">${fmt(row.realizedFuture, 4)}<small>${fmt(row.forecastError, 4)}</small></td><td><span class="sq-status ${row.status === 'valid' ? 'ready' : 'warning'}">${row.status === 'valid' ? '有效' : '失效'}</span><small>${row.labelMaturedAt ? '标签成熟' : '标签未成熟'}</small>${F.button('forecast-row', '查看记录', { id: row.forecastId, small: true })}</td></tr>`
            )
          )
        ) +
        F.advanced('数据口径', '<p>筛选与排序在已提交的分片索引中完成。当前页预览不是计算截断；失效与尾部未成熟记录保留。P／V／e 使用该目标的价格单位，误差 = 实现未来 − 预期未来。</p>'),
      {
        kicker: 'FORECAST LEDGER',
        description: ''
      }
    );
  }
  function validation(r) {
    if (remote.enabled()) return remoteValidation(r);
    const v = reportDiagnostics(r),
      m = v.metrics || {};
    return (
      uncertainty(r) +
      factorIncrement(r) +
      F.panel(
        '预测与无变化基准',
        F.note(
          m.weighting === 'equal_weight_daily_average'
            ? `按观察日期等权聚合，每个日期先计算截面均值；共 ${m.observedDates ?? '—'} 个日期。此表偏差 = 预期 − 实际。`
            : '此历史报告未声明日期权重口径；保留原统计值。此表偏差 = 预期 − 实际。'
        ) +
          table(
            ['统计量', '数值', '解释'],
            [
              ['联合 MSE', m.mse, '预测归一化入场与退出状态'],
              ['无变化 MSE', m.noChangeMse, '入场与退出均预测为当前状态'],
              ['MSE 改善', m.mseImprovement, '无变化 MSE − 模型 MSE'],
              ['归一化 MAE', m.mae, '绝对误差'],
              ['入场 RMSE', m.entryRmse, '预期入场状态'],
              ['目标 RMSE', m.exitRmse, '预期未来状态'],
              ['剩余变化 RMSE', m.remainingChangeRmse, '预期剩余变化与实际变化'],
              ['入场偏差', m.bias?.[0], '预期 − 实际'],
              ['目标偏差', m.bias?.[1], '预期 − 实际']
            ].map(
              ([label, value, help]) =>
                `<tr><td>${label}</td>${cell(value, 7)}<td>${help}</td></tr>`
            )
          )
      ) +
      F.panel(
        '按目标分别计量',
        table(
          ['目标', '成熟观测', '价格单位偏差', '价格单位 RMSE'],
          (v.perTarget || [])
            .slice((ui.page - 1) * 25, ui.page * 25)
            .map(
              (x) =>
                `<tr><td>${e(targetName(x.targetId, r))}<small class="mono">${e(x.targetId)}</small></td>${cell(x.observations, 0)}${cell(x.priceBias)}${cell(x.priceRmse)}</tr>`
            )
        ) + pages(ui.page, v.perTarget?.length || 0, 'forecast-page')
      ) +
      F.panel(
        '开发期模型比较',
        table(
          ['候选', '参数', '验证损失 / 状态'],
          (v.finalTrials || []).map(
            (x) =>
              `<tr><td>${e(ESTIMATORS[x.estimator] || x.estimator || x.id)}<small>${e(x.id)}</small></td><td><code>${e(JSON.stringify(x.params || {}))}</code></td><td>${e(x.score !== undefined ? fmt(x.score, 7) : x.status || '未返回')}${F.button('forecast-trial', '折内证据', { id: x.id, small: true })}</td></tr>`
          )
        )
      ) +
      F.panel(
        '按时间验证的证据',
        `<dl class="sq-key-values"><dt>最终区间选模型</dt><dd>${v.selectionUsesHoldout === false ? '否' : v.selectionUsesHoldout === true ? '是' : '未返回证据'}</dd><dt>标签清除规则</dt><dd>${e(v.purgeRule || '未返回')}</dd><dt>顺序滚动拟合</dt><dd>${v.rollingRefitsUseMaturedPastHoldoutLabels ? '可使用此前成熟的报告期标签' : '未返回该证据'}</dd><dt>显著性检验</dt><dd>${v.significanceTested ? '引擎已记录' : '未提供独立显著性结论；均值区间另列'}</dd></dl>${F.advanced('外层折与训练边界', JSONView(v.outerFolds || []))}`,
        {
          description: '误差改善不等于可获利，不把均值回归作为已经成立的前提。'
        }
      )
    );
  }
  function remoteValidation(r) {
    const v = reportDiagnostics(r),
      m = v.metrics || {};
    const perTarget = remote.page('perTarget');
    const trials = remote.page('finalTrials');
    return (
      uncertainty(r) +
      factorIncrement(r) +
      (ui.baselineOpen ? remoteBaseline(r) : '') +
      F.panel(
        '预测与无变化基准',
        F.note(
          m.weighting === 'equal_weight_daily_average'
            ? `按观察日期等权聚合，共 ${m.observedDates ?? '—'} 个日期。偏差 = 预期 − 实际。`
            : '此历史报告未声明日期权重口径，保留原统计值。偏差 = 预期 − 实际。'
        ) +
          table(
            ['统计量', '数值'],
            [
              ['联合 MSE', m.mse],
              ['无变化 MSE', m.noChangeMse],
              ['MSE 改善', m.mseImprovement],
              ['归一化 MAE', m.mae],
              ['入场 RMSE', m.entryRmse],
              ['目标 RMSE', m.exitRmse],
              ['剩余变化 RMSE', m.remainingChangeRmse],
              ['入场偏差', m.bias?.[0]],
              ['目标偏差', m.bias?.[1]]
            ].map(([label, value]) => `<tr><td>${label}</td>${cell(value, 7)}</tr>`)
          )
      ) +
      F.panel(
        '按目标分别计量',
        remoteState(
          perTarget,
          table(
            ['目标', '成熟观测', '价格单位偏差', '价格单位 RMSE'],
            perTarget.items.map(
              (x) =>
                `<tr><td>${e(pageTargetName(x.targetId, perTarget, r))}<small class="mono">${e(x.targetId)}</small></td>${cell(x.observations, 0)}${cell(x.priceBias)}${cell(x.priceRmse)}</tr>`
            )
          )
        )
      ) +
      F.panel(
        '开发期模型比较',
        remoteState(
          trials,
          table(
            ['候选', '参数', '验证损失 / 状态'],
            trials.items.map(
              (x) =>
                `<tr><td>${e(ESTIMATORS[x.estimator] || x.estimator || x.id)}<small>${e(x.id)}</small></td><td><code>${e(JSON.stringify(x.params || {}))}</code></td><td>${e(x.score !== undefined ? fmt(x.score, 7) : x.status || '未返回')}${F.button('forecast-trial', '折内证据', { id: x.id, small: true })}</td></tr>`
            )
          )
        )
      ) +
      F.panel(
        '按时间验证的边界',
        `<dl class="sq-key-values"><dt>最终区间选模型</dt><dd>${v.selectionUsesHoldout === false ? '否' : v.selectionUsesHoldout === true ? '是' : '未返回证据'}</dd><dt>标签清除规则</dt><dd>${e(v.purgeRule || '未返回')}</dd><dt>顺序滚动拟合</dt><dd>${v.rollingRefitsUseMaturedPastHoldoutLabels ? '可使用此前成熟的报告期标签' : '未返回该证据'}</dd></dl>`
      ) +
      remoteRawCollection('outerFolds', '外层折与训练边界')
    );
  }
  function factorIncrement(r) {
    const f = reportDiagnostics(r).factorIncrement;
    if (!f) return F.note('此报告没有生成因子增量对照。不会从目录定义推断增量收益。');
    if (f.status === 'not_applicable')
      return F.note(
        '当前没有额外预测或事件因子，因子增量对照不适用。PCA 对冲角色没有被当作预测特征移除。'
      );
    if (f.status !== 'available')
      return F.panel(
        '因子增量目前不可估计',
        F.note(
          '没有足够的双方有效且成熟的配对预测，本次没有计算预测改善。模型不可用不等于未来标签尚未成熟；不会把缺失改善填成 0。',
          'warning'
        ) +
          incrementCoverage(f) +
          `<div class="sq-actions">${F.button('forecast-baseline', '查看状态基准预测', { icon: 'book', small: true })}</div>`
      );
    const daily = remote.enabled() ? remote.page('dailyLosses') : null;
    return F.panel(
      '这些因子是否改善了预测？',
      `${table(
        ['对照', '按日期均衡的联合 MSE'],
        [
          ['加入额外因子', f.withFactorsMse],
          ['只保留模型自带状态', f.stateOnlyMse],
          ['因子增量改善 · 基准减当前', f.dateBalancedMseImprovement]
        ].map(([name, value]) => `<tr><td>${name}</td>${cell(value, 8)}</tr>`)
      )}<div class="sq-report-stats">${stat('配对观察日期', fmt(f.pairedDates, 0), '相同目标、日期与双方有效成熟预测')}${stat('配对观测', fmt(f.pairedObservations, 0), '未把截面行数当作独立样本量')}${stat('相对 MSE 改善', pct(f.relativeMseImprovement), '负值表示加入因子后误差更高')}</div>${F.note(f.dateBalancedMseImprovement > 0 ? '这组因子在本次配对预测上降低了损失。该差异尚未进行因子增量显著性检验，不能解释为因果贡献或可获利。' : '这组因子没有降低本次配对预测损失。负向结果照常保留，不隐藏不利对照。', 'warning')}<p class="sq-subtle">实际移除的预测输入：${e((f.featuresRemoved || []).map(reportFeatureLabeler(r, C.state.catalog?.factors || [])).join('、'))}。基准在相同候选与验证预算内独立选模；冻结目标数量和事件/缺失输入掩码保持一致，但两个模型不一定生成相同范围的有效预测。对冲因子未被移除。</p>${incrementCoverage(f)}<div class="sq-actions">${F.button('forecast-baseline', '查看状态基准预测', { icon: 'book', small: true })}</div>${F.advanced(
        '配对日期、模型与对照规则',
        `${daily ? remoteState(daily, '') : ''}${table(
          ['日期', '成熟配对数', '加入因子 MSE', '状态基准 MSE'],
          (daily ? daily.items : (f.dailyLosses || []).slice(0, 25)).map(
            (x) =>
              `<tr><td>${e(d(x.date))}</td>${cell(x.observations, 0)}${cell(x.withFactorsMse, 8)}${cell(x.stateOnlyMse, 8)}</tr>`
          )
        )}<p class="sq-subtle">${daily ? '当前按需读取配对日期；完整产物保留 ' + fmt(remote.transport.collections?.dailyLosses?.total, 0) : '此处预览前 25 个日期；完整产物保留 ' + (f.dailyLosses?.length || 0)} 个日期及全部基准预测。</p>${JSONView({ method: f.method, sameEventAndMissingInputMask: f.sameEventAndMissingInputMask, hedgeFactorsAblated: f.hedgeFactorsAblated, significanceTested: f.significanceTested, causalAttribution: f.causalAttribution, profitabilityEstablished: f.profitabilityEstablished, baselineSelectedModel: f.baselineValidation?.selectedModel })}`
      )}`,
      { kicker: 'ACTUALLY COMPUTED FEATURE ABLATION' }
    );
  }
  function incrementCoverage(f) {
    if (!f.coverage)
      return F.note('此历史报告未返回完整对照覆盖表；配对数不能证明两个模型覆盖了相同记录。');
    const c = f.coverage;
    return `<h3>配对范围与未配对记录</h3>${table(
      ['记录口径', '加入因子模型', '状态基准'],
      [
        ['有效预测', c.fullValidRows, c.baselineValidRows],
        ['标签已成熟（含模型失效）', c.fullMatureRows, c.baselineMatureRows],
        ['同时有效且成熟', c.fullValidMatureRows, c.baselineValidMatureRows],
        ['有效但未配对', c.fullValidUnmatchedRows, c.baselineValidUnmatchedRows],
        ['模型不可用', c.fullUnavailableModelRows, c.baselineUnavailableModelRows],
        ['失效记录', c.fullInvalidRows, c.baselineInvalidRows]
      ].map(
        ([name, full, baseline]) => `<tr><td>${name}</td>${cell(full, 0)}${cell(baseline, 0)}</tr>`
      )
    )}<p class="sq-subtle">配对 ${fmt(c.matchedRows, 0)} 条 / ${fmt(c.matchedDates, 0)} 日期；${f.bothModelValidOnly === true ? '只比较双方均有效的预测' : '本报告未声明双方有效筛选'}。有效输出范围${f.outputValidityMasksIdentical === true ? '一致' : f.outputValidityMasksIdentical === false ? '不一致' : '未返回核对'}。原始未配对记录仍保留。这里没有单独的因子增量置信区间，不会相减两组基准区间制造显著性。</p>`;
  }
  function riskEvidence(r) {
    const x = r.execution || {},
      risk = x.riskAdapter;
    if (!risk) return '';
    const page = remote.enabled() ? remote.page('riskLedger', { filter: ui.riskFilter }) : null;
    const events = new Map();
    for (const [date, event] of Object.entries(page?.related?.riskEvents || {}))
      events.set(date, {
        exits: event.riskExitCount,
        pending: [],
        pendingCount: event.exitPendingCount
      });
    for (const t of r.trades || [])
      if (t.exitReason === 'risk_limit_exit') {
        const v = events.get(t.date) || { exits: 0, pending: [] };
        v.exits++;
        events.set(t.date, v);
      }
    for (const decision of x.decisions || [])
      if (decision.action === 'exit_pending') {
        const v = events.get(decision.date) || { exits: 0, pending: [] };
        v.pending.push(decision);
        events.set(decision.date, v);
      }
    const all = page ? page.items : x.ledger || [],
      rows = page
        ? all
        : all.filter(
            (row) =>
              ui.riskFilter === 'all' ||
              (ui.riskFilter === 'events' &&
                ((row.riskBreaches || []).length || events.has(row.date))) ||
              (ui.riskFilter === 'missing' &&
                ((row.unavailableRiskInputs?.factors || []).length ||
                  (row.unavailableRiskInputs?.volatilitySymbols || []).length))
          ),
      shown = page ? rows : rows.slice((ui.riskPage - 1) * 25, ui.riskPage * 25);
    return F.panel(
      '风险约束实际怎样生效',
      `<dl class="sq-key-values"><dt>仓位缩放</dt><dd>${risk.sizingMode === 'volatility_target' ? '历史协方差波动目标' : '固定名义敞口'}</dd><dt>总 / 净敞口上限</dt><dd>${pct(risk.grossExposure)} / ${pct(risk.netExposureLimit)}</dd><dt>单股目标上限</dt><dd>${pct(risk.maxWeight)}</dd><dt>波动目标 / 窗口</dt><dd>${risk.sizingMode === 'volatility_target' ? `${pct(risk.targetAnnualVolatility)} / ${risk.volatilityLookback} 日` : '本次未启用波动目标'}</dd><dt>因子暴露约束</dt><dd>${(risk.factorExposureLimits || []).map((z) => `${e(z.factorId)} ≤ ${fmt(z.maxAbsExposure, 2)}`).join('；') || '没有配置'}</dd></dl><div class="sq-report-controls"><label class="sq-field"><span>风险日期筛选</span><select id="sq-risk-filter">${Object.entries(
        {
          events: '风险违例 / 风险退出 / 受限退出',
          missing: '风险输入缺失日期',
          all: '全部风险日期'
        }
      )
        .map(
          ([key, label]) =>
            `<option value="${key}" ${ui.riskFilter === key ? 'selected' : ''}>${label}</option>`
        )
        .join('')}</select></label></div>${page ? remoteState(page, '') : ''}${table(
        ['日期 / 风险截止', '实际总 / 净敞口', '估计年化波动', '超限 / 退出', '核对'],
        shown.map((row) => {
          const event = events.get(row.date);
          return `<tr><td>${e(d(row.date))}<small>${e(row.riskInformationCutoff || '未返回')}</small></td><td>${pct(row.risk?.gross ?? row.grossExposure)}<small>${pct(row.risk?.net ?? row.netExposure)}</small></td><td>${pct(row.risk?.annualVolatility)}</td><td>${e(row.riskBreaches?.map(reasonLabel).join('；') || '收盘未记录超限')}<small>${event?.exits ? `风险退出 ${event.exits} 条交易腿` : '无风险退出成交'}</small><small>${event?.pendingCount || event?.pending.length ? `等待退出 ${event.pendingCount || event.pending.length} 项${event.pending.length ? '：' + [...new Set(event.pending.map((x) => reasonLabel(x.reason)))].join('；') : ''}` : ''}</small></td><td>${F.button('forecast-risk-detail', '风险明细', { id: row.date, small: true })}</td></tr>`;
        })
      )}${!page && !shown.length ? F.empty('这个筛选下没有风险日期', '可切换“全部风险日期”检查逐日敞口；零事件不会被补造成风险触发。') : ''}${page ? '' : pages(ui.riskPage, rows.length, 'forecast-risk-page')}<p class="sq-subtle">风险信息来自前一收盘。目标限额与实际价格漂移分别记录；存在风险退出条件时，T+1 和篮子可成交性仍可能限制即时成交。</p>${F.advanced('查看风险实现与完整字段名', JSONView(risk))}`
    );
  }
  function uncertainty(r) {
    const u = reportDiagnostics(r).aggregateUncertainty;
    if (!u) return F.note('本报告未返回依赖感知区间估计，不能据此展示统计显著性。');
    return F.panel(
      '历史平均预测损失改善区间',
      `${F.note('按观察日期整体进行区块重采样，以保留日期内截面相关和部分时间依赖。这是历史平均损失与偏差的区间，不是单股未来价格的置信带。此处偏差采用 实际 − 预期。')}${
        u.intervals
          ? table(
              ['统计量', '估计', '区间下界', '区间上界'],
              Object.entries({
                lossImprovement: '预测损失改善',
                entryBias: '入场偏差',
                exitBias: '未来目标偏差',
                remainingChangeBias: '剩余变化偏差'
              }).map(([key, label]) => {
                const x = u.intervals[key];
                return `<tr><td>${label}</td>${cell(x?.estimate, 7)}${cell(x?.lower, 7)}${cell(x?.upper, 7)}</tr>`;
              })
            )
          : F.note('区间不可用：样本不足或本次计算未产生有效区间。')
      }<p class="sq-subtle">状态 ${e(u.status)} · 观察日期 ${e(u.observedDates ?? '—')} · 预测记录 ${e(u.forecastRows ?? '—')} · 区块长度 ${e(u.blockLengthObservations ?? '—')} · 置信水平 ${pct(u.confidenceLevel)}</p>${F.advanced('区块敏感性与假设', JSONView({ blockSensitivity: u.blockSensitivity, assumptions: u.assumptions, limitations: u.limitations }))}`
    );
  }
  function targets(r) {
    const page = remote.enabled() ? remote.page('targets') : null;
    const definitions = r.forecasts.targetDefinitions || [];
    const shown = page ? page.items : definitions.slice((ui.page - 1) * 25, ui.page * 25);
    const body = table(
      ['定义 / 构造', '成员与数量', '形成区间', '单位 / 审计'],
      shown.map(
        (x) =>
          `<tr><td><code>${e(x.id)}</code><small>${e(typeof x.construction === 'object' ? JSON.stringify(x.construction) : x.construction)}</small></td><td>${x.symbols.map((symbol, n) => `${e(symbol)} × ${fmt(Array.isArray(x.quantities) ? x.quantities[n] : x.quantities?.[symbol], 6)}`).join('<br>')}</td><td>${e(d(x.formationStart))}<small>${e(d(x.formationEnd))}</small></td><td>${e(x.unit)}${F.button('forecast-target-detail', '定义证据', { id: x.id, small: true })}${page ? F.button('forecast-filter-target', '查看这个目标的预测', { id: x.id, label: x.symbols.join(' / '), small: true }) : ''}</td></tr>`
      )
    );
    return F.panel(
      '同一次预测，使用同一组固定数量',
      (page
        ? remoteState(page, body)
        : body + pages(ui.page, definitions.length, 'forecast-page')) +
        F.note(
          '两腿 OLS、PCA 与固定数量定义计量目标。未来状态由独立 F 模型预测。OLS 截距不是交易腿；PCA 投影不自动构成市场 beta 中性、协整或价格收敛证据。'
        )
    );
  }
  function loadFit(id) {
    const request = ++fitRequest;
    selectedFit = { id, loading: true, item: null, error: '' };
    Promise.resolve().then(() => remote.detail('modelFits', id)).then(response => {
      if (request !== fitRequest || !response) return;
      if (response.item?.id !== id) throw Error('拟合记录身份不一致。');
      selectedFit = { id, loading: false, item: response.item, error: '' };
    }).catch(error => {
      if (request === fitRequest) selectedFit = { id, loading: false, item: null, error: error.message };
    }).finally(() => { if (request === fitRequest) render(); });
  }
  function models(r) {
    const page = remote.enabled() ? remote.page('modelFits') : null;
    const rows = r.forecasts.modelFits || [];
    const shown = page ? page.items : rows.slice((ui.page - 1) * 25, ui.page * 25);
    if (!shown.length) return modelCandidates.view(r) + F.panel('F 模型', page ? remoteState(page, '', '没有拟合记录') : F.note('此报告没有拟合记录。'));
    if (!shown.some(fit => fit.id === ui.fitId)) ui.fitId = (shown.find(fit => fit.status !== 'invalid') || shown[0]).id;
    const brief = shown.find(fit => fit.id === ui.fitId);
    if (page && !brief.functionArtifact && selectedFit?.id !== ui.fitId) loadFit(ui.fitId);
    const fit = brief.functionArtifact || !page ? brief : selectedFit?.item;
    const picker = `<label class="sq-field sq-model-fit-picker"><span>拟合版本</span><select id="sq-model-fit">${shown.map(x => `<option value="${e(x.id)}" ${x.id === ui.fitId ? 'selected' : ''}>${e(d(x.fitDate))} · ${e(ESTIMATORS[x.estimator] || x.estimator || r.selection?.winner || 'F')} · ${e(x.id)}</option>`).join('')}</select></label>`;
    let model = fit ? renderSavedModel(C, F, fit, { output: ui.treeOutput, tree: ui.treeIndex }) : selectedFit?.error
      ? F.note(selectedFit.error, 'error') + F.button('forecast-model-retry', '重试读取 F', { small: true })
      : '<div class="sq-loading" role="status">正在读取模型与参数…</div>';
    if (fit?.functionArtifact) model += `<div class="sq-actions">${F.button('forecast-fit', '修改与试算 F', { id: fit.id, primary: true })}${F.button('mfe-library', '已保存的函数', { small: true })}</div>`;
    const adopted = F.panel(r.forecasts?.diagnostics?.modelSearch ? '已采用的滚动模型' : 'F(X)', model + (page ? remotePages(page) : pages(ui.page, rows.length, 'forecast-page')), { actions: picker, className: 'sq-model-primary' });
    return modelCandidates.view(r) + (r.forecasts?.diagnostics?.modelSearch ? F.advanced('已采用的滚动模型', adopted) : adopted) +
      F.panel('拟合数据', table(['版本', '训练日期', '训练行数', '最晚标签成熟', '信息截止'], shown.map(x => `<tr><td>${e(d(x.fitDate))}</td><td>${e(d(x.trainStart))} — ${e(d(x.trainEnd))}</td>${cell(x.trainRows, 0)}<td>${e(d(x.labelEndMax))}</td><td>${e(d(x.informationCutoff))}</td></tr>`)) + F.button('forecast-tab', '查看预测与实际值', { id: 'forecasts', small: true }));
  }
  function execution(r) {
    if (factorOnlyReport(r))
      return F.panel(
        financialReport() ? '仅预测的财务研究' : marketReport(r) ? '仅预测的市场研究' : '因子研究边界',
        F.note(
          (financialReport() ? '此产物检验财务状态对未来价格的预测' : '此产物检验冻结票池的未来状态') + '，没有生成仓位、交易或净值。' + (financialReport() || marketReport(r) ? '交易执行与执行重放尚未开放。' : '策略研究与交易执行将在独立模块接入。')
        ) + '<p>预测误差与历史损失改善不等于可交易收益。</p>'
      );
    const x = r.execution || {},
      m = r.metrics;
    let body = F.note(
      '本次是完整的纯预测研究：未创建持仓、交易或净值。预测诊断与后续策略研究分开保存。'
    );
    if (x.enabled && m) {
      const tradesPage = remote.enabled() ? remote.page('trades') : null;
      const chart = remote.enabled() ? remote.chart() : null;
      const chartBody = chart
        ? chart.error
          ? F.note(chart.error, 'error') +
            F.button('forecast-remote-retry', '重试曲线', {
              id: chart.key,
              small: true
            })
          : !chart.loaded
            ? '<div class="sq-loading" role="status">正在读取有界净值曲线…</div>'
            : C.equityChart(chart.value.points) +
              `<p class="sq-subtle">显示 ${chart.value.points.length} / ${chart.value.totalPoints} 个曲线点；采样方式 ${e(chart.value.samplingMethod)}。完整收益和回撤使用原始账本统计，未从预览曲线重算。</p>`
        : C.equityChart(r.equity || []);
      body = `<div class="sq-report-stats">${stat('执行净收益', pct(m.totalReturn), '实际模拟成交后，扣除全部声明成本')}${stat('最大回撤', pct(m.maxDrawdown), '现金加有符号持仓的净值')}${stat('交易腿数', fmt(m.tradeCount, 0), '每笔成交引用 forecastId')}${stat('累计费用', fmt(m.totalCosts), '佣金、滑点、税费、过户及借券')}</div>${F.panel('独立执行净值', chartBody + `<p class="sq-subtle">预测误差不是现金 PnL。净值来自实际模拟的成交、费用和持仓估值。</p>`)}${F.panel(
        '交易与预测引用',
        (tradesPage ? remoteState(tradesPage, '') : '') +
          table(
            ['成交日 / 标的', '方向 / 数量', '成交价 / 名义额', '费用', '预测 / 退出原因'],
            (tradesPage
              ? tradesPage.items
              : (r.trades || []).slice((ui.tradePage - 1) * 25, ui.tradePage * 25)
            ).map(
              (t) =>
                `<tr><td>${e(d(t.date))}<small>${e(t.symbol)}</small></td><td>${e(t.side)}<small>${fmt(t.signedQuantity, 4)}</small></td><td>${fmt(t.price, 4)}<small>${fmt(t.notional, 2)}</small></td>${cell(t.cost, 2)}<td>${F.button('forecast-row', '对应预测', { id: t.forecastId, small: true })}<small>${e(reasonLabel(t.exitReason))}</small></td></tr>`
            )
          ) +
          (tradesPage ? '' : pages(ui.tradePage, r.trades?.length || 0, 'forecast-trade-page'))
      )}${remote.enabled() ? remoteRawCollection('decisions', '执行决策与未成交原因') : F.advanced('执行决策、未成交原因与费用合计', JSONView({ costBreakdown: m.costBreakdown, decisions: (x.decisions || []).slice(0, 200), previewLimit: 200, totalDecisions: x.decisions?.length, completeRecord: '完整报告 JSON 保留全部决策' }))}`;
    }
    return `${body}${riskEvidence(r)}${F.note('策略研究和交易执行将在独立模块接入；此处只读取已保存的执行记录。')}${F.panel('执行边界', `<dl class="sq-key-values"><dt>预测产物</dt><dd><code>${e(r.forecasts.artifactId)}</code></dd><dt>此次重新拟合</dt><dd>${r.research?.predictionRefitPerformed === false ? '否，复用已冻结预测' : r.research?.predictionRefitPerformed === true ? '本次生成了新的预测' : '未返回证据'}</dd><dt>借券库存</dt><dd>${x.shortInventoryVerified ? '已验证' : '理论假设，未核验实际券源'}</dd><dt>到期终止</dt><dd>不能任意顺延入场；到期不可成交退出按实际可成交时点处理。</dd><dt>成交单位</dt><dd>${e(x.unit === 'fractional_adjusted_research_units' ? '可分割的复权研究单位；未按交易所整手撮合' : x.unit || '预测研究尚未执行')}</dd></dl>`)}`;
  }
  function provenance(r) {
    const ref = financialSourceRef();
    const marketSource = r.provenance?.marketSource, marketDownload = marketDatasetDownload(marketSource?.marketDatasetRef);
    const source = ref
      ? F.panel(
          '冻结数据集来源',
          `<p>原始行情快照、明确子范围、财务输入与准备、日历授权均由独立数据集闭包保存。用户声明单位仍未核验，供应商原始发布版本和修订时点未认证。</p><a class="sq-button" href="${e(datasetLocation(ref).page)}">查看来源与实际覆盖</a>${F.advanced('数据集身份', `<code>${e(ref.datasetRoot)}</code>`)}`
        )
      : marketSource ? F.panel('冻结行情来源',
          '<p>本报告固定引用独立保存的行情输入与来源证据。完整核验需同时保留市场预测结果包和行情来源包。</p>' +
          (marketDownload ? `<a class="sq-button" href="${e(marketDownload)}" download>下载完整行情来源包</a>` : F.note('未返回有效的完整行情来源引用，不能补猜来源包。', 'warning')) +
          F.advanced('本报告保存的行情来源身份', JSONView(marketSource))) : '';
    return (
      (ui.downloads || '') + source + renderContextSources(C, F, r.provenance) +
      (r.warnings?.length ? F.advanced('研究状态与限制', `<ul>${r.warnings.map(x => `<li>${e(typeof x === 'string' ? x : x.message || JSON.stringify(x))}</li>`).join('')}</ul>`) : '') +
      F.panel(
        '数据与研究身份',
        `<dl class="sq-key-values"><dt>引擎版本</dt><dd>${e(r.engineVersion)}</dd><dt>数据指纹</dt><dd><code>${e(r.forecasts.dataFingerprint)}</code></dd><dt>预测配置指纹</dt><dd><code>${e(r.forecasts.predictionConfigHash)}</code></dd><dt>完整预测记录</dt><dd>${r.forecasts.totalRows} · ${remote.enabled() ? '分页读取，计算完整度单独列明' : '截断 ' + (r.forecasts.truncated ? '是' : '否')}</dd>${remote.enabled() ? `<dt>传输 bundleId</dt><dd><code>${e(remote.transport.bundleId)}</code></dd><dt>已提交完整产物</dt><dd>${remote.transport.complete === true ? '是；页面仅按需读取，不改变原预测身份' : '尚未确认'}</dd>` : ''}</dl>${F.advanced('完整配置', JSONView(r.strategy))}${F.advanced('供应商、日历与字段证据', JSONView(r.provenance), true)}`
      )
    );
  }
  function targetDetail(def) {
    if (!def) return F.note('没有返回这个目标的数量定义。', 'warning');
    return `<div class="sq-report-detail"><h3>固定的篮子数量</h3><p class="sq-subtle">当前状态、预期入场、未来目标及实现标签都使用这组数量。数量为负表示该目标中的反向腿。</p>${table(
      ['标的', '固定数量'],
      def.symbols.map(
        (symbol, n) =>
          `<tr><td>${e(symbol)}</td>${cell(Array.isArray(def.quantities) ? def.quantities[n] : def.quantities?.[symbol], 7)}</tr>`
      )
    )}<dl class="sq-key-values"><dt>构造方式</dt><dd>${e({ pair_ols: '两腿价格 OLS', pca_residual: '历史 PCA 投影', fixed: '明确的固定数量', single_asset: '单资产 · 数量 1' }[def.construction] || def.construction)}</dd><dt>形成区间</dt><dd>${e(d(def.formationStart))} — ${e(d(def.formationEnd))}</dd><dt>计量单位</dt><dd>${def.kind === 'asset_price' ? '复权研究价格 / 元' : '复权研究篮子价值 / 元'}</dd></dl>${F.advanced('对冲构造与身份审计', JSONView({ id: def.id, unit: def.unit, hedgeAudit: def.hedgeAudit }))}</div>`;
  }
  function rowDetail(row, r) {
    const definition = r.forecasts.targetDefinitions?.find((x) => x.id === row.targetId);
    const reason =
      {
        target_outside_available_calendar: '目标时间超出已返回的交易日历，因此不允许交易。',
        model_unavailable: '当前模型没有生成有限预测值。',
        missing_input: '输入状态缺失，未生成可执行预测。'
      }[row.invalidReason] || row.invalidReason;
    return `<div class="sq-report-detail">${F.note(row.status === 'valid' ? `有效预测；${row.labelMaturedAt ? '未来标签已经成熟，可以核对预测误差。' : '未来标签尚未成熟，实际状态与误差暂缺。'}` : `此预测失效：${reason || '运行时条件不满足'}。`, 'valid' === row.status ? 'info' : 'warning')}<div class="sq-timeline"><div><span>${e(d(row.date))} · 收盘后</span><strong>观察与信息截止</strong><small>仅使用该时点可知的数据</small></div><div><span>${e(d(row.entryDate))} · 开盘</span><strong>预期入场时点</strong><small>不可成交则不顺延入场</small></div><div><span>${e(d(row.targetDate))} · 开盘</span><strong>未来目标时点</strong><small>入场后 ${e(row.horizonSessions)} 个交易日</small></div></div>${table(
      ['预测量', '数值', '含义'],
      [
        ['当前状态 P', row.currentState, '观察收盘时，固定数量的目标价值'],
        ['预期入场', row.expectedEntry, '模型预测下一开盘的目标状态'],
        ['预期未来 V', row.expectedFuture, '模型预测声明期限后的目标状态'],
        ['e = P − V', row.edgeGap, '当前状态相对预期未来的差距'],
        ['预期剩余变化', row.expectedGrossPnl, '预期未来 − 预期入场，尚未扣除费用'],
        ['预期剩余变化 / bps', row.expectedGrossBps, '按观察时已知的正名义尺度归一'],
        ['实际入场状态', row.realizedEntry, '行情标签，不代表必然获得成交'],
        ['实际未来状态', row.realizedFuture, '标签成熟后观测到的未来值'],
        ['预测误差', row.forecastError, '实际未来 − 预期未来']
      ].map(([name, value, help]) => `<tr><td>${name}</td>${cell(value, 6)}<td>${help}</td></tr>`)
    )}${F.note('没有经校准的单笔未来价格置信带。预测数值是条件估计，不是承诺的成交价或收益。')}${targetDetail(definition)}${F.advanced('预测身份与模型引用', `<dl class="sq-key-values"><dt>forecastId</dt><dd><code>${e(row.forecastId)}</code></dd><dt>modelFitId</dt><dd><code>${e(row.modelFitId)}</code></dd><dt>信息截止</dt><dd>${e(row.informationCutoff)}</dd><dt>标签成熟时点</dt><dd>${e(d(row.labelMaturedAt))}</dd></dl>`)}${F.advanced('查看原始记录', JSONView(row))}</div>`;
  }
  function fitDetail(fit) {
    if (!fit) return F.note('没有找到这个拟合记录。', 'warning');
    const label = fit.functionArtifact?.featureConstruction ? createFeatureLabeler({ factors: fit.functionArtifact.featureConstruction.factors, catalog: C.state.catalog?.factors || [] }) : reportFeatureLabeler(ui.result, C.state.catalog?.factors || []);
    return `<div class="sq-report-detail">${functionEditor.render(fit, { runId: C.state.runId, bundleId: remote.transport?.bundleId || null, modelFitId: fit.id })}${F.advanced('拟合审计', `${fit.status === 'invalid' ? F.note(fit.invalidReason === 'MISSING_MODEL_DATA' ? '这个时点缺少满足条件的训练输入，模型未能拟合。未来标签仍可能已经成熟，不把模型失败当作标签未到期。' : '本次拟合不可用：' + (fit.invalidReason || '未返回原因'), 'warning') : ''}<dl class="sq-key-values"><dt>估计器</dt><dd>${e(ESTIMATORS[fit.estimator] || fit.estimator)}</dd><dt>实际参数</dt><dd><code>${e(JSON.stringify(fit.params || {}))}</code></dd><dt>拟合时点</dt><dd>${e(d(fit.fitDate))}</dd><dt>训练观察日期</dt><dd>${e(d(fit.trainStart))} — ${e(d(fit.trainEnd))}</dd><dt>最晚标签成熟</dt><dd>${e(d(fit.labelEndMax))}</dd><dt>训练规模</dt><dd>${fmt(fit.trainDates, 0)} 日期 / ${fmt(fit.trainRows, 0)} 行</dd><dt>保留输入</dt><dd>${e((fit.featureNames || []).map(name => label(name)).join('、'))}</dd></dl>${
      fit.stateEffects?.length
        ? table(
            ['状态输入', '预测剩余变化效应', '是否观察到负向效应'],
            fit.stateEffects.map(
              (x) =>
                `<tr><td>${e(label(x.feature))}</td>${cell(x.remainingChangeEffect, 7)}<td>${x.negativeEffectObserved ? '是' : '否'}</td></tr>`
            )
          ) + F.note('这是训练状态在四分位区间内扰动的条件效应，不是因果归因或均值回归证明。')
        : ''
    }${F.advanced('去相关与输入剔除', JSONView(fit.decorrelation))}${F.advanced('查看原始拟合记录', JSONView(fit))}`)}</div>`;
  }
  function remoteBaseline(r) {
    const page = remote.page('baselineRows');
    return F.panel(
      '模型自带状态 · 实际基准预测',
      F.note(
        '这是移除额外预测/事件因子后实际重跑的基准；目标数量和标签保持一致。按需逐页读取完整基准，不隐藏失效记录。'
      ) +
        remoteState(
          page,
          table(
            ['日期 / 目标', '当前状态', '预期入场', '预期未来', '误差'],
            page.items.map(
              (x) =>
                `<tr><td>${e(d(x.date))}<small>${e(pageTargetName(x.targetId, page, r))}</small></td>${cell(x.currentState)}${cell(x.expectedEntry)}${cell(x.expectedFuture)}${cell(x.forecastError)}</tr>`
            )
          )
        )
    );
  }
  let detailRequest = 0;
  async function remoteDetail(action, id, r) {
    const collection = {
      'forecast-row': 'forecasts',
      'forecast-target-detail': 'targets',
      'forecast-fit': 'modelFits',
      'forecast-risk-detail': 'riskLedger',
      'forecast-trial': 'finalTrials'
    }[action];
    const title = {
      forecasts: '预测记录 · P / V / e',
      targets: '冻结目标定义',
      modelFits: '模型拟合审计',
      riskLedger: '逐日风险证据',
      finalTrials: '候选模型验证证据'
    }[collection];
    const request = ++detailRequest;
    const marker = `<div data-report-detail="${request}">`;
    openModal(
      title,
      marker + '<div class="sq-loading" role="status">正在读取这条记录的证据…</div></div>',
      true
    );
    const stillOpen = () => document.querySelector(`[data-report-detail="${request}"]`);
    try {
      const response = await remote.detail(collection, id);
      if (!response || !stillOpen()) return;
      let body;
      if (collection === 'forecasts') {
        let definitions = response.related?.targets || [];
        if (
          !definitions.some((x) => x.id === response.item?.targetId) &&
          response.item?.targetId &&
          response.item.targetId !== 'unavailable'
        ) {
          const target = await remote.detail('targets', response.item.targetId);
          if (!target || !stillOpen()) return;
          definitions = [target.item];
        }
        body = rowDetail(response.item, {
          ...r,
          forecasts: { ...r.forecasts, targetDefinitions: definitions }
        });
      } else if (collection === 'targets') body = targetDetail(response.item);
      else if (collection === 'modelFits') body = fitDetail(response.item);
      else if (collection === 'riskLedger') body = riskDetail(response.item, r);
      else body = JSONView(response.item);
      if (stillOpen()) openModal(title, marker + body + '</div>', true);
    } catch (error) {
      if (stillOpen())
        openModal(
          title,
          marker +
            F.note(error.message, 'error') +
            F.button(action, '重试这条记录', { id, small: true }) +
            '</div>',
          true
        );
    }
  }
  function riskDetail(row, r) {
    return `${table(
      ['因子', '实际 z-score 暴露', '声明上限'],
      Object.entries(row.risk?.factorExposures || {}).map(
        ([id, value]) =>
          `<tr><td>${e(id)}</td>${cell(value)}${cell(r.execution?.riskAdapter?.factorExposureLimits?.find((x) => x.factorId === id)?.maxAbsExposure)}</tr>`
      )
    )}<p class="sq-subtle">${e(row.riskBreaches?.map(reasonLabel).join('；') || '收盘未记录超限')}</p>${F.advanced('查看原始日账本', JSONView(row))}`;
  }
  async function handle(el) {
    if (el.dataset.sq?.startsWith('mfe-')) return functionEditor.handle(el);
    const action = el.dataset.sq;
    if (!action?.startsWith('forecast-')) return false;
    const r = ui.result;
    if (!r) return true;
    if (await modelCandidates.handle(el)) return true;
    if (action === 'forecast-model-retry') { loadFit(ui.fitId); render(); return true; }
    if (action === 'forecast-remote-page') {
      remote.move(el.dataset.id, el.dataset.direction);
      return true;
    }
    if (action === 'forecast-remote-retry') {
      remote.retry(el.dataset.id);
      return true;
    }
    if (
      remote.enabled() &&
      [
        'forecast-row',
        'forecast-target-detail',
        'forecast-fit',
        'forecast-risk-detail',
        'forecast-trial'
      ].includes(action)
    ) {
      await remoteDetail(action, el.dataset.id, r);
      return true;
    }
    if (action === 'forecast-baseline' && remote.enabled()) {
      ui.baselineOpen = !ui.baselineOpen;
      render();
      return true;
    }
    if (action === 'forecast-clear-target') {
      ui.target = '';
      ui.targetLabel = '';
      render();
      return true;
    }
    if (action === 'forecast-filter-target') {
      ui.target = el.dataset.id;
      ui.targetLabel = el.dataset.label || '';
      ui.query = '';
      ui.scope = 'all';
      ui.tab = 'forecasts';
      render();
      return true;
    }
    if (action === 'forecast-tab') {
      ui.tab = el.dataset.id;
      ui.page = 1;
      render();
    }
    if (action === 'forecast-page') {
      ui.page = Number(el.dataset.page);
      render();
    }
    if (action === 'forecast-risk-detail') {
      const row = r.execution?.ledger?.find((x) => x.date === el.dataset.id);
      if (row)
        openModal(
          '逐日风险证据',
          `${table(
            ['因子', '实际 z-score 暴露', '声明上限'],
            Object.entries(row.risk?.factorExposures || {}).map(
              ([id, value]) =>
                `<tr><td>${e(id)}</td>${cell(value)}${cell(r.execution.riskAdapter?.factorExposureLimits?.find((x) => x.factorId === id)?.maxAbsExposure)}</tr>`
            )
          )}<p class="sq-subtle">${e(row.riskBreaches?.map(reasonLabel).join('；') || '收盘未记录超限')}</p>${F.advanced('查看原始日账本', JSONView(row))}`,
          true
        );
    }
    if (action === 'forecast-risk-page') {
      ui.riskPage = Number(el.dataset.page);
      render();
    }
    if (action === 'forecast-baseline') {
      const rows = reportDiagnostics(r).factorIncrement?.baselineRows || [];
      openModal(
        '模型自带状态 · 实际基准预测',
        `${F.note('这是移除额外预测/事件因子后实际重跑的基准；目标数量和标签保持一致。此处预览最后 25 条，完整产物保留全部基准记录。')}${table(
          ['日期 / 目标', '当前状态', '预期入场', '预期未来', '误差'],
          rows
            .slice(-25)
            .map(
              (x) =>
                `<tr><td>${e(d(x.date))}<small>${e(targetName(x.targetId, r))}</small></td>${cell(x.currentState)}${cell(x.expectedEntry)}${cell(x.expectedFuture)}${cell(x.forecastError)}</tr>`
            )
        )}`,
        true
      );
    }
    if (action === 'forecast-trade-page') {
      ui.tradePage = Number(el.dataset.page);
      render();
    }
    if (action === 'forecast-row') {
      const row = r.forecasts.rows.find((x) => x.forecastId === el.dataset.id);
      if (row) openModal('预测记录 · P / V / e', rowDetail(row, r), true);
    }
    if (action === 'forecast-target-detail') {
      openModal(
        '冻结目标定义',
        targetDetail(r.forecasts.targetDefinitions.find((x) => x.id === el.dataset.id)),
        true
      );
    }
    if (action === 'forecast-fit')
      openModal(
        '模型拟合审计',
        fitDetail(r.forecasts.modelFits.find((x) => x.id === el.dataset.id)),
        true
      );
    if (action === 'forecast-trial')
      openModal(
        '候选模型验证证据',
        JSONView((reportDiagnostics(r).finalTrials || []).find((x) => x.id === el.dataset.id)),
        true
      );
    if (action === 'forecast-execute') toast('策略执行尚未在本工作区开放。');
    return true;
  }
  let searchTimer;
  function onInput(el) {
    functionEditor.onInput(el);
    const remoteFilters = {
      'sq-forecast-target': 'target',
      'sq-forecast-date-from': 'dateFrom',
      'sq-forecast-date-to': 'dateTo'
    };
    if (remoteFilters[el.id]) ui[remoteFilters[el.id]] = el.value;
    if (el.id === 'sq-forecast-search') {
      ui.query = el.value;
      ui.page = 1;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(render, 180);
    }
  }
  function onChange(el) {
    functionEditor.onInput(el);
    if (modelCandidates.onChange(el)) return;
    if (el.id === 'sq-model-fit') { ui.fitId = el.value; ui.treeIndex = 0; render(); return; }
    if (el.id === 'sq-model-tree-output') { ui.treeOutput = Number(el.value); ui.treeIndex = 0; render(); return; }
    if (el.id === 'sq-model-tree-index') { ui.treeIndex = Number(el.value); render(); return; }
    if (el.id === 'sq-risk-filter') {
      ui.riskFilter = el.value;
      ui.riskPage = 1;
      render();
    }
    const mapping = {
      'sq-forecast-scope': 'scope',
      'sq-forecast-status': 'status',
      'sq-forecast-target': 'target',
      'sq-forecast-date-from': 'dateFrom',
      'sq-forecast-date-to': 'dateTo'
    };
    if (mapping[el.id]) {
      ui[mapping[el.id]] = el.value;
      ui.page = 1;
      render();
    }
    onInput(el);
  }
  return { render: renderReport, handle, onInput, onChange, ui, remote };
}
