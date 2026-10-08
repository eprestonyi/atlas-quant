// Descriptive factor evidence from frozen artifacts; never estimates missing statistics in the browser.
export function createFactorDiagnostics(C, F, { remote, remoteState, table }) {
  const { esc: e, fmt, pct } = C;
  const value = x => fmt(x, 5);
  const cell = x => `<td class="numeric">${value(x)}</td>`;
  const raw = x => `<pre class="sq-report-code">${e(JSON.stringify(x, null, 2))}</pre>`;
  const status = x => x?.status === 'available' || x?.status === 'ok' ? '已计算' : x?.unavailableReason || x?.status || '未提供';
  function featureDetail(x) {
    const ts = x.timeSeriesCorrelation || {}, fit = x.descriptiveFit || {};
    return F.advanced(`${x.name} · 定义与统计口径`,
      `<dl class="sq-key-values"><dt>定义</dt><dd>${e(x.definition?.expression || x.name)}</dd><dt>观察缺失</dt><dd>${fmt(x.missing?.count, 0)} / ${fmt(x.missing?.total, 0)} · ${pct(x.missing?.fraction)}</dd><dt>横截面 IC</dt><dd>${e(status(x.ic))} · ${fmt(x.ic?.dates, 0)} 个有效日期</dd><dt>Rank IC</dt><dd>${e(status(x.rankIc))} · ${fmt(x.rankIc?.dates, 0)} 个有效日期</dd><dt>描述性单变量 R²</dt><dd>${value(fit.rSquared)} · ${e(fit.fitSample || '未提供样本范围')}</dd><dt>显著性</dt><dd>未提供经依赖调整的 p 值或系数标准误</dd></dl>` +
      F.note('单变量拟合使用报告样本，只是描述关系；不是因子加入 F 后的样本外增量，也不是因果解释。单一标的的时间序列相关不称为横截面 IC。') +
      (ts.perTarget?.length ? table(['目标', '样本数', '时序 Pearson', '时序 Spearman'], ts.perTarget.map(t => `<tr><td>${e(t.targetId)}</td><td>${fmt(t.n, 0)}</td>${cell(t.pearson)}${cell(t.spearman)}</tr>`)) + `<p class="sq-subtle">目标总数 ${fmt(ts.totalTargets, 0)}；未展示 ${fmt(ts.omittedTargets, 0)}。${e(ts.interpretation || '')}</p>` : F.note('未返回可用的逐标的时间序列相关。')) +
      F.advanced('原始统计记录', raw(x)));
  }
  function features(rows) {
    return table(['输入因子', '有效 / 缺失', '均值 / 标准差', '中位数 / IQR', '横截面 IC / Rank IC', '描述性 R²'], rows.map(x => {
      const d = x.distribution || {};
      return `<tr><td>${e(x.name)}<small>${x.kind === 'factor' ? '研究因子' : '派生状态'}</small></td><td>${fmt(d.count, 0)} / ${fmt(x.missing?.count, 0)}</td><td>${value(d.mean)}<small>${value(d.std)}</small></td><td>${value(d.median)}<small>${value(d.q25)} — ${value(d.q75)}</small></td><td>${value(x.ic?.mean)} / ${value(x.rankIc?.mean)}<small>${fmt(x.ic?.dates, 0)} 个有效横截面</small></td>${cell(x.descriptiveFit?.rSquared)}</tr>`;
    })) + rows.map(featureDetail).join('');
  }
  function interval(edges, index) {
    const lo = edges?.[index], hi = edges?.[index + 1];
    return `${index === 0 && lo === null ? '−∞' : value(lo)} 至 ${hi === null ? '+∞' : value(hi)}`;
  }
  function joint(x) {
    if (!Array.isArray(x.counts) || !x.counts.length) return F.advanced(`${x.x} × ${x.y}`, F.note(`联合分布不可用：${x.status || '未返回频数表'}`));
    const counts = x.counts, columns = counts[0].length;
    const rowTotals = counts.map(row => row.reduce((a,b) => a + b, 0));
    const columnTotals = Array.from({ length: columns }, (_, n) => counts.reduce((a,row) => a + row[n], 0));
    const cells = counts.map((row, ri) => `<tr><th scope="row">${e(interval(x.xEdges, ri))}</th>${row.map((n, ci) => `<td class="numeric">${fmt(n, 0)}<small>${pct(x.probabilities?.[ri]?.[ci])}</small></td>`).join('')}<td class="numeric">${fmt(rowTotals[ri], 0)}</td></tr>`);
    cells.push(`<tr><th scope="row">Y 边际频数</th>${columnTotals.map(n => `<td class="numeric">${fmt(n, 0)}</td>`).join('')}<td class="numeric">${fmt(x.sampleCount, 0)}</td></tr>`);
    return F.advanced(`${x.x} × ${x.y} · ${fmt(x.sampleCount, 0)} 对观测`,
      `<p>X：${e(x.x)}；Y：${e(x.y)}。每格为联合频数与经验概率。</p>` + table(['X 分箱 / Y 分箱', ...Array.from({ length: columns }, (_, n) => interval(x.yEdges, n)), 'X 边际频数'], cells) +
      `<dl class="sq-key-values"><dt>缺失配对</dt><dd>${fmt(x.missingPairCount, 0)}</dd><dt>观察区间</dt><dd>${e(C.dateText(x.firstDate))} — ${e(C.dateText(x.lastDate))}</dd><dt>分箱来源</dt><dd>${e(x.edgeSource || '未提供')}</dd><dt>边界约定</dt><dd>${e(x.intervalConvention || '未提供')}</dd></dl>` +
      F.note('这是该报告样本的经验联合分布，不是已知的总体分布，也不意味着观测相互独立。') + F.advanced('分箱与概率原始记录', raw(x)));
  }
  function matrix(title, names, values, integer = false) {
    if (!Array.isArray(values)) return F.note(`${title}尚未返回。`);
    return F.advanced(title, table(['输入', ...names], values.map((row, ri) => `<tr><th scope="row">${e(names[ri])}</th>${row.map(n => `<td class="numeric">${fmt(n, integer ? 0 : 4)}</td>`).join('')}</tr>`)));
  }
  function render(r) {
    const meta = r.forecasts?.factorResearch, d = meta?.diagnostics;
    if (!d) return F.panel('因子诊断', F.note('此产物没有因子研究诊断记录。旧结果不会补造 IC、拟合度或联合分布。'));
    const featurePage = remote.enabled() ? remote.page('factorFeatures') : null;
    const jointPage = remote.enabled() ? remote.page('factorJointDistributions') : null;
    const featureRows = featurePage ? featurePage.items : d.features || [];
    const pairs = jointPage ? jointPage.items : d.dependence?.jointDistributions || [];
    const dep = d.dependence || {};
    return F.panel('因子样本与统计口径', `<dl class="sq-key-values"><dt>诊断区间</dt><dd>${e(C.dateText(d.firstDate))} — ${e(C.dateText(d.lastDate))}</dd><dt>成熟有效观察</dt><dd>${fmt(d.maturedValidOrigins, 0)} / ${fmt(d.origins, 0)}</dd><dt>标签定义</dt><dd>${e(d.targetDefinition || d.target || '未提供')}</dd><dt>用途</dt><dd>模型选择后计算的报告诊断，未用于本次候选选择</dd></dl>${F.note(d.significance?.reason || '当前未提供依赖感知的统计显著性；重叠标签不视为独立样本。')}`) +
      F.panel('因子分布与关联', featurePage ? remoteState(featurePage, features(featureRows), '没有因子统计记录') : features(featureRows), { description: 'IC / Rank IC 为逐日期横截面相关；有效横截面至少需要三个非恒定标的。时序相关另列。' }) +
      F.panel('输入间相关与协方差', matrix('相关矩阵', dep.featureNames || [], dep.correlation) + matrix('协方差矩阵', dep.featureNames || [], dep.covariance) + matrix('每对有效观测数', dep.featureNames || [], dep.pairCounts, true) + F.note('矩阵按每对共同有效观测计算。缺失样本不一致时，协方差矩阵不保证半正定。')) +
      F.panel('联合分布表', (jointPage ? remoteState(jointPage, pairs.map(joint).join(''), '没有联合分布记录') : pairs.map(joint).join('')) + `<p class="sq-subtle">共 ${fmt(dep.totalPossiblePairs, 0)} 对可组合输入；本报告计算预算 ${fmt(dep.jointPairBudget, 0)} 对，省略 ${fmt(dep.omittedPairs, 0)} 对。选择规则：${e(dep.jointPairSelection || '未提供')}。</p>`);
  }
  return { render };
}
