# Atlas Quant v0.2 研究方法

本版运行 `atlas_quant.engine.run_research(strategy, data, provenance)`。它是自有日频研究引擎，使用 pandas/NumPy 和 scikit-learn；没有 LEAN、Backtrader 或券商撮合集成。

## 样本和时间

行情按完整交易日历与股票代码构造 panel，键为 `(trade_date, ts_code)`。缺失交易日保留缺失；不能用下一条可见行情冒充紧邻的交易日。因子按股票计算滚动/滞后，按当日股票池计算横截面排名。

T 日收盘后可获知的因子用于预测：

```text
label(T) = adjusted_open(T + h + 1) / adjusted_open(T + 1) - 1
```

`h` 为 1–20 个交易日。开始和结束价格缺失时，该标签无效。实际研究成交最早为 T+1 开盘，不能使用 T 收盘产生信号再按同一个收盘价买入。

`model.target` 默认为 `forward_return`，也支持 `forward_excess_return`：从上述收益减去同日所选股票中可观测收益的平均。后者不是官方指数超额收益；未来标签缺失会改变用于定义标签的股票池均值。两者均为收益比例，例如 `0.01` 表示 1%，不是价格或上涨概率。

`daily_basic` 需要在盘后发布后才可用于下一交易日；当前适配器按交易日期对齐，尚未验证所有字段的历史修订版本和当时可获知时间。数据的抓取时间不是历史 PIT 证据。

## 因子 DSL

每个因子含 `id`、`expression`、`direction`；方向只能为 +1/-1，在模型处理前应用。内置定义在 [catalog.json](../engine/atlas_quant/catalog.json)。

目录含 397 个配方、50 个家族：253 个量价配方、78 个日度估值/规模/流动性配方、66 个公告时点财务配方。不同窗口和相反方向是不同研究假设，不代表独立发现了数百个有效 alpha。当前最多同时选择 32 个因子；目录规模不等于本次输入的可用覆盖率。生成定义与元信息见 [research_registry.py](../engine/atlas_quant/research_registry.py)。

| 函数组 | 定义 |
|---|---|
| `lag(x,n)` | n 个日历交易日前的值 |
| `returns(x,n)` | `x / lag(x,n) - 1` |
| `delta(x,n)` | `x - lag(x,n)` |
| `ts_mean/std/min/max/sum(x,n)` | 包含当日的 n 日滚动统计；需要窗口内完整观测；std 使用 ddof=0 |
| `ts_rank(x,n)` | 当前值在包含当日的 n 日窗口中的百分位排名 |
| `rank(x)`、`zscore(x)` | 当日截面百分位排名、当日截面总体标准差标准化 |
| `log/abs/sqrt/sign(x)` | 自然对数、绝对值、平方根、符号 |
| `min(x,y)`、`max(x,y)` | 对应位置逐元素最小/最大值，保留缺失 |
| `clip(x,lo,hi)` | 截断到固定数字上下界 |

允许 `+ - * /` 和一元正负号。窗口须为 1–252 的正整数，表达式不超过 500 字符，组合回看最多 504 日。非有限结果、零分母、无效 log/sqrt 域转为缺失；分母绝对值不大于 `1e-12` 按无效处理。

字段白名单：`open high low close raw_close vol amount adj_factor turnover_rate turnover_rate_f volume_ratio pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm total_share float_share free_share total_mv circ_mv`。使用未提供字段会报错，不会凭空填入数据。`raw_close` 为原始价格；其公司行动跳变由因子作者自行考虑。

外部字段仅允许 `pcd_` / `fd_` / `ext_` / `model_` 加 1–60 个小写字母、数字或下划线。字段名合法不足以运行：输入必须包含真实数值列、`<alias>__available_date`，以及 `provenance.externalFields[alias]` 的来源、路径、数值类型和 `point_in_time_asof` 映射。每个非空观测的有效可知日期须不晚于信号日；文字、布尔值、非有限数及未来可知日期会被拒绝。可知日期纳入数据指纹。

Tushare 财务适配器按公告后的首个交易日对齐已披露指标，保留报告期与原始单位；不会根据报告期回填到公告前，也不会自动将季度/累计数值当作 TTM。检查可知日期无法独立证明来源时间戳真实性，当前历史版本修订覆盖仍有限。PCD 的字段目录是可发现的结构，不是已填充的历史面板；文本、列表和嵌套记录不能直接当作数值因子。 `ext_` 与 `model_` 支持自有时点数据导入，不表示相应历史数据库已自动接通。MODEL 输入保留 `independentTrainingHistoryVerified:false`：声明过去可用日并不能证明原模型当时只使用了过去数据，原模型训练历史需独立核验。

不允许字符串、比较条件、幂运算、属性、下标、导入、关键字参数、任意代码或负滞后。表达式只产生数值因子。

## 划分与模型选择

1. 根据完整交易日历、声明的最大因子回看期和预测期确定研究日期；边界不由未来标签是否可观测决定。
2. 保留最后约 20% 研究日期为终端 holdout，其后尚未形成完整标签的最近日期可以生成预测/模拟成交，但不进入 RankIC。
3. 开发区间使用 3 个扩展窗口 outer folds。每个 outer 训练段内部使用 2 个时间顺序 folds 选择模型/参数，再评价紧随其后的 outer 窗口。
4. 每次训练都剔除标签结束日期达到或超过验证起始日期的样本。所有同日股票属于同一个时间窗口；不随机拆分 panel 行。
5. 最后在开发区间的 3 个时间 folds 上重新比较候选，冻结赢家，使用已清除跨 holdout 标签的开发样本拟合一次，然后评价 holdout。

outer 指标评价这一滚动选择流程，不是把未来测试结果反向用于当时模型。最终赢家来自开发期验证，holdout 不参与候选排序。整个配置与日期写入报告，`holdoutUsedForSelection=false`。

## 候选与预处理

| 候选 | 有限配置 |
|---|---|
| `factor_score` | 方向调整后的各因子当日百分位排名取均值；无监督训练 |
| `ridge` | alpha = 1 或 10 |
| `elastic_net` | (alpha=0.0001,l1_ratio=0.2) 或 (0.001,0.5)；max_iter=3000，tol=1e-5，cyclic |
| `hist_gradient_boosting` | (max_leaf_nodes=7,l2=1) 或 (15,5)；80 轮，learning_rate=0.06，min_samples_leaf=20，max_bins=64 |
| `bayesian_ridge` | 训练段估计权重/噪声精度；max_iter=500，tol=1e-5 |
| `huber` | (epsilon=1.35,alpha=0.01) 或 (1.75,0.1)；max_iter=500，tol=1e-5 |
| `random_forest` | (max_depth=5,min_samples_leaf=20) 或 (9,40)；64 棵树，max_features=0.7 |
| `extra_trees` | 与随机森林相同的有限深度/叶节点网格；64 棵随机阈值树，max_features=0.7 |

随机模型固定 random_state=17；森林使用单线程。HGB 关闭内部随机 early stopping。手动模式只允许一个模型系列，但仍在该系列的有限参数内验证。8 类模型全选共 14 组配置；样本不足、没有足够有效日期或全部候选失败时，任务明确失败。树的训练不纯度重要性只用于描述拟合结果，相关特征会影响它，不能作为因果解释。

监督模型按需使用训练段 1%/99% 分位阈值去极值、训练段中位数填充和训练段 StandardScaler。验证/holdout 只变换，不重新拟合这些统计量。因子基线直接使用当日截面排名，不需要训练段缩放；其排名先于未来标签可用性的过滤完成。

选择指标为各有效日期预测分数与未来收益的截面 Spearman RankIC 的平均。一天至少需要 3 个有效且非恒定的预测/标签；股票池小于 10 只时，报告提示统计不稳定。平分按固定候选顺序处理。

`qualified` 仅表示开发期、嵌套 outer 和 holdout 的平均 RankIC 都为正。它不是显著性检验、赚钱保证或部署许可；`deploymentQualified` 始终为 false。任一窗口没有正向平均结果时显示 `NO_VALIDATED_EDGE`，但仍保留完整研究结果。

反复看同一个 holdout 后改因子仍会产生研究者选择偏差。框架无法知道用户在别处看过哪些历史；需要新的未查看区间或前向积累来验证新假设。

训练诊断只使用最终清除跨窗标签后的训练段，报告每个特征的覆盖率和绝对相关性至少 0.9 的特征对。不会根据留出期表现自动删除因子。大量相邻窗口往往高度相关，增加列数不等于增加独立信息。

## 逐股预测与解释

`predictions.rows` 保存终端留出期每个可预测日期/股票的分数、截面名次、预测目标、成熟后的实际目标和标签终点；`predictions.latest` 是最后一个可预测日期的排序。最终模型只在开发期拟合一次，不在留出期重选或重训。最近尚无完整未来观察窗口的 `actualTarget` 和 `labelEnd` 为 null。

监督回归模型的 `predictedTarget` 是选定收益目标的估计。因子基线只产生排序分数，`predictedTarget` 为 null，不把百分位当作预测收益。报告对成熟的监督预测给出 MAE、RMSE 和方向命中率；这些不等于校准概率或显著优势，也没有生成未经验证的置信区间。

逐股展示的 `trailingVolatility20Annualized` 与 `trailingDownside20Annualized` 是截至信号日的过去 20 个交易日风险乘以 `sqrt(252)`，不是未来风险预测。预测最早供次日研究成交使用；报告不将尚未知晓的下一开盘价当作已知输入。

## 组合与成交

头条净值和绩效只覆盖终端 holdout。每隔 `rebalanceDays`，用前一交易日信号选最高的 `topN` 个股票，目标权重为 `min(1/topN,maxWeight)`；合计不足 1 时留现金。

次日有开盘价且成交量为正才允许假设成交。缺失标的保持已有仓位，不使用之后价格补成交；信号不足时不调仓。先卖出，再按可用资金同比例缩放买入需求，维持非负现金。权重上限是调仓时的目标，不是价格变动后的连续强制上限。

数量是可为小数的**复权归一化研究单位**。`price` 是调整后的开盘参考价；`notional=quantity*price`。佣金与滑点按双边参考成交额计提，税费仅卖出计提，全部作为明确现金成本扣除；滑点没有重复嵌入 price。

每天验证：`equity = cash + sum(quantity * mark)`。缺少当日价格的持仓沿用最近价格并标记 stale；末日不强制清仓。不存在成交盘口、涨跌停/封板排队、手数约束、成交参与率、容量冲击、真实保证金或完整 A 股交易规则。

基准在 holdout 首日收盘按原股票池等权买入并持有，不计费用；没有首日可用价格/正成交量的份额留现金。用户当前选定股票池可能产生存活/事后选股偏差。

## 指标与审计

总收益和年化收益来自资金曲线；年化使用 252 个交易日。波动率为日收益样本标准差乘 `sqrt(252)`；Sharpe 使用零无风险利率。最大回撤为负比例。`turnover` 为总双边参考成交额除以平均日净值，不是一半口径的单边换手率。

报告包含参数、全部候选验证、outer folds、holdout、费用、逐笔交易、每日现金/持仓、数据与日历指纹、策略指纹、引擎版本。非有限的可选统计量序列化为 null，而不是伪造为零。复现还需要同样的数据、代码和锁定依赖。

[engine-v0.2-validation.json](engine-v0.2-validation.json) 记录本版数值测试与合成压力验算：50 只股票、8 年、32 因子、14 配置，在本次机器上约 288.63 秒，完整结果约 16.14 MB；现金、费用、持仓与净值独立复核通过。该样例持有全部股票，检验的是资源和账本边界，不是择股效果；本地耗时也不是服务时延保证。

[technical-validation.json](technical-validation.json) 保留 v0.1 本地合成验算的历史记录，包括完整结果重复相等。v0.2 测试继续检查严格 holdout 扰动不改变选择结果、缺失日期不改变切分边界，以及新增预测和外部字段时点约束。这些数值证据不等价于真实 provider、生产部署或前向盈利验证。

方法参考：[scikit-learn TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)、[数据泄漏与 Pipeline](https://scikit-learn.org/stable/common_pitfalls.html)、[Nested CV](https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html)。本项目使用按交易日期分组且显式 purge 的实现，不直接对展开后的股票行套用普通 K-fold。
