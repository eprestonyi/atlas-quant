import { providerLabel } from './source-labels.js';

export function createIndustryBrowser(C, F) {
  const { esc: e, render } = C;
  const state = { query: '', market: 'CN', level: '', kind: 'all', page: 1 };
  const safeUrl = value => { try { const url = new URL(value); return url.protocol === 'https:' ? url.href : ''; } catch { return ''; } };
  const pendingLabel = x => x.historyStatus === 'adapter_supported_history_unverified' ? '历史待验' : '待接入';
  function view() {
    const catalog = C.state.catalog?.industrySources;
    if (!catalog?.items) return F.note('行业来源目录正在读取。');
    const tokens = state.query.toLowerCase().trim().split(/\s+/).filter(Boolean);
    const all = [...catalog.items, ...(catalog.proxies || [])];
    const items = all.filter(x => (!state.market || x.market === state.market) && (!state.level || String(x.level) === state.level) && (state.kind === 'all' || (state.kind === 'etf' ? x.sourceKind === 'etf_proxy' : x.sourceKind !== 'etf_proxy')) && tokens.every(q => `${x.name} ${x.nameZh || ''} ${x.code || ''} ${x.symbol || ''} ${(x.path || []).join(' ')} ${(x.keywords || []).join(' ')}`.toLowerCase().includes(q)));
    const pageCount = Math.max(1, Math.ceil(items.length / 18)), page = Math.min(state.page, pageCount), shown = items.slice((page - 1) * 18, page * 18);
    const added = new Set(C.state.strategy.factors.map(x => x.id));
    return `<section class="sq-industry-browser"><div class="sq-industry-controls"><label class="sq-search"><input type="search" id="sq-industry-search" aria-label="搜索行业与 ETF" placeholder="白酒、半导体、Software、ETF 代码…" value="${e(state.query)}"></label><select id="sq-industry-market" aria-label="行业市场">${[['CN','A 股'],['US','美股'],['','全部市场']].map(([v,t])=>`<option value="${v}" ${state.market===v?'selected':''}>${t}</option>`).join('')}</select><select id="sq-industry-level" aria-label="行业层级"><option value="">全部层级</option>${[1,2,3,4].map(n=>`<option value="${n}" ${state.level===String(n)?'selected':''}>L${n}</option>`).join('')}</select><select id="sq-industry-kind" aria-label="行业来源类别">${[['all','全部来源'],['official','官方分类 / 指数'],['etf','ETF 代理']].map(([v,t])=>`<option value="${v}" ${state.kind===v?'selected':''}>${t}</option>`).join('')}</select></div><div class="sq-industry-count">${items.length} 个匹配来源</div><div class="sq-industry-list">${shown.map(x => {
      const available = x.historyStatus === 'adapter_supported_requires_observations' && x.factorId;
      const proxy = x.sourceKind === 'etf_proxy', used = added.has(x.factorId), url = safeUrl(x.sourceUrl);
      const proxies = (x.proxyIds || []).map(id=>(catalog.proxies || []).find(p=>p.id===id)).filter(Boolean);
      const badge = proxy ? 'ETF 代理' : x.historyStatus === 'not_published_in_source' ? '官方分类 · 无已发布指数' : x.sourceKind === 'official_index' ? '官方指数' : '官方分类';
      return `<article class="sq-industry-card"><div><span class="sq-industry-path">${e(x.taxonomy || x.symbol || '')}${x.level ? ` · L${x.level}` : ''}</span><h3>${e(x.nameZh || x.name)}</h3>${x.nameZh ? `<span class="sq-subtle">${e(x.name)}</span>` : ''}<span class="sq-industry-path">${e((x.path || []).slice(0,-1).join(' › ') || x.symbol || x.contextCode || '')}</span><div class="sq-industry-badges"><span>${badge}</span>${providerLabel(x) ? `<span>${e(providerLabel(x))}</span>` : ''}${available ? '' : `<span>${pendingLabel(x)}</span>`}</div>${proxy ? '<span class="sq-industry-path">研究代理，不等同于官方行业指数</span>' : ''}${proxies.length?`<div class="sq-industry-proxies"><span>相关 ETF 代理</span>${proxies.map(p=>p.historyStatus==='adapter_supported_requires_observations'&&p.factorId?`<button class="btn ghost small" data-v2="add-factor" data-id="${e(p.factorId)}" ${added.has(p.factorId)?'disabled':''}>${e(p.symbol)}${added.has(p.factorId)?' · 已加入':' ＋'}</button>`:`<span>${e(p.symbol)} · ${pendingLabel(p)}</span>`).join('')}</div>`:''}</div><footer>${url ? `<a href="${e(url)}" target="_blank" rel="noopener noreferrer">官方来源 ↗</a>` : ''}${available ? `<button class="btn ghost small" data-v2="add-factor" data-id="${e(x.factorId)}" ${used?'disabled':''}>${used?'已加入':'＋ 加入因子'}</button>` : `<button class="btn ghost small" disabled>${pendingLabel(x)}</button>`}</footer></article>`;
    }).join('') || F.empty('没有匹配来源', '调整行业、市场或 ETF 关键词。')}</div><div class="sq-catalog-pagination"><span>${page} / ${pageCount}</span><div>${F.button('industry-page','上一页',{page:page-1,small:true,disabled:page<=1})}${F.button('industry-page','下一页',{page:page+1,small:true,disabled:page>=pageCount})}</div></div></section>`;
  }
  function handle(el) { if(el.dataset.sq!=='industry-page')return false;state.page=Number(el.dataset.page);render();return true; }
  let timer;
  function onInput(el) {
    if(el.id!=='sq-industry-search')return;
    state.query=el.value;state.page=1;clearTimeout(timer);
    timer=setTimeout(()=>{render();const next=document.getElementById('sq-industry-search');next?.focus();},150);
  }
  function onChange(el) {
    const key={'sq-industry-market':'market','sq-industry-level':'level','sq-industry-kind':'kind'}[el.id];
    if(!key)return false;state[key]=el.value;state.page=1;render();return true;
  }
  return {view,handle,onInput,onChange,state};
}
