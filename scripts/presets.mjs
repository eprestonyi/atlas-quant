export function researchPresets(catalog,template){
 const find=(family,window)=>catalog.factors.find(f=>f.id===family)||catalog.factors.find(f=>f.family===family&&(window===undefined||f.window===window));
 const definitions=[
 ['trend','趋势延续','寻找持续走强、走势更一致的公司',[['momentum',20],['momentum',60],['trend_efficiency',20]],'趋势'],
 ['defensive','低波动防守','比较波动较低、回撤较小的公司',[['volatility',20],['downside_risk',60],['drawdown',60]],'防守'],
 ['rebound','短期修复','研究短期回撤后的价格修复',[['reversal',5],['rebound',20],['volume_surge',20]],'反转'],
 ['value','估值比较','按盈利、账面价值与销售额研究估值差异',[['earnings_yield'],['book_yield'],['sales_yield']],'价值'],
 ['income','股息与稳定','研究股息率和历史稳定性',[['dividend_yield'],['volatility',60],['historical_sharpe',60]],'股息'],
 ['liquidity','交易活跃度','研究成交量、换手与资金活跃程度',[['liquidity',20],['volume_ratio'],['turnover']],'流动性'],
 ['volume','量价共振','同时观察价格变化与成交量变化',[['volume_weighted_return',20],['money_flow',20],['volume_surge',20]],'量价'],
 ['breakout','区间突破','研究价格相对近期区间的位置与突破',[['breakout',60],['range_position',20],['trend_persistence',20]],'趋势'],
 ['small','规模比较','研究市值规模、流通比例与估值',[['small_size'],['small_float_size'],['book_yield']],'规模'],
 ['qualitytrend','风险调整趋势','在趋势研究中加入波动与路径效率',[['historical_sharpe',60],['trend_efficiency',60],['momentum',120]],'综合'],
 ['intraday','日内与隔夜','拆分日内价格运动和隔夜变化',[['intraday_momentum',20],['overnight_momentum',20],['gap_reversal']],'行为'],
 ['financial_quality','财务质量','把盈利能力、资本效率与负债放在公告时间之后比较',[['fd_roe'],['fd_roic'],['fd_debt_to_assets']],'财务'],
 ['financial_growth','增长与改善','研究已披露的收入、利润增长及盈利能力变化',[['fd_or_yoy'],['fd_netprofit_yoy'],['fd_roe_change_60']],'财务'],
 ['cash_quality','现金流与偿债','结合经营现金流、现金流估值与短期偿债能力',[['fd_ocf_to_or'],['fd_ocfps_price_yield'],['fd_current_ratio']],'财务'],
 ['balanced','均衡多因子','把趋势、价值与风险放在同一个研究中',[['momentum',60],['book_yield'],['volatility',20],['liquidity',20]],'综合']
 ];
 const packs=definitions.map(([id,name,description,recipes,category])=>({id,name,description,category,factors:recipes.map(([f,w])=>find(f,w)||find(f)).filter(Boolean).map(f=>({id:f.id,name:f.name,expression:f.expression,direction:f.direction,version:1})),availability:{status:'ready',reason:'需要实际标的和所需数据字段覆盖；精选表示研究组织方式，不表示已验证的超额收益。'}})).filter(p=>p.factors.length>=2);
 const goals=[{id:'discover',name:'发现值得继续研究的公司',description:'比较股票池里的信号，得到可核查的排序。',target:'forward_return',horizon:5},{id:'outperform',name:'研究相对表现',description:'预测相对所选股票池的未来表现差异。',target:'forward_excess_return',horizon:10},{id:'defensive',name:'关注稳健与回撤',description:'使用防守型信号，检查回撤和交易成本。',target:'forward_return',horizon:10},{id:'forecast',name:'预测未来价格表现',description:'比较可输出收益估计的模型，并显示误差。',target:'forward_return',horizon:5}];
 const modelPresets=[{id:'balanced',name:'均衡比较',description:'比较简单基线、线性模型与非线性模型。',candidates:['factor_score','ridge','elastic_net','hist_gradient_boosting']},{id:'interpretable',name:'可解释优先',description:'以基线和正则化线性模型为核心。',candidates:['factor_score','ridge','elastic_net','bayesian_ridge']},{id:'robust',name:'异常值稳健研究',description:'加入稳健回归，观察极端样本的影响。',candidates:['factor_score','huber','ridge']},{id:'ensembles',name:'非线性集成',description:'比较梯度提升、随机森林与极随机树。',candidates:['factor_score','hist_gradient_boosting','random_forest','extra_trees']},{id:'comprehensive',name:'完整候选比较',description:'运行全部八类候选，计算时间更长。',candidates:catalog.models.map(m=>m.id)}];
 const strategies=packs.map(p=>({id:p.id,name:p.name,description:p.description,strategy:{...structuredClone(template),name:p.name,factors:p.factors,model:{...template.model,target:'forward_return'}}}));return {goals,packs,modelPresets,strategies};
}
