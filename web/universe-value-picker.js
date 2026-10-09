// Search is presentation state only; membership changes only on an explicit click.
export function filterValueOptions({ esc: e, action, values, selected, query = '', group, index, scope }) {
  const tokens = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const matches = values.filter(x => tokens.every(t => `${x.label} ${x.value} ${(x.aliases || []).join(' ')}`.toLocaleLowerCase().includes(t)));
  const shown = matches.slice(0, 24);
  const attrs = value => `data-id="${e(group)}" data-index="${index}" data-scope="${scope}" data-value="${e(value)}"`;
  return `<div class="uf-value-options" role="group" aria-label="匹配筛选条件">${shown.map(x => action('pool-toggle-value', `${e(x.label)}${x.count !== undefined ? `<small>${e(x.count)} 只</small>` : ''}`, selected.includes(x.value) ? 'check' : 'plus', 'ghost small', `${attrs(x.value)} aria-pressed="${selected.includes(x.value)}"`)).join('') || '<span class="uf-value-empty">没有匹配条件</span>'}</div><span class="uf-value-count" role="status">${matches.length} 个匹配${matches.length > shown.length ? ' · 输入更多关键词缩小范围' : ''}</span>`;
}

export function filterValuePicker({ esc: e, action, values, selected, query = '', group, index, scope, fieldLabel }) {
  const attrs = value => `data-id="${e(group)}" data-index="${index}" data-scope="${scope}" data-value="${e(value)}"`;
  return `<div class="uf-value-picker"><div class="uf-value-selected" aria-label="已选筛选条件">${selected.filter(Boolean).map(value => action('pool-toggle-value', e(values.find(x => x.value === value)?.label || value), 'close', 'ghost small', `${attrs(value)} aria-label="移除 ${e(values.find(x => x.value === value)?.label || value)}" aria-pressed="true"`)).join('')}</div><label class="uf-value-search"><span class="sr-only">搜索${e(fieldLabel)}条件</span><input type="search" autocomplete="off" data-rq-search="${e(group)}" data-scope="${scope}" data-index="${index}" value="${e(query)}" placeholder="搜索${e(fieldLabel)}${/行业|股票池|指数/.test(fieldLabel) ? '，如白酒、半导体' : ''}"></label><div data-rq-search-results>${filterValueOptions({ esc: e, action, values, selected, query, group, index, scope })}</div></div>`;
}
