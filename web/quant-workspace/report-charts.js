// SVG views of saved observations/parameters. No estimation, fitting or imputed data.
const finite = Number.isFinite;
const extent = values => {
  const lo = Math.min(...values), hi = Math.max(...values), pad = (hi - lo) * .08 || Math.abs(lo) * .08 || .01;
  return [lo - pad, hi + pad];
};
const scaled = (domain, range) => value => range[0] + (value - domain[0]) / (domain[1] - domain[0]) * (range[1] - range[0]);
const ticks = domain => Array.from({ length: 5 }, (_, i) => domain[0] + (domain[1] - domain[0]) * i / 4);
const number = x => finite(x) ? Math.abs(x) >= 1000 || Math.abs(x) < .0001 && x !== 0 ? x.toExponential(2) : Number(x.toPrecision(4)).toString() : '—';
const percent = x => finite(x) ? `${number(x * 100)}%` : '—';
const frame = (title, content, e, footer = '') => `<figure class="sq-data-chart"><figcaption>${e(title)}</figcaption>${content}${footer ? `<p>${e(footer)}</p>` : ''}</figure>`;
const svg = (title, content, e, height = 280) => `<svg viewBox="0 0 620 ${height}" role="img" aria-label="${e(title)}">${content}</svg>`;
function axes(xd, yd, e, xTitle, yTitle, format = number) {
  const x = scaled(xd, [65, 590]), y = scaled(yd, [225, 25]);
  return ticks(yd).map(t => `<line class="grid" x1="65" x2="590" y1="${y(t)}" y2="${y(t)}"/><text x="58" y="${y(t) + 4}" text-anchor="end">${e(format(t))}</text>`).join('') + ticks(xd).map((t,i) => `<text x="${x(t)}" y="244" text-anchor="${i===0?'start':i===4?'end':'middle'}">${e(format(t))}</text>`).join('') + `<text class="axis-title" x="325" y="270" text-anchor="middle">${e(xTitle)}</text><text class="axis-title" x="65" y="13">${e(yTitle)}</text>`;
}

export function coefficientChart({ labels, values, esc: e, title = '未来输出系数' }) {
  const rows = values.map((value, i) => ({ value, label: labels[i] || `X${i + 1}` })).filter(x => finite(x.value));
  if (!rows.length) return '';
  const shown = rows.slice(0, 20), max = Math.max(...shown.map(x => Math.abs(x.value))) || 1, h = 35 + shown.length * 48;
  return frame(title, svg(title, `<line class="grid" x1="340" x2="340" y1="10" y2="${h - 10}"/>` + shown.map(({ value, label }, i) => {
    const width = Math.abs(value) / max * 170, y = 14 + i * 48;
    return `<text x="8" y="${y}">${e(label.length > 46 ? label.slice(0, 45) + '…' : label)}</text><rect class="${value < 0 ? 'negative' : 'positive'}" x="${value < 0 ? 340 - width : 340}" y="${y + 10}" width="${Math.max(width, .5)}" height="18" rx="2"><title>${e(label)}: ${value}</title></rect><text x="${value < 0 ? 334 - width : 346 + width}" y="${y + 24}" text-anchor="${value < 0 ? 'end' : 'start'}">${number(value)}</text>`;
  }).join(''), e, h), e, rows.length > shown.length ? `显示声明顺序前 ${shown.length} / ${rows.length} 项；全部系数见参数表。` : '系数按模型输入单位显示；不等同于因果贡献。');
}

export function forecastCharts(rows, { esc: e, scope = '当前记录' }) {
  const eligible = rows.filter(r => r.status === 'valid' && r.labelMaturedAt && finite(r.scale) && r.scale > 0 && [r.currentState, r.expectedFuture, r.realizedFuture].every(finite));
  const sample = eligible.length > 1000 ? Array.from({ length:1000 }, (_,i)=>eligible[Math.round(i*(eligible.length-1)/999)]) : eligible;
  const pairs = sample.map(r => ({
    actual: (r.realizedFuture - r.currentState) / r.scale,
    predicted: (r.expectedFuture - r.currentState) / r.scale,
    error: (r.realizedFuture - r.expectedFuture) / r.scale,
    gap: (r.currentState - r.expectedFuture) / r.scale,
    label: `${r.date} · ${r.targetId}`
  })).filter(r => [r.actual, r.predicted, r.error, r.gap].every(finite));
  if (!pairs.length) return '';
  const domain = extent(pairs.flatMap(p => [p.actual, p.predicted])), x = scaled(domain, [65, 590]), y = scaled(domain, [225, 25]);
  const caption = `${scope} · ${pairs.length} ${eligible.length > 1000 ? `/ ${eligible.length} 条成熟观测，按原行序均匀预览` : '条成熟观测'}；按当前已知总名义值归一化。`;
  const scatter = frame('预测变化 × 实际变化', svg('归一化预测与实现变化散点图', axes(domain, domain, e, '实际变化 / scale', '预测变化 / scale', percent) + `<line class="reference" x1="${x(domain[0])}" x2="${x(domain[1])}" y1="${y(domain[0])}" y2="${y(domain[1])}"/>` + pairs.map(p => `<circle class="point" cx="${x(p.actual)}" cy="${y(p.predicted)}" r="3"><title>${e(p.label)} · 预测 ${percent(p.predicted)} · 实际 ${percent(p.actual)}</title></circle>`).join(''), e), e, caption);
  const hist = (key, title) => {
    const values = pairs.map(p => p[key]), domain = extent(values), bins = Math.min(20, Math.max(5, Math.ceil(Math.sqrt(values.length)))), counts = Array(bins).fill(0);
    for (const value of values) counts[Math.min(bins - 1, Math.max(0, Math.floor((value - domain[0]) / (domain[1] - domain[0]) * bins)))]++;
    const sx = scaled(domain, [65, 590]), sy = scaled([0, Math.max(...counts) || 1], [225, 25]), bw = 525 / bins;
    const chart = axes(domain, [0, Math.max(...counts) || 1], e, key === 'error' ? '(实现 − 预测) / scale' : '(P − V̂) / scale', '频数');
    return frame(title, svg(title, chart + counts.map((count, i) => `<rect class="positive" x="${65 + i * bw + 1}" y="${sy(count)}" width="${Math.max(0, bw - 2)}" height="${225 - sy(count)}"><title>${number(domain[0] + i * (domain[1] - domain[0]) / bins)} 至 ${number(domain[0] + (i + 1) * (domain[1] - domain[0]) / bins)}: ${count}</title></rect>`).join('') + (domain[0] <= 0 && domain[1] >= 0 ? `<line class="reference" x1="${sx(0)}" x2="${sx(0)}" y1="25" y2="225"/>` : ''), e), e, caption);
  };
  return `<div class="sq-chart-grid">${scatter}${hist('error', '事后预测误差分布')}${hist('gap', '预测偏离 E 分布')}</div>`;
}

export function factorDistributionCharts(rows, label, e) {
  return `<div class="sq-chart-grid">${rows.filter(x => [x.distribution?.q01, x.distribution?.q25, x.distribution?.median, x.distribution?.q75, x.distribution?.q99].every(finite)).map(x => {
    const d = x.distribution, domain = extent([d.q01, d.q99]), sx = scaled(domain, [45, 580]), title = label(x.name, x.definition);
    return frame(title, svg(`${title} 分位数`, `<line class="whisker" x1="${sx(d.q01)}" x2="${sx(d.q99)}" y1="35" y2="35"/><line class="whisker" x1="${sx(d.q01)}" x2="${sx(d.q01)}" y1="25" y2="45"/><line class="whisker" x1="${sx(d.q99)}" x2="${sx(d.q99)}" y1="25" y2="45"/><rect class="positive" x="${sx(d.q25)}" y="20" width="${Math.max(1, sx(d.q75) - sx(d.q25))}" height="30" rx="2"/><line class="median" x1="${sx(d.median)}" x2="${sx(d.median)}" y1="17" y2="53"/>${[d.q01, d.median, d.q99].map((v, i) => `<text x="${sx(v)}" y="76" text-anchor="${i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}">${number(v)}</text>`).join('')}`, e, 90), e, '须线 P1–P99 · 箱体 Q1–Q3 · 中线为中位数');
  }).join('')}</div>`;
}

export function candidateScoreChart(candidates, e) {
  const rows = candidates.filter(x => finite(x.validationScore));
  if (!rows.length) return '';
  const max = Math.max(...rows.map(x => x.validationScore)) || 1;
  return frame('开发期时间验证损失', svg('候选模型验证损失，越低越好', rows.map((r, i) => `<text x="8" y="${28 + i * 28}">${e(r.id)}</text><rect class="${r.selected ? 'positive' : 'muted-bar'}" x="255" y="${14 + i * 28}" width="${Math.max(1, r.validationScore / max * 260)}" height="18" rx="2"/><text x="${262 + r.validationScore / max * 260}" y="${28 + i * 28}">${number(r.validationScore)}</text>`).join(''), e, 38 + rows.length * 28), e, '越低越好 · 固定开发期协议 · 不使用最终测试集择优');
}

export function trainingFitChart(plot, e, {artifact} = {}) {
  if (plot?.sample !== 'training_in_sample') return '';
  if (artifact?.schema === 'atlas-model-function/4') return returnResponseCharts((plot.points||[]).map(x=>({date:x.date,assetSymbol:artifact.scope.symbols[0],observedResponse:x.actualResponse,predictedResponse:x.fittedResponse})),{esc:e,association:artifact.scope.studyMode==='association',normalized:artifact.featureConstruction.targetSpecification.normalization.kind!=='none',scope:`训练样本内 · ${(plot.points||[]).length} / ${plot.totalRows} 个样本；不是测试集表现`});
  const points = (plot.points || []).filter(p => [p.actualFuture,p.fittedFuture].every(finite));
  if (!points.length) return '';
  const domain = extent(points.flatMap(p => [p.actualFuture,p.fittedFuture])), x = scaled(domain,[65,590]), y = scaled(domain,[225,25]);
  return frame('训练拟合 · 样本内', svg('训练样本内拟合与实际变化', axes(domain,domain,e,'训练标签：未来变化 / scale','样本内拟合变化 / scale',percent) + `<line class="reference" x1="${x(domain[0])}" x2="${x(domain[1])}" y1="${y(domain[0])}" y2="${y(domain[1])}"/>` + points.map(p=>`<circle class="point" cx="${x(p.actualFuture)}" cy="${y(p.fittedFuture)}" r="3"><title>${e(p.date)} · ${e(p.targetId)} · 拟合 ${percent(p.fittedFuture)} · 实际 ${percent(p.actualFuture)}</title></circle>`).join(''),e),e,`${points.length} / ${plot.totalRows} 个训练样本；按原行序均匀取样。此图不是测试集预测表现。`);
}

// Single-response observations are rendered directly; no fictitious P/entry pair.
export function returnResponseCharts(rows, {esc:e, association=false, normalized=false, scope='当前页'}) {
  const eligible=rows.filter(r=>finite(r.predictedResponse)&&finite(r.observedResponse));
  const sample=eligible.length>1000?Array.from({length:1000},(_,i)=>eligible[Math.round(i*(eligible.length-1)/999)]):eligible;
  if(!sample.length)return '';
  const domain=extent(sample.flatMap(r=>[r.predictedResponse,r.observedResponse])),x=scaled(domain,[65,590]),y=scaled(domain,[225,25]);
  const format=normalized?number:percent, label=association?'同期响应':'未来收益',unit=normalized?'波动标准化响应':'收益率';
  const caption=`${scope} · ${sample.length} / ${eligible.length} 条可配对观测；${unit}`;
  const scatter=frame(`${label}：模型 × 实际`,svg(`${label}散点图`,axes(domain,domain,e,'实际'+unit,'模型'+unit,format)+`<line class="reference" x1="${x(domain[0])}" x2="${x(domain[1])}" y1="${y(domain[0])}" y2="${y(domain[1])}"/>`+sample.map(r=>`<circle class="point" cx="${x(r.observedResponse)}" cy="${y(r.predictedResponse)}" r="3"><title>${e(r.date)} · ${e(r.assetSymbol)} · ${format(r.observedResponse)} / ${format(r.predictedResponse)}</title></circle>`).join(''),e),e,caption);
  const errors=sample.map(r=>r.observedResponse-r.predictedResponse),ed=extent(errors),bins=Math.min(20,Math.max(5,Math.ceil(Math.sqrt(errors.length)))),counts=Array(bins).fill(0);
  for(const v of errors)counts[Math.max(0,Math.min(bins-1,Math.floor((v-ed[0])/(ed[1]-ed[0])*bins)))]++;
  const sy=scaled([0,Math.max(...counts)||1],[225,25]),w=525/bins;
  const histogram=frame('实际 − 模型响应的残差分布',svg('响应残差频数',axes(ed,[0,Math.max(...counts)||1],e,'实际 − 模型响应','频数')+counts.map((n,i)=>`<rect class="positive" x="${65+i*w+1}" y="${sy(n)}" width="${Math.max(0,w-2)}" height="${225-sy(n)}"><title>${n}</title></rect>`).join(''),e),e,caption);
  return `<div class="sq-chart-grid">${scatter}${histogram}</div>`;
}
