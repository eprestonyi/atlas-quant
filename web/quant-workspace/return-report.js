import { RETURN_MODES, returnUnit } from './return-study.js';
import { renderSavedModel } from './report-model.js';
import { renderFactorTransformAudit } from './factor-transform-audit.js';
import { returnResponseCharts } from './report-charts.js';

export const isReturnReport = r => r?.forecasts?.studyProtocol === 'asset-return-study/1' || r?.forecasts?.sourceStrategy?.research?.returnStudy?.schema === 'asset-return-study/1';
// The chosen security scopes every evidence page. No pool-level statistic is
// substituted when a security's evidence is absent.
export function createReturnReport(C,F,{remote,remoteState,table,modelCandidates,functionEditor,factorDiagnostics,provenance}) {
  const {esc:e,fmt,render,openModal}=C;
  const state={identity:'',targetId:'',tab:'models',report:null,page:1,fit:null,fitId:'',request:0,loading:false,error:'',tree:0};
  const raw=x=>`<pre class="sq-report-code">${e(JSON.stringify(x,null,2))}</pre>`;
  const defs=r=>r.forecasts.diagnostics?.assetModels || [];
  const active=()=>defs(state.report).find(x=>x.targetId===state.targetId);
  const strategy=r=>r.forecasts.sourceStrategy || r.strategy;
  function select(targetId) {
    if(!defs(state.report).some(x=>x.targetId===targetId))return;
    Object.assign(state,{targetId,page:1,fit:null,fitId:'',error:'',loading:false,tree:0});state.request++;modelCandidates.reset();render();
  }
  function loadFit(id) {
    const request=++state.request;Object.assign(state,{fitId:id,fit:null,error:'',loading:true});
    remote.detail('modelFits',id).then(response=>{
      if(request!==state.request||!response)return;
      if(response.item?.id!==id||response.item.targetId!==state.targetId)throw Error('函数与所选资产不一致。');
      state.fit=response.item;
    }).catch(error=>{if(request===state.request)state.error=error.message;}).finally(()=>{if(request===state.request){state.loading=false;render();}});
  }
  function fits(r,audit=false) {
    const asset=active(),page=remote.enabled()?remote.page('modelFits',{targetId:state.targetId}):null;
    const items=page?page.items:(r.forecasts.modelFits||[]).filter(x=>x.targetId===state.targetId);
    const id=state.fitId||asset.modelFitId||items.find(x=>x.functionArtifact)?.id;
    if(remote.enabled()&&id&&!state.loading&&state.fit?.id!==id&&!state.error)loadFit(id);
    const fit=remote.enabled()?state.fit:items.find(x=>x.id===id);
    const opts=items.some(x=>x.id===id)?items:id?[{id,...fit},...items]:items;
    const picker=`<label class="sq-field"><span>滚动拟合版本</span><select id="sq-return-fit">${opts.map(x=>`<option value="${e(x.id)}" ${x.id===id?'selected':''}>${e(x.fitDate||'')} · ${e(x.estimator||'')} · ${e(x.id)}</option>`).join('')}</select></label>`;
    const body=state.error?F.note(state.error,'error')+F.button('return-fit-retry','重试',{small:true}):fit?(audit?renderFactorTransformAudit(C,F,fit):renderSavedModel(C,F,fit,{tree:state.tree,controlPrefix:'sq-return-model'})):'<div class="sq-loading">正在读取此资产的 F…</div>';
    const actions=fit?.functionArtifact&&!audit?`<div class="sq-actions">${F.button('return-fit-edit','修改与试算这个 F',{primary:true})}${fit.functionArtifact.scope.studyMode==='association'?F.button('return-fit-scenario','未来因子情景',{small:true}):''}</div>`:'';
    return F.panel(audit?'因子处理':'已采用的滚动模型',picker+body+actions+(page?remoteState(page,''):''));
  }
  function collection(r,name,local) {
    const page=remote.enabled()?remote.page(name,{targetId:state.targetId}):null;
    return {page,rows:page?page.items:local.filter(x=>x.targetId===state.targetId)};
  }
  function rowView(r,panel=false) {
    const source=panel?r.forecasts.factorResearch?.panel?.rows||[]:r.forecasts.rows||[];
    const {page,rows:all}=collection(r,panel?'researchPanel':'forecasts',source);
    const rows=page?all:all.slice((state.page-1)*25,state.page*25),s=strategy(r),association=s.research.returnStudy.mode==='association';
    const columns=panel?['因子日期','响应区间','因子 R','观察收益','响应尺度','状态']:['因子日期','响应区间','模型响应','实际响应','残差','收益率','状态'];
    const body=table(columns,rows.map(x=>`<tr><td>${e(x.featureDate||x.date)}<small>已知截止 ${e(x.informationCutoff||'—')}</small></td><td>${e(x.responseStartDate||'—')} → ${e(x.responseEndDate||'—')}</td>${panel?`<td>${F.advanced('因子值',raw(x.features))}</td><td class="numeric">${fmt(x.observedReturn,6)}</td><td class="numeric">${fmt(x.responseScale,6)}</td>`:`<td class="numeric">${fmt(x.predictedResponse,6)}</td><td class="numeric">${fmt(x.observedResponse,6)}</td><td class="numeric">${fmt(x.responseResidual,6)}</td><td class="numeric">${fmt(x.predictedReturn,6)}<small>实际 ${fmt(x.observedReturn,6)}</small></td>`}<td>${e(x.invalidReason||x.status||(x.inputValid?'有效':'输入缺失'))}</td></tr>`));
    const pages=!page&&all.length>25?`<div class="sq-catalog-pagination"><span>${all.length} 行 · ${state.page} / ${Math.ceil(all.length/25)}</span>${F.button('return-page','上一页',{page:state.page-1,disabled:state.page===1,small:true})}${F.button('return-page','下一页',{page:state.page+1,disabled:state.page*25>=all.length,small:true})}</div>`:'';
    return (!panel?returnResponseCharts(rows,{esc:e,association,normalized:s.target.normalization.kind!=='none',scope:'当前资产 · 当前页'}):'')+F.panel(panel?'完整资产面板':'模型与观测响应',page?remoteState(page,body):body+pages)+ (panel?F.note('日期 × 资产面板保留缺失与暖机行。当前仅显示所选资产的一页；完整面板可从私有产物下载。'):'');
  }
  function validation(r) {
    const {page,rows}=collection(r,'perTarget',r.forecasts.diagnostics?.perTarget||[]);
    const body=rows.map(x=>F.panel('此资产的检验',table(['指标','数值'],Object.entries(x.metrics||{}).filter(([,v])=>typeof v==='number'||v===null).map(([k,v])=>`<tr><th>${e(k)}</th><td class="numeric">${fmt(v,6)}</td></tr>`))+F.advanced('选定模型与样本口径',raw(x)))).join('');
    const folds=remote.enabled()?remote.page('outerFolds',{targetId:state.targetId}):null;
    return (page?remoteState(page,body):body)+(folds?F.panel('外层时间检验',remoteState(folds,folds.items.map(x=>F.advanced(x.id||x.testStart||'时间折',raw(x))).join(''))):'');
  }
  function diagnostics(r,view,mode) {
    const page=remote.enabled()?remote.page('perTarget',{targetId:state.targetId}):null;
    if(page && (page.loading || page.error || !page.loaded && !page.unavailable)) return remoteState(page,'');
    const record=(page?page.items:r.forecasts.diagnostics?.perTarget||[]).find(x=>x.targetId===state.targetId);
    const stored=record?.factorDiagnostics;
    const frozen=stored?{...r,forecasts:{...r.forecasts,factorResearch:{...r.forecasts.factorResearch,diagnostics:{...r.forecasts.factorResearch?.diagnostics,perTarget:[{...stored,targetId:state.targetId}],features:r.forecasts.factorResearch?.diagnostics?.features||stored.features,dependence:{jointDistributions:r.forecasts.factorResearch?.diagnostics?.dependence?.jointDistributions||stored.jointDistributions||stored.dependence?.jointDistributions}}}}}:r;
    return factorDiagnostics.render(frozen,view,{targetId:state.targetId,mode});
  }
  function view(r,downloads='') {
    const identity=`${C.state.runId||''}/${r.forecasts.artifactId}/${remote.transport?.bundleId||''}`;
    if(state.identity!==identity){Object.assign(state,{identity,targetId:'',tab:'models',fit:null,fitId:'',page:1,error:'',loading:false});state.request++;}
    state.report=r;const assets=defs(r),s=strategy(r),mode=s.research.returnStudy.mode;
    if(!assets.length)return F.note('此产物未提供逐资产模型索引，不能把合并结果替代为单资产报告。','error');
    if(!assets.some(x=>x.targetId===state.targetId))state.targetId=assets[0].targetId;
    const asset=active();
    const tabs={models:'F 模型与参数',processing:'因子处理',observations:'拟合数据',panel:'完整面板',statistics:'因子统计',correlations:'相关矩阵',joints:'联合分布',validation:'检验',provenance:'来源'};
    const candidate=r.forecasts.diagnostics?.modelSearch;
    const audit=fit=>renderFactorTransformAudit(C,F,fit);
    const views={models:()=>candidate?modelCandidates.view(r,{targetId:state.targetId,asset})+F.advanced('已采用的滚动模型',fits(r)):fits(r),processing:()=>candidate?modelCandidates.view(r,{targetId:state.targetId,asset,audit}):fits(r,true),observations:()=>rowView(r),panel:()=>rowView(r,true),statistics:()=>diagnostics(r,'features',mode),correlations:()=>diagnostics(r,'correlations',mode),joints:()=>diagnostics(r,'joints',mode),validation:()=>validation(r),provenance:()=>provenance(r)};
    return `<section class="sq-return-report" data-return-target="${e(state.targetId)}"><div class="sq-report-heading"><h2>逐资产 F 模型与报告</h2><span class="sq-status">${e(RETURN_MODES[mode])}</span>${r.provenance?.synthetic||r.provenance?.dataSource==='demo'?'<span class="sq-status warning">合成数据</span>':''}${F.advanced('完整产物下载',downloads)}</div><div class="sq-asset-panel"><label class="sq-field"><span>研究资产 · ${assets.length} 只</span><select id="sq-return-asset">${assets.map(x=>`<option value="${e(x.targetId)}" ${x.targetId===state.targetId?'selected':''}>${e(x.targetSymbol)}</option>`).join('')}</select></label><div><strong>${e(asset.targetSymbol)} · ${e(returnUnit(s))}</strong><span>共用因子集合 · 当前资产独立参数</span><span>${mode==='association'?'同期已观测因子 → 同期收益；情景推演在 F 试算中单独选择':'起点已知因子 → 后续 '+s.target.horizonSessions+' 交易日收益'}</span></div></div><div class="sq-report-tabs" role="group" aria-label="单资产报告章节">${Object.entries(tabs).map(([id,label])=>F.button('return-tab',label,{id,small:true,pressed:id===state.tab,primary:id===state.tab})).join('')}</div>${(views[state.tab]||views.models)()}</section>`;
  }
  async function handle(el) {
    const action=el.dataset.sq;if(!action?.startsWith('return-'))return false;
    if(action==='return-tab'){state.tab=el.dataset.id;state.page=1;render();}
    if(action==='return-page'){state.page=Math.max(1,Number(el.dataset.page)||1);render();}
    if(action==='return-fit-retry'){loadFit(state.fitId);render();}
    if(['return-fit-edit','return-fit-scenario'].includes(action)){
      const fit=remote.enabled()?state.fit:state.report.forecasts.modelFits.find(x=>x.id===(state.fitId||active().modelFitId));
      if(fit?.targetId===state.targetId&&fit.functionArtifact)openModal('单资产 F 试算',functionEditor.render(fit,{runId:C.state.runId,bundleId:remote.transport?.bundleId||null,modelFitId:fit.id},action==='return-fit-scenario'?{mode:'future_scenario'}:{}),true);
    }
    return true;
  }
  function onChange(el){if(el.id==='sq-return-asset'){select(el.value);return true;}if(el.id==='sq-return-fit'){state.fitId=el.value;state.fit=null;state.error='';render();return true;}if(el.id==='sq-return-model-tree-index'){state.tree=Number(el.value);render();return true;}return false;}
  return {view,handle,onChange,state};
}
