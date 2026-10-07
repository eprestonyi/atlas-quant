import {validateUniverseSelection} from './universe.mjs';
export class ApiError extends Error{constructor(code,message,status=400){super(message);this.code=code;this.status=status;}}
export const FIELDS=new Set('open high low close raw_close vol amount adj_factor turnover_rate turnover_rate_f volume_ratio pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm total_share float_share free_share total_mv circ_mv'.split(' '));
const WINDOWS=new Set('lag returns delta ts_mean ts_std ts_min ts_max ts_sum ts_rank'.split(' '));
const UNARY=new Set('rank zscore log abs sqrt sign'.split(' '));
const MODELS=new Set(['factor_score','ridge','elastic_net','hist_gradient_boosting','bayesian_ridge','huber','random_forest','extra_trees']);
function fail(message){throw new ApiError('INVALID_INPUT',message);}
export function textField(value,label,max=200){if(typeof value!=='string'||!value.trim()||value.length>max)fail(`${label}需为 1–${max} 字符`);return value.trim();}
export function validateExpression(expression){
 const text=textField(expression,'因子表达式',500);let pos=0,nodes=0;const fields=new Set();
 const tokens=[];while(pos<text.length){if(/\s/.test(text[pos])){pos++;continue;}const m=/^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|^[a-zA-Z_][a-zA-Z_0-9]*|^[()+\-*/,]/.exec(text.slice(pos));if(!m)fail('表达式包含不支持的字符；仅支持因果数学运算');tokens.push(m[0]);pos+=m[0].length;}
 let i=0;const peek=()=>tokens[i];const take=()=>tokens[i++];const expect=t=>{if(take()!==t)fail('表达式括号或参数格式无效');};
 function expr(depth=0){let a=term(depth+1);while(['+','-'].includes(peek())){take();const b=term(depth+1);a={lookback:Math.max(a.lookback,b.lookback)};}return a;}
 function term(depth){let a=atom(depth+1);while(['*','/'].includes(peek())){take();const b=atom(depth+1);a={lookback:Math.max(a.lookback,b.lookback)};}return a;}
 function atom(depth){if(depth>48||++nodes>128)fail('表达式过于复杂');const t=take();if(!t)fail('表达式不完整');if(t==='+'||t==='-'){const x=atom(depth+1);return {...x,...(x.literal!==undefined?{literal:(t==='-'?-1:1)*x.literal}:{})};}if(t==='('){const x=expr(depth+1);expect(')');return x;}
 if(/^\d|^\./.test(t)){const v=Number(t);if(!Number.isFinite(v)||Math.abs(v)>1e6)fail('数字常量超出范围');return {lookback:0,literal:v};}
 if(peek()!=='('){if(!FIELDS.has(t)&&!/^(?:pcd|fd|ext|model)_[a-z0-9_]{1,60}$/.test(t))fail(`不支持的数据字段：${t}`);fields.add(t);return {lookback:0};}
 take();const args=[];if(peek()!==')'){do{args.push(expr(depth+1));if(peek()!==',')break;take();}while(true);}expect(')');
 if(WINDOWS.has(t)){if(args.length!==2||!Number.isInteger(args[1].literal)||args[1].literal<1||args[1].literal>252)fail(`${t} 的窗口必须是 1–252 的正整数`);return {lookback:args[0].lookback+args[1].literal-(WINDOWS.has(t)&&!(['lag','returns','delta'].includes(t))?1:0)};}
 if(UNARY.has(t)){if(args.length!==1)fail(`${t} 需要一个参数`);return {lookback:args[0].lookback};}
 if(t==='min'||t==='max'){if(args.length!==2)fail(`${t} 需要两个参数`);return {lookback:Math.max(...args.map(a=>a.lookback))};}
 if(t==='clip'){if(args.length!==3||args[1].literal===undefined||args[2].literal===undefined||args[1].literal>=args[2].literal)fail('clip 需要表达式与递增的数值上下限');return {lookback:args[0].lookback};}fail(`不支持的因子函数：${t}`);
 }
 const ast=expr();if(i!==tokens.length)fail('表达式包含多余内容');if(!fields.size)fail('因子必须引用至少一个数据字段');if(ast.lookback>504)fail('因子总回看窗口不可超过 504 个交易日');return {fields:[...fields].sort(),lookback:ast.lookback,validation:'syntax_validated',language:'atlas-factor-dsl/v1'};
}
function number(v,label,min,max,integer=false){if(typeof v!=='number'||!Number.isFinite(v)||v<min||v>max||(integer&&!Number.isInteger(v)))fail(`${label} 必须介于 ${min}–${max}`);return v;}
function date(v){if(typeof v!=='string'||!/^\d{8}$/.test(v))fail('日期应为 YYYYMMDD');const d=new Date(`${v.slice(0,4)}-${v.slice(4,6)}-${v.slice(6,8)}T00:00:00Z`);if(!Number.isFinite(+d)||d.toISOString().slice(0,10).replaceAll('-','')!==v)fail('日期无效');return v;}
export function validateStrategy(input){
 if(!input||typeof input!=='object'||Array.isArray(input))fail('策略配置无效');const s={...input};s.universe=input.universe?{...input.universe}:null;s.schemaVersion=1;s.name=textField(s.name,'策略名称',80);
 const statArb=input.research?.mode==='stat_arb';const u=s.universe;if(!u||!Array.isArray(u.symbols)||u.symbols.length<3||u.symbols.length>50)fail('横截面研究需要 3–50 个标的');u.symbols=[...new Set(u.symbols)];if(u.symbols.length<3||u.symbols.some(x=>typeof x!=='string'||!/^\d{6}\.(SH|SZ)$/.test(x)))fail('请输入至少三个有效的沪深 A 股代码，例如 600519.SH');u.start=date(u.start);u.end=date(u.end);if(u.start>=u.end)fail('结束日期必须晚于开始日期');if(+new Date(`${u.end.slice(0,4)}-${u.end.slice(4,6)}-${u.end.slice(6,8)}`)-+new Date(`${u.start.slice(0,4)}-${u.start.slice(4,6)}-${u.start.slice(6,8)}`)>366*8*86400000)fail('首版单次回测最多 8 年');
 if(!Array.isArray(s.factors)||s.factors.length<(statArb?0:1)||s.factors.length>32)fail('请选择 1–32 个因子');const seen=new Set();s.factors=s.factors.map(f=>{if(!f||typeof f!=='object'||Array.isArray(f))fail('因子项须为对象');const id=textField(f.id,'因子 ID',100);if(!/^[A-Za-z0-9_-]{1,100}$/.test(id))fail('因子 ID 仅允许字母、数字、下划线或连字符');if(seen.has(id))fail('同一个因子不能重复添加');seen.add(id);const expression=textField(f.expression,'因子表达式',500);validateExpression(expression);return {id,expression,direction:f.direction===-1?-1:1,...(f.version!==undefined?{version:number(f.version,'因子版本',1,1000000,true)}:{})};});
 s.preprocess={winsorize:s.preprocess?.winsorize!==false,standardize:s.preprocess?.standardize!==false,decorrelation:s.preprocess?.decorrelation||'none',correlationThreshold:number(s.preprocess?.correlationThreshold??.9,'相关阈值',.5,1)};if(!['none','drop_correlated'].includes(s.preprocess.decorrelation))fail('去相关方式无效');const r=input.research||{};if(!r||typeof r!=='object'||Array.isArray(r))fail('研究配置无效');s.research={mode:r.mode||'legacy_long_only',observationDays:number(r.observationDays??1,'观察采样间隔',1,60,true)};if(!['factor','legacy_long_only','stat_arb'].includes(s.research.mode))fail('研究模式无效');if(r.baseline){if(typeof r.baseline!=='object'||Array.isArray(r.baseline))fail('基准来源无效');s.research.baseline={id:textField(r.baseline.id,'基准ID',100),name:textField(r.baseline.name,'基准名称',100),version:number(r.baseline.version??1,'基准版本',1,1000000,true)};}
 const m=statArb?{mode:'manual',candidates:['factor_score'],horizon:5}:s.model||{};if(m.target!==undefined&&!['forward_return','forward_excess_return'].includes(m.target))fail('预测目标无效');if(!['auto','manual'].includes(m.mode))fail('模型模式应为 auto 或 manual');if(!Array.isArray(m.candidates)||!m.candidates.length||m.candidates.some(x=>!MODELS.has(x)))fail('模型候选无效');s.model={mode:m.mode,candidates:[...new Set(m.candidates)],horizon:number(m.horizon,'预测期限',1,20,true),metric:'rank_ic',target:['forward_return','forward_excess_return'].includes(m.target)?m.target:'forward_return'};if(m.mode==='manual'&&m.candidates.length!==1)fail('手动模式请选择一个模型');
 const p=s.portfolio||{};s.portfolio={topN:number(statArb?Math.min(3,u.symbols.length):p.topN,'持仓数量',1,u.symbols.length,true),maxWeight:number(statArb?1:p.maxWeight,'单只权重上限',.01,1),rebalanceDays:number(p.rebalanceDays??1,'调仓间隔',1,60,true),initialCapital:number(p.initialCapital??1000000,'初始资金',10000,1e9),rebalanceThresholdBps:number(p.rebalanceThresholdBps??0,'目标权重变化阈值',0,10000),rankBuffer:number(statArb?0:p.rankBuffer??0,'排名缓冲',0,u.symbols.length-(p.topN||Math.min(3,u.symbols.length)),true)};
 const c=s.costs||{};s.costs={commissionBps:number(c.commissionBps??(statArb?2.5:3),'单边佣金',0,100),slippageBps:number(c.slippageBps??(statArb?3:10),'单边滑点',0,200),sellTaxBps:number(c.sellTaxBps??5,'卖出税费',0,100),transferBps:number(c.transferBps??(statArb?.1:0),'双边过户费',0,100),minCommission:number(c.minCommission??(statArb?5:0),'最低佣金',0,1000)};if(statArb)s.statArb=validateStatArbConfig(input.statArb,u.symbols.length,s.factors.length);
 const graph=s.graph||{nodes:[],edges:[]};validateGraph(graph);s.graph={...(Array.isArray(graph.groups)?{groups:graph.groups.slice(0,12).map(g=>({id:textField(g.id,'分组ID',80),name:textField(g.name,'分组名称',80),factorIds:Array.isArray(g.factorIds)?g.factorIds.filter(id=>seen.has(id)).slice(0,32):[]}))}:{}),nodes:graph.nodes.map(n=>({id:n.id,type:n.type,...(n.x!==undefined?{x:n.x}:{}),...(n.y!==undefined?{y:n.y}:{})})),edges:graph.edges.map(e=>({source:e.source,target:e.target}))};s.dataBindings=validateBindings(input.dataBindings);for(const binding of Object.values(s.dataBindings.pcd||{}))if(binding.records.some(r=>!u.symbols.includes(r.ts_code)))fail('PCD映射包含股票池以外的标的');s.universe={symbols:u.symbols,start:u.start,end:u.end,...(typeof u.presetId==='string'?{presetId:textField(u.presetId,'票池ID',120)}:{}),...(typeof u.snapshotDate==='string'?{snapshotDate:date(u.snapshotDate)}:{}),...validateUniverseState(u)};return Object.fromEntries(['schemaVersion','name','universe','factors','preprocess','model','portfolio','costs','graph','dataBindings','research',...(statArb?['statArb']:[])].map(k=>[k,s[k]]));
}
export function validateFactor(f){const name=textField(f.name,'因子名称',80),expression=textField(f.expression,'因子表达式',500),metadata=validateExpression(expression);const license=['Apache-2.0','MIT','BSD-3-Clause','CC0-1.0'].includes(f.license)?f.license:null;if(!license)fail('请选择支持的开源许可证');let sourceUrl=f.sourceUrl||'';if(sourceUrl){try{const u=new URL(sourceUrl);if(u.protocol!=='https:')fail('来源链接需为 HTTPS');}catch{fail('来源链接无效');}if(sourceUrl.length>500)fail('来源链接过长');}
 return {name,description:textField(f.description,'说明',2000),expression,direction:f.direction===-1?-1:1,category:textField(f.category||'community','分类',40),author:textField(f.author||'Atlas contributor','作者',80),license,sourceUrl,forkOf:f.forkOf?textField(f.forkOf,'来源因子',80):null,metadata};
}
export function validateDataset(dataset,strategy){if(!dataset||!Array.isArray(dataset.rows)||dataset.rows.length<100||dataset.rows.length>110000)fail('数据集需要 100–110,000 行');const symbols=new Set(strategy.universe.symbols),keys=new Set();for(const r of dataset.rows){if(!symbols.has(r.ts_code))fail('数据包含股票池以外的标的');date(r.trade_date);const k=r.ts_code+':'+r.trade_date;if(keys.has(k))fail('数据集存在重复的标的日期');keys.add(k);for(const field of ['open','high','low','close'])number(r[field],field,1e-8,1e8);if(r.high<Math.max(r.open,r.close,r.low)||r.low>Math.min(r.open,r.close,r.high))fail('OHLC 高低价不一致');number(r.vol,'成交量',0,1e15);if(r.amount===undefined||r.amount===null){if(dataset.provenance?.amountDerivation!=='vol*close*100/1000')fail('缺少 amount（千元）；请提供数据或显式声明成交额近似方法');if(r.raw_close!==undefined&&r.raw_close!==r.close)fail('复权价格不可用于近似成交额');}else number(r.amount,'成交额',0,1e18);if(r.raw_close!==undefined)number(r.raw_close,'原始收盘价',1e-8,1e8);if(r.adj_factor!==undefined)number(r.adj_factor,'复权因子',1e-8,1e8);for(const field of new Set([...FIELDS,...Object.keys(r).filter(k=>/^(?:pcd|fd|ext|model)_[a-z0-9_]{1,60}$/.test(k)&&!k.endsWith('__available_date'))])){if(['open','high','low','close','raw_close','vol','amount','adj_factor'].includes(field))continue;if(r[field]!==undefined&&r[field]!==null)number(r[field],field,-1e20,1e20);}}if(new TextEncoder().encode(JSON.stringify(dataset)).length>24*1024*1024)fail('数据集超过 24 MiB');return dataset;}

export function validateGraph(graph){
 if(!graph||!Array.isArray(graph.nodes)||!Array.isArray(graph.edges)||JSON.stringify(graph).length>20000)fail('画布无效或过大');
 if(!graph.nodes.length&&!graph.edges.length)return;
 const stages=['universe','factors','preprocess','model','portfolio','backtest'];
 if(graph.nodes.length!==6||graph.edges.length!==5)fail('请连接完整的六个研究模块');
 if(stages.some(stage=>graph.nodes.filter(n=>n.id===stage&&n.type===stage).length!==1))fail('研究模块缺失或重复');
 const links=graph.edges.map(e=>e.source+'>'+e.target);if(stages.slice(1).some((stage,i)=>links.filter(link=>link===stages[i]+'>'+stage).length!==1))fail('模块连接顺序无效，请按研究流程连接');
 for(const n of graph.nodes)for(const key of ['x','y'])if(n[key]!==undefined&&(!Number.isFinite(n[key])||Math.abs(n[key])>100000))fail('画布坐标无效');
}

export function validateBindings(input){
 if(input===undefined)return {};if(!input||typeof input!=='object'||Array.isArray(input))fail('数据绑定无效');
 const pcd=input.pcd===undefined?{}:input.pcd;
 if(!pcd||typeof pcd!=='object'||Array.isArray(pcd)||Object.keys(pcd).length>32)fail('PCD最多绑定32个字段');
 const clean={};for(const [alias,b] of Object.entries(pcd)){
  if(!/^pcd_[a-z0-9_]{1,60}$/.test(alias)||!b||typeof b!=='object'||Array.isArray(b))fail('PCD字段别名无效');
  if(!Array.isArray(b.records)||!b.records.length||b.records.length>50)fail('每个PCD字段需绑定1–50条精确记录');
  const seenRecords=new Set();
  clean[alias]={fieldId:textField(b.fieldId,'PCD字段ID',220),unitCode:textField(b.unitCode,'单位代码',80),records:b.records.map(r=>{
   if(!r||typeof r!=='object'||Array.isArray(r)||!/^\d{6}\.(SH|SZ)$/.test(r.ts_code))fail('PCD标的映射代码无效');
   const entityId=textField(r.entityId,'实体ID',200),recordId=textField(r.recordId,'记录ID',200),recordKey=entityId+':'+recordId;
   if(seenRecords.has(recordId))fail('PCD映射不可重复精确记录');seenRecords.add(recordId);
   return {ts_code:r.ts_code,entityId,recordId};
  })};
 }return {pcd:clean};
}

export function validateStatArbConfig(input,n,factors){
 const defaults={method:'pca_residual',formationDays:126,residualWindow:60,components:Math.min(2,n-2),refitDays:20,entryZ:2,exitZ:.5,stopZ:4,maxHoldingDays:20,grossExposure:1,shorting:'theoretical',borrowAnnualBps:300,maxHalfLife:60};
 if(input!==undefined&&(!input||typeof input!=='object'||Array.isArray(input)))fail('统计套利配置无效');const a={...defaults,...input};
 if(!['market_residual','pca_residual','factor_residual'].includes(a.method))fail('共同成分方法无效');if(a.shorting!=='theoretical')fail('仅支持明确标注的理论借券研究');
 for(const [key,lo,hi,int] of [['formationDays',60,504,true],['residualWindow',20,252,true],['components',1,10,true],['refitDays',1,126,true],['entryZ',.25,6,false],['exitZ',0,5.9,false],['stopZ',.3,10,false],['maxHoldingDays',1,252,true],['grossExposure',.1,2,false],['borrowAnnualBps',0,10000,false],['maxHalfLife',1,252,false]])a[key]=number(a[key],key,lo,hi,int);
 if(!(a.exitZ<a.entryZ&&a.entryZ<a.stopZ))fail('须满足退出阈值 < 入场阈值 < 止损阈值');if(a.residualWindow>a.formationDays)fail('残差窗口不可超过形成窗口');if(a.method==='pca_residual'&&a.components>n-2)fail('PCA成分数最多为股票数减2');if(a.method==='factor_residual'&&!factors)fail('显式因子残差至少需要一个因子');return Object.fromEntries(Object.keys(defaults).map(k=>[k,a[k]]));
}
function validateUniverseState(u){
 if(u.selection===undefined)return {};let selection;try{selection=validateUniverseSelection(u.selection);}catch(e){fail(e.message);}
 const hash=(v,label)=>{if(typeof v!=='string'||!/^[a-f0-9]{64}$/.test(v))fail(label+'无效，请重新解析股票池');return v;};
 if(!['all','explicit'].includes(u.subsetPolicy))fail('请明确使用完整筛选结果或自行选择研究子集');
 return {selection,resolutionHash:hash(u.resolutionHash,'结果版本'),snapshotHash:hash(u.snapshotHash,'目录版本'),subsetPolicy:u.subsetPolicy,...(u.catalogSnapshot&&typeof u.catalogSnapshot==='object'&&!Array.isArray(u.catalogSnapshot)?{catalogSnapshot:{hash:hash(u.catalogSnapshot.hash,'快照版本'),asOf:typeof u.catalogSnapshot.asOf==='string'?u.catalogSnapshot.asOf.slice(0,80):null,historicalMembershipVerified:false}}:{})};
}
