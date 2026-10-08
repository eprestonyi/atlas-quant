// Edits derive new immutable functions. Server resolves the owner-bound source and performs numeric inference.
export function createModelFunctionEditor(C, F) {
  const { esc: e, fmt, api, openModal, download, toast } = C;
  let current = null, sequence = 0, libraryRequest = 0;
  const hash = x => /^[a-f0-9]{64}$/.test(x || '');
  const id = x => /^[a-f0-9-]{36}$/.test(x || '');
  const sourceReady = x => x && (id(x.functionId) && hash(x.artifactId) || id(x.runId) && hash(x.bundleId) && /^[A-Za-z0-9_.:-]{1,160}$/.test(x.modelFitId || ''));
  const raw = x => `<pre class="sq-report-code">${e(JSON.stringify(x, null, 2))}</pre>`;
  const table = (head, rows) => `<div class="sq-table-scroll"><table class="sq-table"><thead><tr>${head.map(x => `<th>${e(x)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
  const at = (a, path) => path.slice(1).split('/').reduce((v, k) => v?.[k], a);
  const field = (state, key, label, help = '', multiline = false) => `<label class="sq-field"><span>${e(label)}</span>${multiline ? `<textarea rows="7" data-mfe-input="${key}">${e(state.fields[key])}</textarea>` : `<input data-mfe-input="${key}" value="${e(state.fields[key])}" ${key === 'name' ? 'maxlength="160"' : ''}>`}${help ? `<small>${e(help)}</small>` : ''}</label>`;
  function parameter(state, path, label) {
    return `<label class="sq-field"><span>${e(label)}</span><input type="number" step="any" data-mfe-param="${e(path)}" value="${e(state.edits.get(path) ?? at(state.artifact, path))}" required></label>`;
  }
  function treeControls(state) {
    const outputs = state.artifact.estimator.outputs, out = Math.min(1, Math.max(0, Number(state.fields.output) || 0));
    const trees = outputs[out].trees, treeIndex = Math.min(trees.length - 1, Math.max(0, Number(state.fields.tree) || 0));
    const tree = trees[treeIndex], leaves = tree.map((node, index) => ({ node, index })).filter(x => x.node[5] === 1);
    const selected = leaves.some(x => String(x.index) === state.fields.leaf) ? state.fields.leaf : String(leaves[0]?.index ?? '');
    state.fields.leaf = selected;
    return `<p>树的分裂条件、拓扑和训练变换保持冻结。可以修改森林基准与指定叶子输出；叶子值已含学习率。</p><div class="sq-form-grid">${[0,1].map(n => parameter(state, `/estimator/outputs/${n}/baseline`, n ? '未来状态森林基准' : '入场状态森林基准')).join('')}<label class="sq-field"><span>森林输出</span><select data-mfe-input="output"><option value="0" ${out === 0 ? 'selected' : ''}>入场状态</option><option value="1" ${out === 1 ? 'selected' : ''}>未来状态</option></select></label><label class="sq-field"><span>树编号</span><select data-mfe-input="tree">${trees.map((_,n) => `<option value="${n}" ${n === treeIndex ? 'selected' : ''}>${n}</option>`).join('')}</select></label><label class="sq-field"><span>叶子编号 / 原值</span><select data-mfe-input="leaf">${leaves.map(x => `<option value="${x.index}" ${String(x.index) === selected ? 'selected' : ''}>${x.index} · ${e(x.node[0])}</option>`).join('')}</select></label>${field(state, 'leafValue', '新叶子输出', '填写有限数值，再加入修改。')}</div>${F.button('mfe-leaf', '加入叶子修改', { small: true })}${F.advanced('当前树的冻结节点', raw(tree))}`;
  }
  function parameters(state) {
    const a = state.artifact, k = a.estimator.kind;
    let body;
    if (k === 'constant') body = `<div class="sq-form-grid">${[0,1].map(n => parameter(state, `/estimator/value/${n}`, n ? '未来状态常量' : '入场状态常量')).join('')}</div>`;
    else if (k === 'linear') body = `<div class="sq-form-grid">${[0,1].map(n => parameter(state, `/estimator/intercepts/${n}`, n ? '未来状态截距' : '入场状态截距')).join('')}</div>` + table(['输入（训练变换后）', '入场输出系数', '未来输出系数'], a.inputSchema.map((x, n) => `<tr><th scope="row">${e(x.name)}</th>${[0,1].map(o => `<td>${parameter(state, `/estimator/coefficients/${o}/${n}`, `${x.name} · ${o ? '未来' : '入场'}`)}</td>`).join('')}</tr>`));
    else body = treeControls(state);
    return F.advanced('修改 F 的数值参数', body + F.note('修改后是未验证的新函数；原函数、预测与统计结果保持冻结。修改值不继承原模型的 IC、误差或拟合结论。'), true);
  }
  function changes(state) {
    const edits = [];
    for (const [path, text] of state.edits) {
      if (!String(text).trim() || !Number.isFinite(Number(text))) throw Error('所有修改参数必须填写有限数值。');
      const value = Number(text);
      if (value !== at(state.artifact, path)) edits.push({ path, value });
    }
    if (edits.length > 256) throw Error('单次最多修改 256 个数值参数。');
    return edits;
  }
  function parseInput(state) {
    let rows;
    try { rows = JSON.parse(state.fields.rows); } catch { throw Error('X 输入需要是有效 JSON 数组。'); }
    if (!Array.isArray(rows) || rows.length < 1 || rows.length > 256) throw Error('一次试算需要 1–256 行输入。');
    const names = state.artifact.inputSchema.map(x => x.name).sort();
    for (const row of rows) {
      if (!row || typeof row !== 'object' || Array.isArray(row) || JSON.stringify(Object.keys(row).sort()) !== JSON.stringify(names) || Object.values(row).some(x => x !== null && (typeof x !== 'number' || !Number.isFinite(x)))) throw Error('每一行必须包含完整输入名，值为有限数字或明确的 null。');
    }
    const context = (key, positive) => {
      if (!state.fields[key].trim()) throw Error('请填写当前状态 P 与正的尺度 scale；不会自动填充市场数值。');
      let result;
      try { result = JSON.parse(state.fields[key]); } catch { throw Error('P 与 scale 需要是数字或与行数一致的 JSON 数组。'); }
      result = Array.isArray(result) ? result : [result];
      if (result.length !== rows.length || result.some(x => typeof x !== 'number' || !Number.isFinite(x) || positive && x <= 0)) throw Error('P 与 scale 必须逐行对应；scale 为正的有限数值。');
      return result;
    };
    return { rows, currentState: context('currentState', false), scale: context('scale', true) };
  }
  function resultView(state) {
    if (!state.result) return '';
    const { response, input, revision } = state.result, result = response.result;
    return F.panel('用户输入的 F(X) 试算',
      `<div data-mfe-stale ${revision === state.revision ? 'hidden' : ''}>${F.note('输入或参数已修改；下方仍是上次提交时的试算结果。', 'warning')}</div>` +
      `<code>${e(result.artifactId)}</code>` + table(['行', '当前 P', '预期入场', '未来 V', 'E = P − V', '预期变化 −E'], (result.levels || []).map((x,n) => `<tr><td>${n + 1}</td>${[input.currentState[n], x.expectedEntry, x.expectedFuture, x.e, x.expectedChange].map(v => `<td class="numeric">${fmt(v, 6)}</td>`).join('')}</tr>`)) +
      F.note('这只是给定数值输入的函数求值，没有调用实时市场数据、重新训练或运行历史回测。新验证状态：未进行。'));
  }
  function view(state) {
    const a = state.artifact, k = a.estimator.kind, eligible = sourceReady(state.source);
    const formula = k === 'linear' ? 'g_j(X) = b_j + Σ β_jk · T_k(X_k)' : k === 'constant' ? 'g_j(X) = c_j' : 'g_j(X) = baseline_j + Σ tree_jm(T(X))';
    const pending = [...state.edits].filter(([p,v]) => String(at(a,p)) !== String(v));
    return `<section class="mfe-editor" data-mfe-key="${state.key}"><h3>可复用的 F 函数</h3><div class="sq-core-equation">${e(formula)}<br><small>V = P + scale · g₁(X)；E = P − V</small></div><p>T 使用本次训练冻结的截尾、缺失填充和标准化。两个输出分别描述入场与未来状态相对当前已知尺度的变化。</p><dl class="sq-key-values"><dt>函数身份</dt><dd><code>${e(a.artifactId)}</code></dd><dt>当前版本来源</dt><dd>${e(a.lineage?.status || '未返回')}</dd><dt>训练截止</dt><dd>${e(a.training?.informationCutoff)}</dd><dt>期限 / 观察间隔</dt><dd>${e(a.scope?.horizonSessions)} / ${e(a.scope?.observationDays)} 交易日</dd><dt>证券范围</dt><dd>${e(a.scope?.symbols?.join('、') || '未提供')}</dd></dl>${F.note('原研究范围以外的适用性尚未验证。输入须按原特征定义构建，不能把任意股票或任意单位直接代入。')}${state.error ? F.note(state.error, 'error') : ''}${state.notice ? F.note(state.notice) : ''}${parameters(state)}${pending.length ? F.advanced(`待派生参数 · ${pending.length} 项`, table(['路径', '新值', '操作'], pending.map(([path,value]) => `<tr><td><code>${e(path)}</code></td><td>${e(value)}</td><td>${F.button('mfe-remove', '撤销', { id: path, small: true })}</td></tr>`)), true) : ''}<div class="sq-actions">${F.button('mfe-download', '下载原函数 JSON', { small: true })}${F.button('mfe-resolve', '核验函数来源', { small: true, disabled: !eligible || state.busy })}${F.button('mfe-library', '已保存的函数版本', { small: true })}</div>${!eligible ? F.note('当前报告没有完整的私有来源引用；可以读取原函数，在线试算和派生保存尚不可用。') : ''}${F.panel('给 F 提供试算输入', field(state, 'rows', 'X 行数组 · 待填示例', '初始 null 只是待填结构，不是市场数据。null 会使用训练时冻结的缺失处理。最多 256 行。', true) + `<div class="sq-form-grid">${field(state, 'currentState', '当前状态 P', '单行填数字；多行填等长数组。')}${field(state, 'scale', '当前已知尺度 scale', '原目标的总绝对腿价值，必须为正；须与 P 和模型保持同一单位。')}</div>${F.button('mfe-evaluate', state.busy === 'evaluate' ? '正在试算…' : '运行 F(X) 试算', { primary: true, disabled: !eligible || !!state.busy })}`)}${resultView(state)}${F.panel('保存独立的派生函数', field(state, 'name', '新函数名称') + F.button('mfe-derive', state.busy === 'derive' ? '正在保存…' : '保存新的函数版本', { primary: true, disabled: !eligible || !!state.busy }) + '<p class="sq-subtle">需要至少一项实际参数修改。保存新函数不会覆盖报告，也不会自动生成新的统计验证。</p>')}${state.saved ? F.panel('已保存派生版本', `<code>${e(state.saved.ref.artifactId)}</code><p>UNVALIDATED_USER_EDIT · 未继承父模型统计检验</p>${F.button('mfe-open-saved', '打开这个函数版本', { id: state.saved.ref.functionId, artifact: state.saved.ref.artifactId, small: true })}`) : ''}${F.advanced('完整输入变换与函数来源', raw({ transforms: a.transforms, featureConstruction: a.featureConstruction, training: a.training, scope: a.scope, provenance: a.provenance, lineage: a.lineage }))}</section>`;
  }
  function render(fit, source) {
    const a = fit?.functionArtifact;
    if (!a) return F.note('此拟合记录未保存可移植的 F 函数。不能从旧报告的摘要重建系数或假装导出函数。');
    if (a.schema !== 'atlas-model-function/1' || !hash(a.artifactId) || !Array.isArray(a.inputSchema) || !['constant','linear','histogram_trees'].includes(a.estimator?.kind)) return F.note('函数格式不受支持，未启用编辑。', 'error');
    const identity = JSON.stringify([a.artifactId, source]);
    if (current?.identity !== identity) current = { identity, key: ++sequence, source: structuredClone(source || {}), artifact: structuredClone(a), edits: new Map(), fields: { rows: JSON.stringify([Object.fromEntries(a.inputSchema.map(x => [x.name, null]))], null, 2), currentState: '', scale: '', name: '派生 F · ' + a.artifactId.slice(0, 10), output: '1', tree: '0', leaf: '', leafValue: '' }, revision: 0, busy: '', error: '', notice: '', result: null, saved: null, request: null };
    return view(current);
  }
  function mounted(state) { return document.querySelector(`[data-mfe-key="${state.key}"]`); }
  function refresh(state) { const node = current === state && mounted(state); if (node) node.outerHTML = view(state); }
  function onInput(el) {
    const state = current;
    if (!state || !el.closest?.(`[data-mfe-key="${state.key}"]`)) return;
    if (el.dataset.mfeParam) state.edits.set(el.dataset.mfeParam, el.value);
    else if (el.dataset.mfeInput) state.fields[el.dataset.mfeInput] = el.value;
    else return;
    state.revision++;
    const stale = mounted(state)?.querySelector('[data-mfe-stale]'); if (stale) stale.hidden = false;
    if (['output','tree'].includes(el.dataset.mfeInput)) { state.fields.leaf = ''; if (el.dataset.mfeInput === 'output') state.fields.tree = '0'; refresh(state); }
  }
  async function library(offset = 0) {
    const request = ++libraryRequest;
    const marker = `<div data-mfe-library="${request}">`;
    const active = () => request === libraryRequest && document.querySelector(`[data-mfe-library="${request}"]`);
    openModal('已保存的函数版本', marker + '<div class="sq-loading">正在读取私有函数…</div></div>', true);
    try {
      const response = await api('/model-functions?offset=' + offset);
      if (!active()) return;
      openModal('已保存的函数版本', marker + table(['名称', '状态', '操作'], (response.items || []).map(x => `<tr><td>${e(x.name)}<small><code>${e(x.artifactId)}</code></small></td><td>未验证的派生函数</td><td>${F.button('mfe-open-saved', '打开', { id: x.ref?.functionId || x.id, artifact: x.ref?.artifactId || x.artifactId, small: true })}</td></tr>`)) + (!response.items?.length ? F.note('还没有保存的派生函数。') : '') + `<div class="sq-actions">${offset ? F.button('mfe-library', '返回首页', { small: true }) : ''}${response.nextOffset != null ? F.button('mfe-library', '下一页', { offset: response.nextOffset, small: true }) : ''}</div></div>`, true);
    } catch (error) { if (active()) openModal('已保存的函数版本', marker + F.note(error.message, 'error') + F.button('mfe-library', '重试', { offset, small: true }) + '</div>', true); }
  }
  async function handle(el) {
    const action = el.dataset.sq;
    if (!action?.startsWith('mfe-')) return false;
    if (action === 'mfe-library') { await library(Number(el.dataset.offset) || 0); return true; }
    if (action === 'mfe-open-saved') {
      const request = ++libraryRequest, functionId = el.dataset.id, artifactId = el.dataset.artifact;
      const marker = `<div data-mfe-library="${request}">`;
      const active = () => request === libraryRequest && document.querySelector(`[data-mfe-library="${request}"]`);
      openModal('读取函数版本', marker + '<div class="sq-loading">正在核验私有函数来源…</div></div>', true);
      try {
        const response = await api(`/model-functions/${encodeURIComponent(functionId)}?artifactId=${encodeURIComponent(artifactId)}`);
        if (active()) openModal(response.item?.name || '派生函数', render({ functionArtifact: response.artifact }, response.ref), true);
      } catch (error) { if (active()) openModal('读取函数版本', marker + F.note(error.message, 'error') + F.button('mfe-open-saved', '重试读取', { id: functionId, artifact: artifactId, small: true }) + '</div>', true); }
      return true;
    }
    const state = current;
    if (!state || !el.closest?.(`[data-mfe-key="${state.key}"]`)) return true;
    if (action === 'mfe-download') { download(`atlas-function-${state.artifact.artifactId}.json`, state.artifact); return true; }
    if (action === 'mfe-remove') { state.edits.delete(el.dataset.id); state.revision++; refresh(state); return true; }
    if (state.busy) return true;
    try {
      state.error = ''; state.notice = '';
      if (action === 'mfe-leaf') {
        const out = Number(state.fields.output), tree = Number(state.fields.tree), leaf = Number(state.fields.leaf), node = state.artifact.estimator.outputs?.[out]?.trees?.[tree]?.[leaf];
        if (!node || node[5] !== 1 || !state.fields.leafValue.trim() || !Number.isFinite(Number(state.fields.leafValue))) throw Error('选择有效叶子并填写有限数值。');
        state.edits.set(`/estimator/outputs/${out}/trees/${tree}/${leaf}/0`, state.fields.leafValue); state.revision++; refresh(state); return true;
      }
      const revision = state.revision, edits = changes(state);
      if (!sourceReady(state.source)) throw Error('缺少完整的私有函数来源引用。');
      let payload, endpoint;
      if (action === 'mfe-evaluate') {
        payload = { source: state.source, input: parseInput(state), ...(edits.length ? { edits } : {}) }; endpoint = 'evaluate';
      } else if (action === 'mfe-derive') {
        if (!edits.length) throw Error('先修改至少一个函数参数，再保存派生版本。');
        const name = state.fields.name.trim(); if (!name || name.length > 160) throw Error('填写 1–160 字符的新函数名称。');
        const requestKey = JSON.stringify({ source: state.source, edits, name });
        if (state.request?.key !== requestKey) state.request = { key: requestKey, id: crypto.randomUUID() };
        payload = { source: state.source, edits, name, requestId: state.request.id }; endpoint = 'derive';
      } else if (action === 'mfe-resolve') { payload = { source: state.source }; endpoint = 'resolve'; }
      else return true;
      state.busy = endpoint; refresh(state);
      const response = await api('/model-functions/' + endpoint, { method: 'POST', body: JSON.stringify(payload) });
      if (current !== state || !mounted(state)) { if (endpoint === 'derive') toast('此前提交的派生函数已保存，可在函数版本列表读取。'); return true; }
      if (endpoint === 'resolve') { if (response.artifact?.artifactId !== state.artifact.artifactId) throw Error('来源返回了不同的函数身份。'); state.notice = '已从私有来源核验这个函数版本。'; }
      if (endpoint === 'evaluate') {
        if (response.inferenceOnly !== true || response.newValidationPerformed !== false || !hash(response.result?.artifactId) || !Array.isArray(response.result.levels) || response.result.levels.length !== payload.input.rows.length) throw Error('试算返回的结果或验证边界无效。');
        if ((!edits.length && response.result.artifactId !== state.artifact.artifactId) || (edits.length && response.result.evidenceStatus !== 'UNVALIDATED_USER_EDIT') || response.result.levels.some(row => ['expectedEntry','expectedFuture','e','expectedChange'].some(key => !Number.isFinite(row[key])))) throw Error('函数试算的身份、数值或派生状态不一致。');
        state.result = { response, input: payload.input, revision };
      }
      if (endpoint === 'derive') {
        if (!sourceReady(response.item?.ref) || response.item.ref.artifactId !== response.artifact?.artifactId || response.artifact?.lineage?.parentArtifactId !== state.artifact.artifactId || response.artifact?.lineage?.status !== 'UNVALIDATED_USER_EDIT') throw Error('派生版本没有返回正确的父函数与未验证状态。');
        state.saved = response.item;
        state.notice = revision === state.revision ? '新函数版本已保存，原始拟合结果保持不变。' : '提交时的函数版本已保存；当前后续修改尚未保存。';
      }
    } catch (error) { if (current === state) state.error = error.message; }
    finally { state.busy = ''; refresh(state); }
    return true;
  }
  return { render, handle, onInput, library };
}
