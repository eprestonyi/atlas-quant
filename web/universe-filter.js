// A single set editor: recommendations, rules and resulting membership share state.
export function universeFilter({ esc: e, icon: i, action, fmt, selection, state,
  fields, filterEditor, members, directory }) {
  const selectedPools = new Set(selection.includeGroups.flatMap(g =>
    g.filters.filter(f => f.field === 'universe').flatMap(f => f.value)));
  const pools = fields.find(f => f.field === 'universe')?.values || [];
  const preferred = pools.filter(p => /沪深300|中证500|中证1000|上证50|创业板|科创50/.test(p.label));
  const recommendations = [...preferred, ...pools.filter(p => !preferred.includes(p))].slice(0, 6);
  const group = (g, scope, n) => {
    const exclusion = scope === 'excludeGroups';
    const result = state.resolution?.steps?.filter(x => x.groupId === g.id).at(-1);
    return `<article class="uf-group ${exclusion ? 'exclude' : ''}">
      <header><strong>${exclusion ? '剔除' : n ? '或纳入' : '纳入'}</strong><span>${result && !state.dirty ? `${result.before} → ${result.after}` : ''}</span>
      ${action('pool-remove-group', '删除', 'close', 'subtle small', `data-id="${e(g.id)}" data-scope="${scope}"`)}</header>
      ${g.filters.map((f, index) => filterEditor(g, f, index, scope)).join('')}
      ${action('pool-add-filter', '且满足', 'plus', 'ghost small', `data-id="${e(g.id)}" data-scope="${scope}"`)}</article>`;
  };
  const query = state.query.trim().toLowerCase();
  const rows = members.filter(x => !query || `${x.ts_code} ${x.name || ''} ${x.area || ''} ${x.industry || ''}`.toLowerCase().includes(query));
  const pageCount = Math.max(1, Math.ceil(rows.length / 40));
  const page = Math.min(state.page, pageCount);
  const shown = rows.slice((page - 1) * 40, page * 40);
  const metadata = members.some(x => x.name || x.industry || x.area);
  return `<section class="uf-workbench" aria-label="股票筛选工作台">
    <div class="uf-presets"><span>快捷条件</span>${recommendations.map(p => action('pool-preset', e(p.label), selectedPools.has(p.value) ? 'check' : 'plus', 'ghost small', `data-id="${e(p.value)}" aria-pressed="${selectedPools.has(p.value)}"`)).join('')}</div>
    <details class="uf-directory"><summary>票池目录 <span>${fmt(directory.total || pools.length, 0)}</span></summary>
      <label class="v2-search">${i('search')}<input id="v2-universe-search" aria-label="搜索股票池" placeholder="搜索指数、行业、主题" value="${e(directory.query || '')}"></label>
      <div class="uf-pool-list">${directory.loading ? '<span role="status">读取中…</span>' : directory.items.map(p => `<div><strong>${e(p.name || p.id)}</strong><span>${fmt(p.symbolCount || p.symbols?.length || 0, 0)} 只</span>${action('pool-preset', '加入', 'plus', 'ghost small', `data-id="${e(p.id)}" ${directory.ready(p) ? '' : 'disabled'}`)}</div>`).join('') || '<span>没有匹配票池</span>'}</div>
      <div class="uf-pagination"><span>${directory.page} / ${Math.max(1, Math.ceil(directory.total / 12))}</span>${action('universe-page', '上一页', '', 'ghost small', `data-page="${directory.page - 1}" ${directory.page <= 1 ? 'disabled' : ''}`)}${action('universe-page', '下一页', '', 'ghost small', `data-page="${directory.page + 1}" ${directory.page * 12 >= directory.total ? 'disabled' : ''}`)}</div>
    </details>
    <div class="uf-rules">
      ${selection.includeGroups.map((g, n) => group(g, 'includeGroups', n)).join('')}
      ${selection.excludeGroups.map((g, n) => group(g, 'excludeGroups', n)).join('')}
      <div class="uf-rule-actions">${action('pool-add-group', '纳入条件', 'plus', 'ghost small', 'data-scope="includeGroups"')}${action('pool-add-group', '剔除条件', 'minus', 'ghost small', 'data-scope="excludeGroups"')}</div>
      <details class="uf-symbols"><summary>按代码增减</summary><div class="field-row"><label class="field"><span>加入</span><textarea id="rq-include-symbols" placeholder="000001.SZ, 600000.SH">${e(state.includeText ?? selection.includeSymbols.join(', '))}</textarea></label><label class="field"><span>剔除</span><textarea id="rq-exclude-symbols" placeholder="000002.SZ">${e(state.excludeText ?? selection.excludeSymbols.join(', '))}</textarea></label></div></details>
    </div>
    <header class="uf-result-header"><div><span>当前票池</span><strong>${state.resolution && !state.dirty ? fmt(state.resolution.symbolCount, 0) : '—'}<small>只</small></strong><span role="status">${state.resolving ? '更新中' : state.dirty ? '待更新' : state.resolution?.restored ? '已保存的冻结筛选集合' : ''}</span></div>${action('pool-resolve', state.resolving ? '筛选中…' : '更新筛选', 'refresh', 'primary', state.resolving ? 'disabled' : '')}</header>
    ${state.error ? `<p class="notice error" role="alert">${e(state.error)}</p>` : ''}
    <div class="uf-results"><label class="v2-search">${i('search')}<input id="rq-member-search" aria-label="查找筛选结果" value="${e(state.query)}" placeholder="查找代码、名称、行业、地区"></label>
      <div class="table-wrap"><table><thead><tr><th>代码</th>${metadata ? '<th>名称</th><th>行业</th><th>地区</th>' : ''}<th><span class="sr-only">操作</span></th></tr></thead><tbody>${shown.map(x => `<tr><td>${e(x.ts_code)}</td>${metadata ? `<td>${e(x.name || '—')}</td><td>${e(x.industry || '—')}</td><td>${e(x.area || '—')}</td>` : ''}<td>${action('pool-exclude-symbol', '剔除', 'minus', 'subtle small', `data-id="${e(x.ts_code)}" ${state.dirty || state.resolving ? 'disabled' : ''}`)}</td></tr>`).join('') || `<tr><td colspan="${metadata ? 5 : 2}">${state.resolution ? '没有匹配股票' : '选择条件开始筛选'}</td></tr>`}</tbody></table></div>
      <div class="uf-pagination"><span>${fmt(rows.length, 0)} 只 · ${page} / ${pageCount}</span>${action('pool-page', '上一页', '', 'ghost small', `data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}`)}${action('pool-page', '下一页', '', 'ghost small', `data-page="${page + 1}" ${page >= pageCount ? 'disabled' : ''}`)}</div>
    </div>
  </section>`;
}
