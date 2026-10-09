/** Numeric-only portable F(X). Shared by the browser and private Worker API. */
import { validateMetadata } from './model-function-metadata.js';
export const FUNCTION_SCHEMA = 'atlas-model-function/1';
export const FUNCTION_SCHEMAS = Object.freeze([FUNCTION_SCHEMA, 'atlas-model-function/2']);
export const FUNCTION_HASH = 'sha256-canonical-f64-json/1';
export const FUNCTION_LIMITS = Object.freeze({ bytes: 2 * 1024 * 1024, features: 128, rows: 256, trees: 256, nodes: 255, operations: 2000000 });
const OUTPUTS = ['entry_level_change_over_known_gross', 'exit_level_change_over_known_gross'];
const TRANSFORMS = ['imputeMedian', 'winsorLower', 'winsorUpper', 'scaleMean', 'scaleScale'];
const NODE_FIELDS = ['value', 'feature', 'threshold', 'left', 'right', 'leaf', 'missingLeft'];
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const number = x => typeof x === 'number' && Number.isFinite(x);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
export class ModelFunctionError extends Error {
  constructor(message) { super(message); this.code = 'INVALID_MODEL_FUNCTION'; }
}
function require(condition, message) { if (!condition) throw new ModelFunctionError(message); }
function keys(x, expected, label) {
  require(object(x) && equal(Object.keys(x).sort(), [...expected].sort()), label + ' 字段不符合函数协议');
}
function vector(value, length, label) {
  require(Array.isArray(value) && value.length === length && value.every(number), label + ' 需要匹配长度的有限数值');
}
function validString(value) {
  for (let i = 0; i < value.length; i++) {
    const c = value.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const next = value.charCodeAt(++i);
      require(next >= 0xdc00 && next <= 0xdfff, '字符串含无效 Unicode');
    } else require(c < 0xdc00 || c > 0xdfff, '字符串含无效 Unicode');
  }
  return JSON.stringify(value);
}

export function canonicalFunctionText(value) {
  let visited = 0;
  const visit = (x, depth) => {
    require(++visited <= 600000 && depth <= 32, '函数结构超过预算');
    if (x === null || typeof x === 'boolean') return JSON.stringify(x);
    if (typeof x === 'string') return validString(x);
    if (typeof x === 'number') {
      require(number(x), '函数身份只接受有限数值');
      const buffer = new ArrayBuffer(8);
      new DataView(buffer).setFloat64(0, x === 0 ? 0 : x, false);
      const hex = [...new Uint8Array(buffer)].map(v => v.toString(16).padStart(2, '0')).join('');
      return '{"$f64":"' + hex + '"}';
    }
    if (Array.isArray(x)) return '[' + Array.from(x, y => visit(y, depth + 1)).join(',') + ']';
    require(object(x), '函数仅支持 JSON 值');
    return '{' + Object.keys(x).sort().map(key => {
      require(/^[\x00-\x7f]*$/.test(key) && key !== '$f64', '函数键须为 ASCII，且不得使用内部数值标记');
      return JSON.stringify(key) + ':' + visit(x[key], depth + 1);
    }).join(',') + '}';
  };
  return visit(value, 0);
}
export async function functionDigest(value) {
  const bytes = new TextEncoder().encode(canonicalFunctionText(value));
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(hash)].map(v => v.toString(16).padStart(2, '0')).join('');
}

function validateTree(tree, features) {
  require(Array.isArray(tree) && tree.length >= 1 && tree.length <= FUNCTION_LIMITS.nodes, '树节点数量无效');
  const seen = new Set(), pending = [0];
  while (pending.length) {
    const index = pending.pop();
    require(!seen.has(index), '树不能有环或多个父节点');
    seen.add(index);
    const node = tree[index]; vector(node, 7, '树节点');
    const [, feature, , left, right, leaf, missing] = node;
    require([feature, left, right, leaf, missing].every(Number.isInteger) && feature >= 0 && feature < features && [0, 1].includes(leaf) && [0, 1].includes(missing), '树索引无效');
    if (!leaf) {
      require(left > index && right > index && left < tree.length && right < tree.length && left !== right, '树只能引用后续子节点');
      pending.push(left, right);
    }
  }
  require(seen.size === tree.length, '树包含不可到达节点');
}

export async function validateFunction(artifact) {
  require(object(artifact), '需要函数对象');
  let text;
  try { text = JSON.stringify(artifact); } catch { throw new ModelFunctionError('函数仅支持无环的 JSON 值'); }
  require(new TextEncoder().encode(text).length <= FUNCTION_LIMITS.bytes, '函数超过大小预算');
  keys(artifact, ['schema', 'hashAlgorithm', 'artifactId', 'inputSchema', 'transforms', 'estimator', 'training', 'scope', 'outputs', 'identity', 'provenance', 'editPolicy', 'lineage', 'featureConstruction'], '函数');
  require(FUNCTION_SCHEMAS.includes(artifact.schema) && artifact.hashAlgorithm === FUNCTION_HASH && equal(artifact.outputs, OUTPUTS), '函数格式或输出版本不匹配');
  const {artifactId, ...content} = artifact;
  require(typeof artifactId === 'string' && /^[a-f0-9]{64}$/.test(artifactId) && artifactId === await functionDigest(content), '函数内容身份不一致');
  const inputs = artifact.inputSchema;
  require(Array.isArray(inputs) && inputs.length > 0 && inputs.length <= FUNCTION_LIMITS.features, '特征数量无效');
  for (const x of inputs) {
    keys(x, ['name', 'type'], '特征');
    require(typeof x.name === 'string' && x.name.length > 0 && x.name.length <= 160 && x.type === 'finite_number_or_null', '特征定义无效');
  }
  const names = inputs.map(x => x.name), n = names.length;
  require(new Set(names).size === n, '特征名称重复');
  const t = artifact.transforms; keys(t, TRANSFORMS, '训练变换');
  for (const value of Object.values(t)) if (value !== null) vector(value, n, '训练变换');
  for (const [a, b] of [['winsorLower', 'winsorUpper'], ['scaleMean', 'scaleScale']]) require((t[a] === null) === (t[b] === null), '训练变换不完整');
  if (t.winsorLower) require(t.winsorLower.every((x, i) => x <= t.winsorUpper[i]), '截尾边界倒置');
  if (t.scaleScale) require(t.scaleScale.every(x => x > 0), '标准化尺度必须为正');
  const e = artifact.estimator;
  require(object(e), '估计器无效');
  if (e.kind === 'constant') { keys(e, ['kind', 'value'], '常量模型'); vector(e.value, 2, '常量'); }
  else if (e.kind === 'linear') {
    keys(e, ['kind', 'coefficients', 'intercepts'], '线性模型');
    vector(e.intercepts, 2, '截距');
    require(Array.isArray(e.coefficients) && e.coefficients.length === 2, '系数形状无效');
    e.coefficients.forEach(x => vector(x, n, '系数'));
  } else if (e.kind === 'histogram_trees') {
    keys(e, ['kind', 'nodeFields', 'thresholdRule', 'leafValuesIncludeLearningRate', 'outputs'], '树模型');
    require(equal(e.nodeFields, NODE_FIELDS) && e.thresholdRule === 'left_if_less_equal' && e.leafValuesIncludeLearningRate === true && Array.isArray(e.outputs) && e.outputs.length === 2, '树模型编码不兼容');
    for (const out of e.outputs) {
      keys(out, ['baseline', 'trees'], '森林输出');
      require(number(out.baseline) && Array.isArray(out.trees) && out.trees.length > 0 && out.trees.length <= FUNCTION_LIMITS.trees, '森林预算或基准值无效');
      out.trees.forEach(tree => validateTree(tree, n));
    }
  } else require(false, '不支持的估计器');
  require(e.kind === 'constant' || t.imputeMedian !== null, '非恒定模型必须保留训练中位数');
  validateMetadata(artifact, {require, keys, number, equal});
  const kinds = {no_change:'constant',historical_drift:'constant',ridge:'linear',elastic_net:'linear',hist_gradient_boosting:'histogram_trees'};
  require(e.kind === kinds[artifact.provenance.estimator], '估计器编码与来源不一致');
  require(e.kind !== 'constant' || Object.values(t).every(x => x === null), '常量函数不能带有被忽略的变换');
  return artifact;
}

export async function evaluateFunction(artifact, {rows, currentState, scale}) {
  const a = await validateFunction(artifact);
  require(Array.isArray(rows) && rows.length <= FUNCTION_LIMITS.rows, '单次函数试算最多256行');
  const names = a.inputSchema.map(x => x.name), t = a.transforms, estimator = a.estimator;
  let operations = 0;
  const count = () => require(++operations <= FUNCTION_LIMITS.operations, '单次函数试算超过运算预算，请减少输入行数');
  const output = rows.map(row => {
    keys(row, names, '输入行');
    const x = names.map((name, i) => {
      let value = row[name];
      require(value === null || number(value), '输入须为有限数值或明确缺失');
      if (value !== null && t.winsorLower) value = Math.max(t.winsorLower[i], Math.min(t.winsorUpper[i], value));
      if (value === null && t.imputeMedian) value = t.imputeMedian[i];
      if (value !== null && t.scaleMean) value = (value - t.scaleMean[i]) / t.scaleScale[i];
      require(estimator.kind === 'constant' || number(value), '训练变换后的输入超出有限数值范围');
      return value;
    });
    if (estimator.kind === 'constant') return [...estimator.value];
    if (estimator.kind === 'linear') return estimator.coefficients.map((coefficients, i) => {
      let value = 0;
      for (let j = 0; j < x.length; j++) { count(); value += coefficients[j] * x[j]; }
      return value + estimator.intercepts[i];
    });
    return estimator.outputs.map(out => {
      let value = out.baseline;
      for (const tree of out.trees) {
        let index = 0;
        while (!tree[index][5]) { count(); const node = tree[index]; index = x[node[1]] <= node[2] ? node[3] : node[4]; }
        value += tree[index][0];
      }
      return value;
    });
  });
  require(output.every(x => x.every(number)), '函数计算超出有限数值范围');
  const result = {artifactId: a.artifactId, normalizedChanges: output, evidenceStatus: a.lineage.status};
  require((currentState === undefined) === (scale === undefined), '当前状态与尺度必须一起提供');
  if (currentState !== undefined) {
    vector(currentState, rows.length, '当前状态'); vector(scale, rows.length, '当前已知尺度');
    require(scale.every(x => x > 0), '当前已知尺度必须为正');
    result.levels = output.map((x, i) => {
      const expectedEntry = currentState[i] + scale[i] * x[0], expectedFuture = currentState[i] + scale[i] * x[1];
      const e = currentState[i] - expectedFuture, expectedChange = -e;
      require([expectedEntry, expectedFuture, e, expectedChange].every(number), '价格还原或偏离超出有限数值范围');
      return {expectedEntry, expectedFuture, e, expectedChange};
    });
  }
  return result;
}

export async function deriveFunction(artifact, edits) {
  await validateFunction(artifact);
  require(Array.isArray(edits) && edits.length >= 1 && edits.length <= 256, '单次修改需包含1至256个参数');
  const value = structuredClone(artifact), kind = value.estimator.kind;
  for (const edit of edits) {
    keys(edit, ['path', 'value'], '参数修改');
    require(typeof edit.path === 'string' && number(edit.value), '修改须指定路径和有限数值');
    const permitted = kind === 'constant' ? /^\/estimator\/value\/[01]$/ : kind === 'linear' ? /^\/estimator\/(?:intercepts\/[01]|coefficients\/[01]\/(?:0|[1-9]\d*))$/ : /^\/estimator\/outputs\/[01]\/(?:baseline|trees\/(?:0|[1-9]\d*)\/(?:0|[1-9]\d*)\/0)$/;
    require(permitted.test(edit.path), '只允许修改常量、系数、截距、森林基准或叶子值');
    const parts = edit.path.slice(1).split('/'); let target = value;
    for (const part of parts.slice(0, -1)) { require(target !== null && typeof target === 'object' && Object.hasOwn(target, part), '参数路径超出函数范围'); target = target[part]; }
    const last = parts.at(-1);
    require(target !== null && typeof target === 'object' && Object.hasOwn(target, last), '参数路径超出函数范围');
    if (kind === 'histogram_trees' && parts.includes('trees')) require(target[5] === 1, '不能编辑树的拓扑或分支节点');
    target[last] = edit.value;
  }
  value.lineage = {parentArtifactId: artifact.artifactId, status: 'UNVALIDATED_USER_EDIT', edits: structuredClone(edits)};
  delete value.artifactId; value.artifactId = await functionDigest(value);
  return validateFunction(value);
}
