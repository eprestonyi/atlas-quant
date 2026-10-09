import { createFeatureLabeler } from './feature-labels.js';
import { featureSymbol, renderFunctionInputs } from './model-inputs.js';
import { FUNCTION_SCHEMAS } from '../model-function-runtime.js';

// Presentation of the saved portable function only; never refits or infers absent parameters.
export function modelFormula(artifact, output = 1) {
  const estimator = artifact?.estimator;
  if (estimator?.kind === 'constant') return String(estimator.value[output]);
  if (estimator?.kind === 'linear') return [String(estimator.intercepts[output]),
    ...estimator.coefficients[output].map((value, index) => `${value < 0 ? '−' : '+'} ${Math.abs(value)} × ${featureSymbol(index)}`)
  ].join(' ');
  if (estimator?.kind === 'histogram_trees') {
    const forest = estimator.outputs[output];
    return `${forest.baseline} + Σ(m = 1…${forest.trees.length}) tree_${output === 1 ? 'future' : 'entry'},m(X)`;
  }
  return null;
}

export function renderSavedModel(C, F, fit, { output = 1, tree = 0 } = {}) {
  const { esc: e } = C, a = fit?.functionArtifact;
  if (!a) return F.note(fit?.status === 'invalid' ? fit.invalidReason || '本次拟合不可用。' : '此记录未保存 F 函数与参数。', 'warning');
  if (!FUNCTION_SCHEMAS.includes(a.schema) || !Array.isArray(a.inputSchema) || !modelFormula(a)) return F.note('函数格式不受支持。', 'error');
  const table = (headers, rows) => `<div class="sq-table-scroll"><table class="sq-table"><thead><tr>${headers.map(x => `<th>${e(x)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
  const number = x => x == null ? '—' : String(x);
  const numeric = x => `<td class="numeric">${e(number(x))}</td>`;
  const raw = x => `<pre class="sq-report-code">${e(JSON.stringify(x, null, 2))}</pre>`;
  const label = createFeatureLabeler({ factors: a.featureConstruction?.factors || [], catalog: C.state?.catalog?.factors || [] });
  const kind = a.estimator.kind;
  const scaleDefinition = a.identity?.scale === 'origin_known_gross_absolute_leg_value'
    ? '<code class="sq-model-transform" data-model-scale="origin_known_gross_absolute_leg_value">P = Σ qⱼ pⱼ,ₜ<br>scale = Σ |qⱼ pⱼ,ₜ|</code>' : '';
  const formulas = `<div class="sq-model-formulas" data-saved-function="${e(a.artifactId)}"><span class="sq-kicker">F(X) · ${e(a.provenance?.estimator || fit.estimator || kind)}</span><pre class="sq-model-equation">Fₕ(X) = V̂future = P + scale × (${e(modelFormula(a, 1))})</pre>${scaleDefinition}<details class="sq-model-entry"><summary>入场输出</summary><pre class="sq-model-equation">V̂entry = P + scale × (${e(modelFormula(a, 0))})</pre></details><div class="sq-model-identity"><span>${a.lineage?.status === 'UNVALIDATED_USER_EDIT' ? '未验证的派生函数' : '已保存的拟合函数'}</span><code>${e(a.artifactId)}</code></div></div>`;
  let parameters;
  if (kind === 'constant') parameters = table(['参数', '入场', '未来'], [`<tr><th>常量</th>${a.estimator.value.map(numeric).join('')}</tr>`]);
  else if (kind === 'linear') parameters = table(['输入', '入场系数', '未来系数'], [
    `<tr><th>截距</th>${a.estimator.intercepts.map(numeric).join('')}</tr>`,
    ...a.inputSchema.map((input, index) => `<tr><th scope="row"><span>${featureSymbol(index)} · ${e(label(input.name))}</span></th>${numeric(a.estimator.coefficients[0][index])}${numeric(a.estimator.coefficients[1][index])}</tr>`)
  ]);
  else {
    output = output === 0 ? 0 : 1;
    const forest = a.estimator.outputs[output];
    tree = Math.max(0, Math.min(forest.trees.length - 1, Number(tree) || 0));
    parameters = `<div class="sq-report-controls"><label class="sq-field"><span>输出</span><select id="sq-model-tree-output"><option value="1" ${output === 1 ? 'selected' : ''}>未来</option><option value="0" ${output === 0 ? 'selected' : ''}>入场</option></select></label><label class="sq-field"><span>树</span><select id="sq-model-tree-index">${forest.trees.map((_, index) => `<option value="${index}" ${index === tree ? 'selected' : ''}>${index + 1} / ${forest.trees.length}</option>`).join('')}</select></label><span>基准 ${e(forest.baseline)}</span></div>` +
      table(['节点', '条件 / 叶子值', '满足 → 节点', '否则 → 节点'], forest.trees[tree].map((node, index) => `<tr><th scope="row">${index}</th><td>${node[5] ? `叶子 = ${e(node[0])}` : `${featureSymbol(node[1])} · ${e(label(a.inputSchema[node[1]].name))} ≤ ${e(node[2])}`}</td><td>${node[5] ? '—' : node[3]}</td><td>${node[5] ? '—' : node[4]}</td></tr>`));
  }
  const transforms = renderFunctionInputs(C, F, a);
  const params = fit.params || a.provenance?.parameters || {};
  return `<div class="sq-saved-model">${formulas}<div class="sq-model-meta"><span>训练 ${e(a.training?.trainStart || fit.trainStart || '—')} — ${e(a.training?.trainEnd || fit.trainEnd || '—')}</span><span>${e(a.training?.trainRows ?? fit.trainRows ?? '—')} 行</span><span>h = ${e(a.scope?.horizonSessions ?? '—')}</span></div><h3>模型参数</h3>${Object.keys(params).length ? `<dl class="sq-model-hyperparameters">${Object.entries(params).map(([name, value]) => `<div><dt>${e(name)}</dt><dd>${e(typeof value === 'object' ? JSON.stringify(value) : value)}</dd></div>`).join('')}</dl>` : ''}${parameters}${transforms}${F.advanced('函数定义与来源', raw({ identity: a.identity, training: a.training, scope: a.scope, featureConstruction: a.featureConstruction, provenance: a.provenance, lineage: a.lineage }))}</div>`;
}
