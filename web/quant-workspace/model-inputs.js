import { createFeatureLabeler, constructedFeatureLabel } from './feature-labels.js';

const SUBSCRIPT = '₀₁₂₃₄₅₆₇₈₉';
const subscript = index => String(index + 1).replace(/\d/g, digit => SUBSCRIPT[Number(digit)]);
export const featureSymbol = index => 'X' + subscript(index);

const BUILTIN_DEFINITION = Object.freeze({
  volatility20: 'sampleSD(ΔPₜ₋₁₉, …, ΔPₜ; ddof = 1) / scale',
  state_deviation20: '(Pₜ − mean(Pₜ₋₁₉, …, Pₜ)) / scale',
  state_deviation60: '(Pₜ − mean(Pₜ₋₅₉, …, Pₜ)) / scale',
  change1: '(Pₜ − Pₜ₋₁) / scale', change5: '(Pₜ − Pₜ₋₅) / scale',
  trend1: '(Pₜ − Pₜ₋₁) / scale', trend5: '(Pₜ − Pₜ₋₅) / scale',
  trend20: '(Pₜ − Pₜ₋₂₀) / scale', trend60: '(Pₜ − Pₜ₋₆₀) / scale'
});

function constructionDefinition(artifact, input, factor, symbol) {
  const asset = artifact.scope?.targetKind === 'asset_price' && artifact.identity?.scale === 'origin_known_gross_absolute_leg_value';
  const descriptor = artifact.featureConstruction?.automatic?.factors?.find(item => item.feature === input.name);
  if (!factor) return { equations: Object.hasOwn(BUILTIN_DEFINITION, input.name) ? [`${symbol} = ${BUILTIN_DEFINITION[input.name]}`] : [], operations: [], descriptor: null };
  if (!descriptor && artifact.featureConstruction?.schema === 'origin-state-features/2') return { equations: [], operations: ['构建定义缺失'], descriptor: null };
  if (!descriptor) return { equations: [`dⱼ,ₜ = ${factor.expression}`, `${symbol} = ${asset ? '' : 'Σⱼ(qⱼ pⱼ,ₜ / scale) × '}${factor.direction} × dⱼ,ₜ`], operations: [asset ? '个股输入' : '篮子聚合'], descriptor: null };
  const { transform: t, scope, direction } = descriptor;
  const raw = scope === 'global' ? 'dₜ' : 'dⱼ,ₜ', out = scope === 'global' ? 'gₜ' : 'gⱼ,ₜ';
  const member = scope === 'global' ? '' : 'ⱼ,';
  const formulas = {
    identity: [`${out} = ${raw}`],
    log_positive: [`${out} = ln(${raw})`, `${raw} ≤ 0 → null`],
    log1p_nonnegative: [`${out} = ln(1 + ${raw})`, `${raw} < 0 → null`],
    reciprocal_nonzero: [`${out} = 1 / ${raw}`, `${raw} = 0 → null`],
    percent_to_fraction: [`${out} = ${raw} / ${t.divisor}`],
    return_over_trailing_volatility: [`r${member}ₜ = ${raw} / d${member}ₜ₋₁ − 1`, `σ${member}ₜ = sampleSD(r${member}ₜ₋${t.volatilityWindow}, …, r${member}ₜ₋${t.volatilityLag}; ddof = ${t.ddof})`, `${out} = r${member}ₜ / σ${member}ₜ`, `d ≤ 0 或 σ${member}ₜ ≤ ${t.minVolatility} 或历史不足 → null`]
  };
  const labels = {identity:'原值',log_positive:'对数',log1p_nonnegative:'log1p',reciprocal_nonzero:'倒数',percent_to_fraction:'百分比转小数',return_over_trailing_volatility:'收益 / 历史波动'};
  return { descriptor, equations: [`${raw} = ${descriptor.expression}`, ...(formulas[t.kind] || ['未识别的经济变换']), `${out} 非有限 → null`,
    `${symbol} = ${scope === 'global' || asset ? `${direction} × ${out}` : `Σⱼ(qⱼ pⱼ,ₜ / scale) × ${direction} × ${out}`}`],
    operations: [labels[t.kind] || t.kind, scope === 'global' ? '全局一次' : asset ? '个股输入' : '篮子聚合'] };
}

// These are the portable function's actual numerical inputs. A factor expression
// is source metadata, not permission to substitute unaggregated security values.
export function inputDefinition(artifact, index) {
  const input = artifact.inputSchema[index], t = artifact.transforms || {};
  const factor = input.name.startsWith('factor:')
    ? artifact.featureConstruction?.factors?.find(value => value.id === input.name.slice(7)) : null;
  const symbol = featureSymbol(index), r = 'R' + subscript(index), u = 'u' + subscript(index);
  const construction = constructionDefinition(artifact, input, factor, r);
  const observed = t.winsorLower
    ? `clip(${r}, ${t.winsorLower[index]}, ${t.winsorUpper[index]})` : r;
  const filled = t.imputeMedian ? `${r} = null ? ${t.imputeMedian[index]} : ${observed}` : observed;
  const equations = [`${r} = input[${JSON.stringify(input.name)}]`, `${u} = ${filled}`,
    `${symbol} = ${t.scaleMean ? `(${u} − ${t.scaleMean[index]}) / ${t.scaleScale[index]}` : u}`];
  return { input, factor, symbol, equations, construction,
    operations: [...construction.operations, t.winsorLower && '截尾', t.imputeMedian && '缺失填充', t.scaleMean && (artifact.featureConstruction?.schema === 'origin-state-features/2' ? 'median / IQR' : '标准化')].filter(Boolean) };
}

export function renderFunctionInputs(C, F, artifact) {
  if (artifact.estimator.kind === 'constant') return '';
  const { esc: e } = C;
  const label = createFeatureLabeler({ factors: artifact.featureConstruction?.factors || [], catalog: C.state?.catalog?.factors || [] });
  return `<div class="sq-model-inputs"><h3>因子定义</h3>${artifact.inputSchema.map((_, index) => {
    const d = inputDefinition(artifact, index), factor = d.factor;
    const source = { inputKey: d.input.name, construction: artifact.featureConstruction?.schema,
      ...(factor ? { factorId: factor.id, expression: factor.expression, direction: factor.direction, role: factor.role,
        ...(factor.version === undefined ? {} : { version: factor.version }) } : {}) };
    if (d.construction.descriptor) source.automatic = d.construction.descriptor;
    const population = artifact.featureConstruction?.automatic?.fitPopulation === 'asset_rows_global_dates'
      ? d.construction.descriptor?.scope === 'global' ? '每训练日期一次' : '训练样本行' : null;
    if (population) source.fitPopulation = artifact.featureConstruction.automatic.fitPopulation;
    return `<details class="sq-model-input" data-model-input="${e(d.input.name)}"><summary><strong>${d.symbol}</strong><span>${e(constructedFeatureLabel(label(d.input.name),d.construction.descriptor))}</span><small>${e(d.operations.join(' → ') || '原值')}</small></summary><div class="sq-model-input-body">${d.construction.equations.length ? `<h4>输入构建 R${subscript(index)}</h4><pre class="sq-model-equation" data-input-construction>${e(d.construction.equations.join('\n'))}</pre>` : ''}<h4>训练变换 ${d.symbol}</h4><pre class="sq-model-equation" data-input-transform>${e(d.equations.join('\n'))}</pre><dl class="sq-model-input-source"><dt>输入键</dt><dd><code>${e(d.input.name)}</code></dd>${factor ? `<dt>因子表达式</dt><dd><code>${e(factor.expression)}</code></dd><dt>方向 / 角色</dt><dd>${e(factor.direction)} / ${e(factor.role)}</dd>` : ''}${d.construction.descriptor ? `<dt>作用域</dt><dd>${e(d.construction.descriptor.scope)}</dd>` : ''}${population ? `<dt>拟合样本</dt><dd>${e(population)}</dd>` : ''}<dt>构建协议</dt><dd>${e(artifact.featureConstruction?.schema || '未保存')}</dd></dl>${F.advanced('原始定义', `<pre class="sq-report-code">${e(JSON.stringify(source, null, 2))}</pre>`)}</div></details>`;
  }).join('')}</div>`;
}
