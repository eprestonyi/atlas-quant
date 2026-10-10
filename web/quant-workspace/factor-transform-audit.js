import { FUNCTION_SCHEMAS } from '../model-function-runtime.js';
import { inputDefinition, featureSymbol } from './model-inputs.js';

// Read the chosen immutable function and its own fit record. Never recover
// historical units, transforms or missing counts from today's factor catalog.
export function factorTransformRows(fit) {
  const a = fit?.functionArtifact;
  if (!a?.inputSchema) return [];
  const automatic = a.featureConstruction?.automatic;
  const declared = a.featureConstruction?.factors || [];
  const inputs = a.inputSchema.map((input, index) => {
    const definition = inputDefinition(a, index);
    const fitIndex = fit.featureNames?.indexOf(input.name) ?? -1;
    const population = fitIndex < 0 ? null : fit.automaticFitRows?.[fitIndex];
    const observed = fitIndex < 0 ? null : fit.automaticObservedRows?.[fitIndex];
    const countsValid = Number.isSafeInteger(population) && Number.isSafeInteger(observed) && population >= observed && observed >= 0;
    return { name: input.name, index, included: true, definition,
      population: countsValid ? population : null,
      observed: countsValid ? observed : null,
      missing: countsValid ? population - observed : null,
      populationUnit: definition.construction.descriptor?.scope === 'global' ? '日期' : '行' };
  });
  const included = new Set(inputs.map(x => x.name));
  return [...inputs, ...declared.filter(f => !included.has('factor:' + f.id)).map(factor => {
    const name = 'factor:' + factor.id;
    const descriptor = automatic?.factors?.find(x => x.feature === name);
    const dropped = fit.decorrelation?.dropped?.find(x => x.feature === name || x.name === name);
    return { name, included: false, factor, descriptor, dropped };
  })];
}

export function renderFactorTransformAudit(C, F, fit) {
  const a = fit?.functionArtifact;
  if (!a) return F.note('此拟合记录未保存因子处理参数。');
  if (!FUNCTION_SCHEMAS.includes(a.schema) || !Array.isArray(a.inputSchema)) return F.note('函数格式不受支持。', 'error');
  const { esc: e } = C;
  const value = x => x == null ? '—' : String(x);
  const pair = (label, x) => `<span>${e(label)} <code>${e(value(x))}</code></span>`;
  const raw = x => `<pre class="sq-report-code">${e(JSON.stringify(x, null, 2))}</pre>`;
  const t = a.transforms || {}, automatic = a.featureConstruction?.automatic;
  const training = a.training || {};
  const rows = factorTransformRows(fit);
  const table = `<div class="sq-table-scroll"><table class="sq-table sq-transform-table"><thead><tr>${['输入与原始表达式', '经济变换 R', '训练有效 / 缺失', '截尾与填补', '训练中心与尺度', '模型输入'].map(s => `<th>${s}</th>`).join('')}</tr></thead><tbody>${rows.map(row => {
    const d = row.definition, factor = d?.factor || row.factor, descriptor = d?.construction.descriptor || row.descriptor;
    const scope = descriptor?.scope === 'global' ? '全局 · 每训练日期一次' : descriptor?.scope === 'asset' ? '个股 · 训练样本行' : '状态输入';
    const count = row.included && row.population != null ? `<strong>${row.observed} / ${row.missing}</strong><small>${row.population} ${row.populationUnit} · R 填补前</small>` : '—';
    const conditioning = row.included ? `<div class="sq-transform-values">${t.winsorLower ? pair('下界', t.winsorLower[row.index]) + pair('上界', t.winsorUpper[row.index]) : '<span>未截尾</span>'}${t.imputeMedian ? pair('缺失填补', t.imputeMedian[row.index]) : '<span>未填补</span>'}</div>` : '—';
    const scaling = row.included ? t.scaleMean ? `<div class="sq-transform-values">${pair(automatic ? '中位数' : '中心', t.scaleMean[row.index])}${pair(automatic ? 'IQR / 常量回退尺度' : '尺度', t.scaleScale[row.index])}</div>` : '不缩放' : '—';
    const transform = d ? d.construction.operations.join(' → ') : descriptor?.transform?.kind || (factor?.role === 'hedge' ? '对冲暴露' : '未保存');
    const reason = row.dropped ? `相关性剔除 · ${row.dropped.retainedAgainst || '—'}` : factor?.role === 'hedge' ? '对冲角色，不进入预测 F' : '未进入这个 F';
    return `<tr data-factor-transform="${e(row.name)}"><th scope="row"><code>${e(row.name)}</code><small>${e(factor?.expression || row.name)}</small><small>${e(scope)}</small>${descriptor?.sourceUnit ? `<small>原单位 ${e(descriptor.sourceUnit)} · ${e(descriptor.economicType)}</small><small>${descriptor.clock === 'observed_source_sessions_asof' ? '来源交易期 → 已知时点对齐' : '研究交易期'}</small>` : ''}</th><td>${e(transform)}</td><td>${count}</td><td>${conditioning}</td><td>${scaling}</td><td>${row.included ? `<strong>${featureSymbol(row.index)}</strong>` : `<span>${e(reason)}</span>`}</td></tr>`;
  }).join('')}</tbody></table></div>`;
  const details = rows.filter(row => row.included).map(row => {
    const d = row.definition;
    return F.advanced(`${d.symbol} · ${row.name}`, `<h4>原始表达式 → 经济输入 R</h4><pre class="sq-model-equation">${e(d.construction.equations.join('\n') || '此产物未保存经济构建公式。')}</pre><h4>训练处理 → 模型输入 X</h4><pre class="sq-model-equation">${e(d.equations.join('\n'))}</pre>`);
  }).join('');
  return `<section class="sq-factor-transform-audit" data-transform-artifact="${e(a.artifactId)}"><div class="sq-transform-chain"><span>原始表达式 D</span><span>→ 经济输入 R</span><span>→ 训练处理 X</span><span>→ F 的基函数与系数</span></div><div class="sq-model-identity"><code>${e(a.artifactId)}</code><span>训练 ${e(training.trainStart || fit.trainStart || '—')} — ${e(training.trainEnd || fit.trainEnd || '—')}</span><span>信息截止 ${e(training.informationCutoff || fit.informationCutoff || '—')}</span><span>${e(automatic?.schema || '原研究处理协议')}</span></div>${table}<p class="sq-subtle">训练计数来自这个 F 的经济输入 R；原始字段缺失数与独立复算记录未保存时不补造。报告期分布在“因子统计”中查看。</p>${details}${F.advanced('冻结处理记录', raw({ artifactId: a.artifactId, training, featureConstruction: a.featureConstruction, transforms: t, featureNames: fit.featureNames, automaticFitRows: fit.automaticFitRows, automaticObservedRows: fit.automaticObservedRows, decorrelation: fit.decorrelation }))}</section>`;
}
