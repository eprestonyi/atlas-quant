import { reportFeatureLabeler, constructedFeatureLabel } from './feature-labels.js';
import { jointFrequencyViews } from './joint-table.js';
import { factorDistributionCharts, coefficientChart } from './report-charts.js';
// Descriptive factor evidence from frozen artifacts; never estimates missing statistics in the browser.
export function createFactorDiagnostics(C, F, { remote, remoteState, table }) {
  const { esc: e, fmt, pct } = C;
  const value = x => fmt(x, 5);
  const cell = x => `<td class="numeric">${value(x)}</td>`;
  const raw = x => `<pre class="sq-report-code">${e(JSON.stringify(x, null, 2))}</pre>`;
  const unavailable = reason => ({
    fewer_than_three_finite_targets_or_constant_cross_section: '少于三个有效标的，或横截面取值不变',
    insufficient_or_constant_pairs: '有效配对不足，或取值不变',
    no_development_values_for_bins: '开发期没有可用数值，无法固定分箱',
    unavailable: '不可计算',
    global_factor_identical_within_date: '全局因子在同一日对所有股票相同，横截面 IC 不适用'
  })[reason] || '未提供可计算的统计值';
  const status = x => x?.status === 'available' || x?.status === 'ok' ? '已计算' : unavailable(x?.unavailableReason || x?.status);
  let label = key => key;
  function featureDetail(x) {
    const ts = x.timeSeriesCorrelation || {}, fit = x.descriptiveFit || {};
    const fitSample = fit.fitSample === 'reported_terminal_pairs' ? '报告区间内的有效成熟配对' : '未提供样本范围';
    return F.advanced(`${label(x.name, x.definition)} · 定义与统计口径`,
      `<dl class="sq-key-values"><dt>输入标识</dt><dd><code>${e(x.name)}</code></dd><dt>定义</dt><dd>${e(x.definition?.expression || x.name)}</dd><dt>观察缺失</dt><dd>${fmt(x.missing?.count, 0)} / ${fmt(x.missing?.total, 0)} · ${pct(x.missing?.fraction)}</dd><dt>横截面 IC</dt><dd>${e(status(x.ic))} · ${fmt(x.ic?.dates, 0)} 个有效日期</dd><dt>Rank IC</dt><dd>${e(status(x.rankIc))} · ${fmt(x.rankIc?.dates, 0)} 个有效日期</dd><dt>描述性单变量 R²</dt><dd>${value(fit.rSquared)} · ${e(fitSample)}</dd><dt>显著性</dt><dd>未提供经依赖调整的 p 值或系数标准误</dd></dl>` +
      F.note('单变量拟合使用报告样本，只是描述关系；不是因子加入 F 后的样本外增量，也不是因果解释。单一标的的时间序列相关不称为横截面 IC。') +
      (ts.perTarget?.length ? table(['目标', '样本数', '时序 Pearson', '时序 Spearman'], ts.perTarget.map(t => `<tr><td>${e(t.targetId)}</td><td>${fmt(t.n, 0)}</td>${cell(t.pearson)}${cell(t.spearman)}</tr>`)) + `<p class="sq-subtle">目标总数 ${fmt(ts.totalTargets, 0)}；未展示 ${fmt(ts.omittedTargets, 0)}。各标的自身的时序相关，与横截面 IC 分开统计。</p>` : F.note('未返回可用的逐标的时间序列相关。')) +
      F.advanced('原始统计记录', raw(x)));
  }
  function features(rows) {
    return factorDistributionCharts(rows,label,e) + table(['输入因子', '有效 / 缺失', '均值 / 标准差', '中位数 / IQR', '横截面 IC / Rank IC', '未来变化 R²', '同期变化 R²'], rows.map(x => {
      const d = x.distribution || {};
      return `<tr><td>${e(label(x.name, x.definition))}<small>${x.kind === 'factor' ? '研究因子' : '派生状态'}</small></td><td>${fmt(d.count, 0)} / ${fmt(x.missing?.count, 0)}</td><td><span class="sq-stat-value">${value(d.mean)}</span><small>标准差 ${value(d.std)}</small></td><td><span class="sq-stat-value">${value(d.median)}</span><small>Q1 ${value(d.q25)}<br>Q3 ${value(d.q75)}</small></td><td>${x.ic?.unavailableReason==='global_factor_identical_within_date'?'全局因子 · 不适用':`<span class="sq-stat-value">${value(x.ic?.mean)} / ${value(x.rankIc?.mean)}</span><small>${fmt(x.ic?.dates, 0)} 个有效横截面</small>`}</td>${cell(x.descriptiveFit?.rSquared)}${cell(x.contemporaneousFit?.pooled?.rSquared)}</tr>`;
    })) + rows.map(featureDetail).join('');
  }
  function interval(edges, index) {
    const lo = edges?.[index], hi = edges?.[index + 1];
    return `${index === 0 && lo === null ? '−∞' : value(lo)} 至 ${hi === null ? '+∞' : value(hi)}`;
  }
  function joint(x, index = 0) {
    const views = jointFrequencyViews(x);
    if (!views) return F.advanced(`${label(x.x)} × ${label(x.y)}`, F.note(`联合分布不可用：${unavailable(x.reason || x.status)}`) + F.advanced('联合输入原始记录', raw(x)));
    const counts = x.counts, columns = counts[0].length;
    const headers = ['X 分箱 / Y 分箱', ...Array.from({length: columns}, (_, n) => interval(x.yEdges, n))];
    const cells = counts.map((row, ri) => `<tr><th scope="row">${e(interval(x.xEdges, ri))}</th>${row.map((n, ci) => `<td class="numeric sq-report-stat-heat" style="--heat:${x.sampleCount > 0 ? Math.min(.65, n / x.sampleCount * 2) : 0}">${fmt(n, 0)}<small>${pct(x.probabilities?.[ri]?.[ci])}</small></td>`).join('')}<td class="numeric">${fmt(views.rowCounts[ri], 0)}<small>${pct(views.rowProbabilities[ri])}</small></td></tr>`);
    cells.push(`<tr><th scope="row">Y 边际频数 / 概率</th>${views.columnCounts.map((n, i) => `<td class="numeric">${fmt(n, 0)}<small>${pct(views.columnProbabilities[i])}</small></td>`).join('')}<td class="numeric">${fmt(x.sampleCount, 0)}</td></tr>`);
    const conditional = values => table(headers, values.map((row, ri) => `<tr><th scope="row">${e(interval(x.xEdges, ri))}</th>${row.map(p => `<td class="numeric">${pct(p)}</td>`).join('')}</tr>`));
    return F.advanced(`${label(x.x)} × ${label(x.y)} · ${fmt(x.sampleCount, 0)} 对观测`,
      `<p>X：${e(label(x.x))}；Y：${e(label(x.y))}。每格为联合频数与经验概率，末行及末列为边际分布。</p>` + table([...headers, 'X 边际频数 / 概率'], cells) +
      F.advanced('条件分布 P(Y 分箱 | X 分箱)', '<p>固定一行 X 分箱，查看该行内 Y 的经验分布。非空行概率之和为 1。</p>' + conditional(views.yGivenX)) +
      F.advanced('条件分布 P(X 分箱 | Y 分箱)', '<p>固定一列 Y 分箱，查看该列内 X 的经验分布。非空列概率之和为 1。</p>' + conditional(views.xGivenY)) +
      '<p class="sq-subtle">边际和条件概率仅由这份冻结频数表相除得到；空的条件分箱显示 —，不作平滑或补值。</p>' +
      `<dl class="sq-key-values"><dt>缺失配对</dt><dd>${fmt(x.missingPairCount, 0)}</dd><dt>观察区间</dt><dd>${e(C.dateText(x.firstDate))} — ${e(C.dateText(x.lastDate))}</dd><dt>分箱来源</dt><dd>${x.edgeSource === 'pre_terminal_development_feature_quantiles' ? '仅用报告期之前的开发样本固定分位数边界' : '详见原始记录'}</dd><dt>边界约定</dt><dd>${x.intervalConvention === '[left,right); exterior null means -infinity/+infinity; final right closed' ? '左闭右开，最后一档包含右端点；∞ 表示无界' : '详见原始记录'}</dd></dl>` +
      F.advanced('统计口径', F.note('这是该报告样本的经验联合分布，不是已知的总体分布，也不意味着观测相互独立。')) + F.advanced('分箱与概率原始记录', raw(x)), index === 0);
  }
  function matrix(title, names, values, integer = false) {
    if (!Array.isArray(values)) return F.note(`${title}尚未返回。`);
    return F.advanced(title, table(['输入', ...names.map(name => label(name))], values.map((row, ri) => `<tr><th scope="row">${e(label(names[ri]))}</th>${row.map(n => `<td class="numeric ${integer || !Number.isFinite(n) ? '' : 'sq-report-stat-heat' + (n < 0 ? ' negative' : '')}" style="--heat:${integer || !Number.isFinite(n) ? 0 : Math.min(.55, Math.abs(n) * .55)}">${fmt(n, integer ? 0 : 4)}</td>`).join('')}</tr>`)), true);
  }
  function render(r, view = 'all') {
    const rawLabel = reportFeatureLabeler(r, C.state?.catalog?.factors || []);
    let featureDefinitions = [];
    label = (name, definition) => constructedFeatureLabel(rawLabel(name, definition), featureDefinitions.find(x=>x.name===name)?.inputConstruction);
    const meta = r.forecasts?.factorResearch, d = meta?.diagnostics;
    if (!d) return F.panel('因子诊断', F.note('此产物没有因子研究诊断记录。旧结果不会补造 IC、拟合度或联合分布。'));
    const featurePage = remote.enabled() && ['all', 'features', 'exposures'].includes(view) ? remote.page('factorFeatures') : null;
    const jointPage = remote.enabled() && ['all', 'joints'].includes(view) ? remote.page('factorJointDistributions') : null;
    const featureRows = featurePage ? featurePage.items : d.features || [];
    featureDefinitions = featureRows;
    const pairs = jointPage ? jointPage.items : d.dependence?.jointDistributions || [];
    const dep = d.dependence || {};
    const significance = d.significance?.reason === 'overlapping_labels_and_cross_sectional_temporal_dependence_not_adjusted_for_factor_tests'
      ? '未校正标签重叠及横截面、时序依赖，暂不报告因子显著性。'
      : d.significance?.reason || '当前未提供依赖感知的统计显著性；重叠标签不视为独立样本。';
    const pairSelection = dep.jointPairSelection === 'declared_factor_order_then_derived_states_no_outcome_ranking'
      ? '按预先声明的因子顺序，再列内置状态；不按结果挑选'
      : dep.jointPairSelection || '未提供';
    const sample = F.advanced('样本与统计口径', `<dl class="sq-key-values"><dt>诊断区间</dt><dd>${e(C.dateText(d.firstDate))} — ${e(C.dateText(d.lastDate))}</dd><dt>成熟有效观察</dt><dd>${fmt(d.maturedValidOrigins, 0)} / ${fmt(d.origins, 0)}</dd><dt>标签定义</dt><dd>${e(d.targetDefinition || d.target || '未提供')}</dd><dt>用途</dt><dd>模型选择后计算的报告诊断，未用于本次候选选择</dd></dl>${F.note(significance)}${F.advanced('显著性口径原始记录', raw(d.significance))}`);
    const featureView = () => F.panel('因子分布与关联', featurePage ? remoteState(featurePage, features(featureRows), '没有因子统计记录') : features(featureRows), { description: '' });
    const exposureView = () => F.panel('同期关联与未来预测', table(['输入因子', '同期变化 R²', '未来变化 R²', '同期样本'], featureRows.map(x=>`<tr><td>${e(label(x.name,x.definition))}</td>${cell(x.contemporaneousFit?.pooled?.rSquared)}${cell(x.descriptiveFit?.rSquared)}<td>${fmt(x.contemporaneousFit?.pooled?.n,0)}</td></tr>`)) + featureRows.map(x=>{const c=x.contemporaneousFit;if(!c)return '';return F.advanced(label(x.name,x.definition),coefficientChart({esc:e,labels:(c.perTarget||[]).map(v=>v.targetId),values:(c.perTarget||[]).map(v=>v.rSquared),title:'逐标的同期单变量 R²'})+table(['目标','同期斜率','同期截距','同期 R²','样本'],(c.perTarget||[]).map(t=>`<tr><td>${e(t.targetId)}</td>${cell(t.slope)}${cell(t.intercept)}${cell(t.rSquared)}<td>${fmt(t.n,0)}</td></tr>`))+F.advanced('定义',raw(c)));}).join('')+F.note('同期列比较因子与当期已观测变化；未来列比较因子与声明期限后的变化。两者的标签和用途不同，均不是因果结论。'));
    const correlationView = () => F.panel('输入间相关与协方差', matrix('相关矩阵', dep.featureNames || [], dep.correlation) + matrix('协方差矩阵', dep.featureNames || [], dep.covariance) + matrix('每对有效观测数', dep.featureNames || [], dep.pairCounts, true) + F.advanced('矩阵口径', F.note('矩阵按每对共同有效观测计算。缺失样本不一致时，协方差矩阵不保证半正定。')));
    const jointView = () => F.panel('联合分布表', (jointPage ? remoteState(jointPage, pairs.map(joint).join(''), '没有联合分布记录') : pairs.map(joint).join('')) + `<p class="sq-subtle">共 ${fmt(dep.totalPossiblePairs, 0)} 对可组合输入；本报告计算预算 ${fmt(dep.jointPairBudget, 0)} 对，省略 ${fmt(dep.omittedPairs, 0)} 对。选择规则：${e(pairSelection)}。</p>` + F.advanced('联合分布选择口径原始记录', raw({ jointPairSelection: dep.jointPairSelection })));
    return (view === 'all' ? featureView() + correlationView() + jointView() : ({ features: featureView, exposures: exposureView, correlations: correlationView, joints: jointView }[view] || featureView)()) + sample;
  }
  return { render };
}
