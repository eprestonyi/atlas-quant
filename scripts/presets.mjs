export function researchPresets(catalog,template){
 const find=(family,window)=>catalog.factors.find(f=>f.id===family)||catalog.factors.find(f=>f.family===family&&(window===undefined||f.window===window));
 const definitions=[
 ['trend','趋势与路径效率','20 / 60 日收益变化与 20 日路径效率，刻画历史价格趋势暴露。',[['momentum',20],['momentum',60],['trend_efficiency',20]],'趋势'],
 ['defensive','波动与回撤暴露','20 日收益波动、60 日下行波动与回撤，刻画不同维度的历史风险暴露。',[['volatility',20],['downside_risk',60],['drawdown',60]],'风险'],
 ['rebound','短期价格偏离','5 日收益变化、20 日距区间低点与相对成交量，测量短期价格路径和交易量特征。',[['reversal',5],['rebound',20],['volume_surge',20]],'反转'],
 ['value','估值比率暴露','盈利、账面价值、销售额与价格的比率，作为横截面估值暴露。',[['earnings_yield'],['book_yield'],['sales_yield']],'价值'],
 ['income','股息与收益波动','股息率、60 日收益波动与历史 Sharpe，分别测量分红和已发生的收益风险特征。',[['dividend_yield'],['volatility',60],['historical_sharpe',60]],'股息'],
 ['liquidity','换手与成交活跃','20 日对数平均成交额、量比与换手率，刻画可观测的交易活跃暴露。',[['liquidity',20],['volume_ratio'],['turnover']],'流动性'],
 ['volume','收益与成交量关系','成交量加权收益、收盘位置加权量能与相对成交量，测量价格和交易量的统计关系。',[['volume_weighted_return',20],['money_flow',20],['volume_surge',20]],'量价'],
 ['breakout','价格区间与路径','价格相对历史区间的位置、突破统计与趋势持续性，刻画历史价格路径。',[['breakout',60],['range_position',20],['trend_persistence',20]],'趋势'],
 ['small','市值与流通规模','总市值、流通市值及账面价值比率，刻画规模与估值的横截面暴露。',[['small_size'],['small_float_size'],['book_yield']],'规模'],
 ['qualitytrend','收益风险与路径','60 日历史 Sharpe、路径效率与 120 日价格变化，描述已发生的收益风险结构。',[['historical_sharpe',60],['trend_efficiency',60],['momentum',120]],'综合'],
 ['intraday','日内与隔夜收益','开盘到收盘、隔夜变化与缺口统计，分解不同时间段的价格暴露。',[['intraday_momentum',20],['overnight_momentum',20],['gap_reversal']],'行为'],
 ['financial_quality','盈利、资本与负债','已公告的 ROE、ROIC 与资产负债率；仅在披露可用日期之后参与暴露估计。',[['fd_roe'],['fd_roic'],['fd_debt_to_assets']],'财务'],
 ['financial_growth','财务增长与变动','已披露的收入和利润同比变化、ROE 变动；按公告可用时间对齐。',[['fd_or_yoy'],['fd_netprofit_yoy'],['fd_roe_change_60']],'财务'],
 ['cash_quality','现金流与流动比率','经营现金流收入比、每股现金流价格比与流动比率，测量已披露的现金流和负债结构。',[['fd_ocf_to_or'],['fd_ocfps_price_yield'],['fd_current_ratio']],'财务'],
 ['balanced','多类暴露组合','同时加入历史收益变化、账面价值比率、波动与成交活跃暴露，检验对残差篮子的增量影响。',[['momentum',60],['book_yield'],['volatility',20],['liquidity',20]],'综合']
 ];
 const packs=definitions.map(([id,name,description,recipes,category])=>({id,name,description,category,factors:recipes.map(([f,w])=>find(f,w)||find(f)).filter(Boolean).map(f=>({id:f.id,name:f.name,expression:f.expression,direction:f.direction,version:1})),availability:{status:'ready',reason:'因子包只组织可测量的暴露定义；实际数据覆盖、对冲增量与残差表现须在本次研究中检验。'}})).filter(p=>p.factors.length>=2);
 const goals=[{id:'discover',name:'发现值得继续研究的公司',description:'比较股票池里的信号，得到可核查的排序。',target:'forward_return',horizon:5},{id:'outperform',name:'研究相对表现',description:'预测相对所选股票池的未来表现差异。',target:'forward_excess_return',horizon:10},{id:'defensive',name:'关注稳健与回撤',description:'使用防守型信号，检查回撤和交易成本。',target:'forward_return',horizon:10},{id:'forecast',name:'预测未来价格表现',description:'比较可输出收益估计的模型，并显示误差。',target:'forward_return',horizon:5}];
 const modelPresets=[{id:'balanced',name:'均衡比较',description:'比较简单基线、线性模型与非线性模型。',candidates:['factor_score','ridge','elastic_net','hist_gradient_boosting']},{id:'interpretable',name:'可解释优先',description:'以基线和正则化线性模型为核心。',candidates:['factor_score','ridge','elastic_net','bayesian_ridge']},{id:'robust',name:'异常值稳健研究',description:'加入稳健回归，观察极端样本的影响。',candidates:['factor_score','huber','ridge']},{id:'ensembles',name:'非线性集成',description:'比较梯度提升、随机森林与极随机树。',candidates:['factor_score','hist_gradient_boosting','random_forest','extra_trees']},{id:'comprehensive',name:'完整候选比较',description:'运行全部八类候选，计算时间更长。',candidates:catalog.models.map(m=>m.id)}];
 const strategies=packs.map(p=>({id:p.id,name:p.name,description:p.description,strategy:{...structuredClone(template),name:p.name,factors:p.factors,model:{...template.model,target:'forward_return'}}}));const residualDefaults={formationDays:126,residualWindow:60,components:2,refitDays:20,entryZ:2,exitZ:.5,stopZ:4,maxHoldingDays:20,grossExposure:1,shorting:'theoretical',borrowAnnualBps:300,maxHalfLife:60};
 const residualTemplates=[
 ['basket_mean_residual','篮子均值残差','剥离等权截面共同成分，研究相对偏离与收敛；净资金中性不等于市场beta中性。','market_residual',[]],
 ['pca_residual','PCA 共同成分残差','用过去收益估计共同成分，再交易剥离这些成分后的残差篮子。','pca_residual',[]],
 ['pca_style_residual','PCA + 风格因子残差','在PCA对冲中加入规模与估值暴露，并与未加入因子的同窗基准比较。','pca_residual',['small_size','book_yield']]
 ].map(([id,name,description,method,ids])=>({id,name,description,kind:'stat_arb_baseline',version:1,evidence:{status:'research_baseline_not_validated_alpha',outOfSampleVerified:false,performancePromise:false},rules:{signal:'残差累计水平的滚动z分数',entry:'绝对z达到入场阈值且半衰诊断有效',exit:'收敛、止损、期限或诊断失效',hedge:'共同暴露投影矩阵',shorting:'理论借券，未核实券源'},strategy:{...structuredClone(template),name,research:{mode:'stat_arb',observationDays:1,baseline:{id,name,version:1}},statArb:{...residualDefaults,method},factors:ids.map(id=>find(id)).filter(Boolean).map(f=>({id:f.id,expression:f.expression,direction:f.direction,version:1})),preprocess:{winsorize:true,standardize:true,decorrelation:'drop_correlated',correlationThreshold:.9},model:{mode:'manual',candidates:['factor_score'],horizon:5,metric:'rank_ic',target:'forward_excess_return'},portfolio:{topN:3,maxWeight:1,rebalanceDays:1,rebalanceThresholdBps:25,initialCapital:1000000},costs:{commissionBps:2.5,slippageBps:3,sellTaxBps:5,transferBps:.1,minCommission:5}}}));
 return {goals:[],packs,modelPresets,strategies:residualTemplates,legacyStrategies:strategies.map(x=>({...x,kind:'legacy_long_only',strategy:{...x.strategy,research:{mode:'legacy_long_only',observationDays:1}}})),researchScope:'basket_residual_statistical_arbitrage',defaultStrategy:'pca_residual'};
}
