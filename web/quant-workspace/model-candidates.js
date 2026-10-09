import { renderSavedModel } from './report-model.js';
import { candidateScoreChart, trainingFitChart } from './report-charts.js';

// Candidate functions are separate from the chosen rolling forecast function.
// Bounded pages carry summaries; only the selected immutable function is fetched.
export function createModelCandidates(C, F, { remote, remotePages, table, functionEditor }) {
  const { esc: e, render, fmt, openModal } = C;
  const state = { id: '', item: null, request: 0, loading: false, error: '', report: null, output: 1, tree: 0, page: 1 };
  const metadata = r => (r.forecasts?.diagnostics || r.validation)?.modelSearch;
  function reset() { state.request++; Object.assign(state, { id: '', item: null, loading: false, error: '', report: null, output: 1, tree: 0, page: 1 }); }
  function load(id) {
    const request = ++state.request;
    Object.assign(state, { id, item: null, loading: true, error: '' });
    Promise.resolve().then(() => remote.detail('modelSearchCandidates', id)).then(response => {
      if (state.request !== request || !response) return;
      if (response.item?.id !== id) throw Error('候选函数身份不一致。');
      state.item = response.item;
    }).catch(error => { if (state.request === request) state.error = error.message; })
      .finally(() => { if (state.request === request) { state.loading = false; render(); } });
  }
  function selected() {
    if (remote.enabled()) return state.item;
    return metadata(state.report)?.candidates?.find(x => x.id === state.id);
  }
  function view(r) {
    const meta = metadata(r);
    if (!meta) return '';
    state.report = r;
    const page = remote.enabled() ? remote.page('modelSearchCandidates') : null;
    const all = meta.candidates || [], localPages = Math.max(1,Math.ceil(all.length/25));
    state.page = Math.min(state.page,localPages);
    const candidates = page ? page.items : all.slice((state.page-1)*25,state.page*25);
    if (!state.id) state.id = meta.researchCandidateId || (meta.selectedCandidateId === 'per_target' ? meta.selectedCandidateIds?.[0] : meta.selectedCandidateId) || candidates.find(x => x.functionArtifact)?.id || '';
    if (page && !page.unavailable && state.id && state.item?.id !== state.id && !state.loading && !state.error) load(state.id);
    const item = selected(), fit = item && { ...item.fit, id: item.id, estimator: item.estimator, params: item.params, status: item.status, functionArtifact: item.functionArtifact };
    const options = candidates.some(x => x.id === state.id) ? candidates : state.id ? [{ id: state.id, estimator: item?.estimator, ...item }, ...candidates] : candidates;
    const picker = `<label class="sq-field sq-model-fit-picker"><span>候选方程</span><select id="sq-model-candidate">${options.map(x => `<option value="${e(x.id)}" ${x.id === state.id ? 'selected' : ''}>${e((x.symbols||[]).join(' / ')+(x.symbols?.length?' · ':'')+x.id)}${(x.id === meta.selectedCandidateId || meta.selectedCandidateIds?.includes(x.id)) ? ' · 已采用' : x.id === meta.researchCandidateId ? ' · 最佳非基线候选' : ''}</option>`).join('')}</select></label>`;
    const primary = fit ? renderSavedModel(C, F, fit, { output: state.output, tree: state.tree, controlPrefix: 'sq-candidate-model' }) : state.error ? F.note(state.error, 'error') + F.button('forecast-candidate-retry', '重试读取候选', { small: true }) : page?.unavailable ? F.note('这份归档未保存候选方程。') : '<div class="sq-loading" role="status">正在读取候选函数…</div>';
    const badges = `<div class="sq-candidate-identity"><span class="sq-status">采用：${e(meta.selectedCandidateId === 'per_target' ? '逐标的选择' : meta.selectedCandidateId || '未提供')}</span><span class="sq-status ${!(state.id === meta.selectedCandidateId || meta.selectedCandidateIds?.includes(state.id)) ? 'warning' : ''}">${(state.id === meta.selectedCandidateId || meta.selectedCandidateIds?.includes(state.id)) ? '当前查看已采用模型' : '当前查看研究候选'}</span><span>${meta.parameterSharing === 'per_target' ? '逐标的独立参数' : meta.parameterSharing === 'pooled' ? '票池共享参数' : ''}</span></div>`;
    const columns = ['候选', '验证损失', '训练 MSE', '训练 R²', '状态'];
    const scores = table(columns, candidates.map(x => `<tr><td>${F.button('forecast-candidate-select', x.id, { id: x.id, small: true })}</td><td class="numeric">${fmt(x.validationScore, 7)}</td><td class="numeric">${fmt(x.trainingMetrics?.mse, 7)}</td><td class="numeric">${fmt(x.trainingMetrics?.rSquared ?? x.trainingMetrics?.r2, 5)}</td><td>${e((x.id === meta.selectedCandidateId || meta.selectedCandidateIds?.includes(x.id)) ? '已采用' : x.id === meta.researchCandidateId ? '最佳非基线候选' : x.status || '—')}</td></tr>`));
    return F.panel('研究方程', badges + primary + (item?.functionArtifact ? `<div class="sq-actions">${F.button('forecast-candidate-edit', '修改与试算这个 F', { id: item.id, primary: true })}</div>` : ''), { actions: picker, className: 'sq-model-primary sq-candidate-primary' }) +
      (item?.trainingPlot ? F.panel('训练拟合数据', trainingFitChart(item.trainingPlot,e)) : '') +
      F.panel('模型比较', candidateScoreChart(candidates, e) + scores + (page ? remotePages(page) : localPages > 1 ? `<div class="sq-catalog-pagination"><span>${all.length} 个候选 · ${state.page} / ${localPages}</span><div>${F.button('forecast-candidate-page','上一页',{page:state.page-1,small:true,disabled:state.page<=1})}${F.button('forecast-candidate-page','下一页',{page:state.page+1,small:true,disabled:state.page>=localPages})}</div></div>` : '') + F.advanced('固定开发期与逐次预测', F.note('候选方程只使用最终测试集之前的成熟样本拟合。实际测试预测使用下方“已采用的滚动模型”，研究候选不会替换已冻结预测。')));
  }
  function choose(id) { Object.assign(state, { id, item: null, error: '', loading: false, output: 1, tree: 0 }); state.request++; render(); }
  async function handle(el) {
    const action = el.dataset.sq;
    if (action === 'forecast-candidate-page') { state.page=Number(el.dataset.page);render();return true; }
    if (action === 'forecast-candidate-select') { choose(el.dataset.id); return true; }
    if (action === 'forecast-candidate-retry') { load(state.id); render(); return true; }
    if (action === 'forecast-candidate-edit') {
      const item = selected();
      if (item?.id === el.dataset.id && item.functionArtifact) openModal('修改与试算候选 F', functionEditor.render({ ...item.fit, id: item.id, functionArtifact: item.functionArtifact }, { runId: C.state.runId, bundleId: remote.transport?.bundleId || null, modelSearchCandidateId: item.id }), true);
      return true;
    }
    return false;
  }
  function onChange(el) {
    if (el.id === 'sq-model-candidate') { choose(el.value); return true; }
    if (el.closest?.('.sq-candidate-primary') && el.id === 'sq-candidate-model-tree-output') { state.output = Number(el.value); state.tree = 0; render(); return true; }
    if (el.closest?.('.sq-candidate-primary') && el.id === 'sq-candidate-model-tree-index') { state.tree = Number(el.value); render(); return true; }
    return false;
  }
  return { view, handle, onChange, reset, state };
}
