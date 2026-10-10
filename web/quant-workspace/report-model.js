import { isReturnFunction, returnUnit, returnTiming } from './return-study.js';
import { createFeatureLabeler, constructedFeatureLabel } from './feature-labels.js';
import { featureSymbol, renderFunctionInputs } from './model-inputs.js';
import { FUNCTION_SCHEMAS } from '../model-function-runtime.js';
import { coefficientChart } from './report-charts.js';

export function basisTerm(term) {
  if (term.kind === 'power') return featureSymbol(term.feature) + (term.degree === 1 ? '' : term.degree === 2 ? '²' : term.degree === 3 ? '³' : `^${term.degree}`);
  if (term.kind === 'interaction') return term.features.map(featureSymbol).join(' × ');
  if (term.kind === 'signed_log1p') return `sign(${featureSymbol(term.feature)}) ln(1 + |${featureSymbol(term.feature)}|)`;
  if (term.kind === 'signed_expm1') return `sign(${featureSymbol(term.feature)})(exp(min(|${featureSymbol(term.feature)}|, 3)) − 1)`;
  return '?';
}

export function expandedBasis(estimator, output = 1) {
  const coefficients = estimator.coefficients[output].map((value, index) => value / estimator.termScale[index]);
  return { coefficients, intercept: estimator.intercepts[output] - coefficients.reduce((sum, value, index) => sum + value * estimator.termCenter[index], 0) };
}

// Presentation of the saved portable function only; never refits or infers absent parameters.
export function modelFormula(artifact, output = 1) {
  if (isReturnFunction(artifact)) output = 0;
  const estimator = artifact?.estimator;
  if (estimator?.kind === 'constant') return String(estimator.value[output]);
  if (estimator?.kind === 'linear') return [String(estimator.intercepts[output]),
    ...estimator.coefficients[output].map((value, index) => `${value < 0 ? '−' : '+'} ${Math.abs(value)} × ${featureSymbol(index)}`)
  ].join(' ');
  if (estimator?.kind === 'basis_linear') {
    const expanded = expandedBasis(estimator, output);
    return [String(expanded.intercept), ...expanded.coefficients.map((value, index) => `${value < 0 ? '−' : '+'} ${Math.abs(value)} × ${basisTerm(estimator.terms[index])}`)].join(' ');
  }
  if (estimator?.kind === 'histogram_trees') {
    const forest = estimator.outputs[output];
    return `${forest.baseline} + Σ(m = 1…${forest.trees.length}) tree_${isReturnFunction(artifact) ? 'return' : output === 1 ? 'future' : 'entry'},m(X)`;
  }
  return null;
}

export function renderSavedModel(C, F, fit, { output = 1, tree = 0, controlPrefix = 'sq-model' } = {}) {
  const { esc: e } = C, a = fit?.functionArtifact;
  if (isReturnFunction(a)) return renderReturnModel(C, F, fit, {tree,controlPrefix});
  if (!a) return F.note(fit?.status === 'invalid' ? fit.invalidReason || '本次拟合不可用。' : '此记录未保存 F 函数与参数。', 'warning');
  if (!FUNCTION_SCHEMAS.includes(a.schema) || !Array.isArray(a.inputSchema) || !modelFormula(a)) return F.note('函数格式不受支持。', 'error');
  const table = (headers, rows) => `<div class="sq-table-scroll"><table class="sq-table"><thead><tr>${headers.map(x => `<th>${e(x)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
  const number = x => x == null ? '—' : String(x);
  const numeric = x => `<td class="numeric">${e(number(x))}</td>`;
  const raw = x => `<pre class="sq-report-code">${e(JSON.stringify(x, null, 2))}</pre>`;
  const rawLabel = createFeatureLabeler({ factors: a.featureConstruction?.factors || [], catalog: C.state?.catalog?.factors || [] });
  const label = name => constructedFeatureLabel(rawLabel(name), a.featureConstruction?.automatic?.factors?.find(x=>x.feature===name));
  const kind = a.estimator.kind;
  const assetReturn = a.scope?.targetKind === 'asset_price' && a.identity?.scale === 'origin_known_gross_absolute_leg_value';
  const scaleDefinition = a.identity?.scale === 'origin_known_gross_absolute_leg_value'
    ? `<code class="sq-model-transform" data-model-scale="origin_known_gross_absolute_leg_value">${assetReturn ? 'P = pᵢ,ₜ<br>scale = P' : 'P = Σ qⱼ pⱼ,ₜ<br>scale = Σ |qⱼ pⱼ,ₜ|'}</code>` : '';
  const formulas = `<div class="sq-model-formulas" data-saved-function="${e(a.artifactId)}"><span class="sq-kicker">${assetReturn ? '条件收益模型' : '归一化条件变化'} · ${e(a.provenance?.estimator || fit.estimator || kind)}</span><pre class="sq-model-equation">fₕ(X) = ${e(modelFormula(a, 1))}</pre><div class="sq-model-output-definition"><code>fₕ(X) = ${assetReturn ? 'V̂future / P − 1' : '(V̂future − P) / scale'}</code><code>Fₕ(X) = V̂future = ${assetReturn ? 'P × (1 + fₕ(X))' : 'P + scale × fₕ(X)'}</code></div>${scaleDefinition}<details class="sq-model-entry"><summary>入场输出</summary><pre class="sq-model-equation">fentry(X) = ${e(modelFormula(a, 0))}\nV̂entry = ${assetReturn ? 'P × (1 + fentry(X))' : 'P + scale × fentry(X)'}</pre></details><div class="sq-model-identity"><span>${a.lineage?.status === 'UNVALIDATED_USER_EDIT' ? '未验证的派生函数' : '已保存的拟合函数'}</span><code>${e(a.artifactId)}</code></div></div>`;
  let parameters;
  if (kind === 'constant') parameters = table(['参数', '入场', '未来'], [`<tr><th>常量</th>${a.estimator.value.map(numeric).join('')}</tr>`]);
  else if (kind === 'linear') parameters = table(['输入', '入场系数', '未来系数'], [
    `<tr><th>截距</th>${a.estimator.intercepts.map(numeric).join('')}</tr>`,
    ...a.inputSchema.map((input, index) => `<tr><th scope="row"><span>${featureSymbol(index)} · ${e(label(input.name))}</span></th>${numeric(a.estimator.coefficients[0][index])}${numeric(a.estimator.coefficients[1][index])}</tr>`)
  ]);
  else if (kind === 'basis_linear') {
    const entry = expandedBasis(a.estimator, 0), future = expandedBasis(a.estimator, 1);
    parameters = table(['方程项', '入场系数', '未来系数'], [
      `<tr><th>截距</th>${numeric(entry.intercept)}${numeric(future.intercept)}</tr>`,
      ...a.estimator.terms.map((term, index) => `<tr><th scope="row">${e(basisTerm(term))}</th>${numeric(entry.coefficients[index])}${numeric(future.coefficients[index])}</tr>`)
    ]) + F.advanced('基函数训练尺度', table(['基函数', '训练中心', '训练尺度', '原始入场系数', '原始未来系数'], a.estimator.terms.map((term, index) => `<tr><th>${e(basisTerm(term))}</th>${numeric(a.estimator.termCenter[index])}${numeric(a.estimator.termScale[index])}${numeric(a.estimator.coefficients[0][index])}${numeric(a.estimator.coefficients[1][index])}</tr>`)) + raw({ intercepts: a.estimator.intercepts, terms: a.estimator.terms }));
  }
  else {
    output = output === 0 ? 0 : 1;
    const forest = a.estimator.outputs[output];
    tree = Math.max(0, Math.min(forest.trees.length - 1, Number(tree) || 0));
    parameters = `<div class="sq-report-controls"><label class="sq-field"><span>输出</span><select id="${e(controlPrefix)}-tree-output"><option value="1" ${output === 1 ? 'selected' : ''}>未来</option><option value="0" ${output === 0 ? 'selected' : ''}>入场</option></select></label><label class="sq-field"><span>树</span><select id="${e(controlPrefix)}-tree-index">${forest.trees.map((_, index) => `<option value="${index}" ${index === tree ? 'selected' : ''}>${index + 1} / ${forest.trees.length}</option>`).join('')}</select></label><span>基准 ${e(forest.baseline)}</span></div>` +
      table(['节点', '条件 / 叶子值', '满足 → 节点', '否则 → 节点'], forest.trees[tree].map((node, index) => `<tr><th scope="row">${index}</th><td>${node[5] ? `叶子 = ${e(node[0])}` : `${featureSymbol(node[1])} · ${e(label(a.inputSchema[node[1]].name))} ≤ ${e(node[2])}`}</td><td>${node[5] ? '—' : node[3]}</td><td>${node[5] ? '—' : node[4]}</td></tr>`));
  }
  const transforms = renderFunctionInputs(C, F, a);
  const chart = kind === 'linear' ? coefficientChart({ esc: e, labels: a.inputSchema.map((input, i) => `${featureSymbol(i)} · ${label(input.name)}`), values: a.estimator.coefficients[1] }) : kind === 'basis_linear' ? coefficientChart({ esc: e, labels: a.estimator.terms.map(basisTerm), values: expandedBasis(a.estimator, 1).coefficients }) : '';
  const params = fit.params || a.provenance?.parameters || {};
  return `<div class="sq-saved-model">${formulas}<div class="sq-model-meta"><span>训练 ${e(a.training?.trainStart || fit.trainStart || '—')} — ${e(a.training?.trainEnd || fit.trainEnd || '—')}</span><span>${e(a.training?.trainRows ?? fit.trainRows ?? '—')} 行</span><span>h = ${e(a.scope?.horizonSessions ?? '—')}</span></div><h3>模型参数</h3>${Object.keys(params).length ? `<dl class="sq-model-hyperparameters">${Object.entries(params).map(([name, value]) => `<div><dt>${e(name)}</dt><dd>${e(typeof value === 'object' ? JSON.stringify(value) : value)}</dd></div>`).join('')}</dl>` : ''}${parameters}${chart}${transforms}${F.advanced('函数定义与来源', raw({ identity: a.identity, training: a.training, scope: a.scope, featureConstruction: a.featureConstruction, provenance: a.provenance, lineage: a.lineage }))}</div>`;
}

function renderReturnModel(C, F, fit, {tree,controlPrefix}) {
  const a=fit.functionArtifact,{esc:e}=C, estimator=a.estimator,kind=estimator.kind;
  const table=(head,rows)=>`<div class="sq-table-scroll"><table class="sq-table"><thead><tr>${head.map(x=>`<th>${e(x)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
  const raw=x=>`<pre class="sq-report-code">${e(JSON.stringify(x,null,2))}</pre>`;
  const label=createFeatureLabeler({factors:a.featureConstruction.factors,catalog:[]});
  const output=returnUnit(a), symbol=a.scope.symbols[0];
  let rows=[],chart='';
  if(kind==='constant') rows=[['常量',estimator.value[0]]];
  if(kind==='linear') { rows=[['截距',estimator.intercepts[0]],...a.inputSchema.map((x,i)=>[`${featureSymbol(i)} · ${label(x.name)}`,estimator.coefficients[0][i]])];chart=coefficientChart({esc:e,labels:rows.slice(1).map(x=>x[0]),values:estimator.coefficients[0],title:'收益响应系数'}); }
  if(kind==='basis_linear') { const v=expandedBasis(estimator,0); rows=[['截距',v.intercept],...estimator.terms.map((x,i)=>[basisTerm(x),v.coefficients[i]])];chart=coefficientChart({esc:e,labels:rows.slice(1).map(x=>x[0]),values:v.coefficients,title:'收益响应系数'}); }
  let parameters=table(['方程项','系数'],rows.map(([name,value])=>`<tr><th>${e(name)}</th><td class="numeric">${e(value)}</td></tr>`));
  if(kind==='histogram_trees') {
    const forest=estimator.outputs[0];tree=Math.max(0,Math.min(forest.trees.length-1,Number(tree)||0));
    parameters=`<label class="sq-field"><span>收益响应树</span><select id="${e(controlPrefix)}-tree-index">${forest.trees.map((_,index)=>`<option value="${index}" ${tree===index?'selected':''}>${index+1} / ${forest.trees.length}</option>`).join('')}</select></label>`+table(['节点','条件 / 叶子值','满足','否则'],(forest.trees[tree]||[]).map((node,index)=>`<tr><th>${index}</th><td>${node[5]?`叶子 = ${e(node[0])}`:`${featureSymbol(node[1])} ≤ ${e(node[2])}`}</td><td>${node[5]?'—':node[3]}</td><td>${node[5]?'—':node[4]}</td></tr>`));
  }
  const norm=a.featureConstruction.targetSpecification.normalization;
  const unit=norm.kind==='none'?'ŷ = r̂':`ŷ = r̂ / (σorigin × √h)；σorigin：${norm.windowSessions} 个已知交易日的收益标准差`;
  return `<div class="sq-saved-model" data-return-model="${e(symbol)}"><div class="sq-model-formulas" data-saved-function="${e(a.artifactId)}"><span class="sq-kicker">${e(symbol)} · ${e(output)} · ${e(a.provenance?.estimator||fit.estimator||kind)}</span><pre class="sq-model-equation">Fᵢ,ₕ(X) = ${e(modelFormula(a,0))}</pre><div class="sq-model-output-definition"><code>${e(unit)}</code></div><span>${e(returnTiming(a))}</span><div class="sq-model-identity"><span>${a.lineage?.status==='UNVALIDATED_USER_EDIT'?'未验证的派生函数':'已保存的单资产函数'}</span><code>${e(a.artifactId)}</code></div></div><div class="sq-model-meta"><span>训练 ${e(a.training.trainStart)} — ${e(a.training.trainEnd)}</span><span>${e(a.training.trainRows)} 行</span><span>h = ${e(a.scope.horizonSessions)}</span></div><h3>模型参数</h3>${parameters}${chart}${kind==='basis_linear'?F.advanced('基函数训练尺度',raw(estimator)):''}${renderFunctionInputs(C,F,a)}${F.advanced('函数定义与来源',raw({identity:a.identity,training:a.training,scope:a.scope,featureConstruction:a.featureConstruction,provenance:a.provenance,lineage:a.lineage}))}</div>`;
}
