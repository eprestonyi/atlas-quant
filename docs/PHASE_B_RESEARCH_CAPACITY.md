# Phase B：完整证券池预测与因子研究容量方案

状态：**待审设计，尚未实现，也不是当前服务能力**。本文件基于 Phase A worktree 的实际代码与合成资源证据，提出下一轮可实施的数值／数据／内存方案；不修改现有上限，不承诺供应商权限、运行时间或盈利。Phase A 仍须独立完成 Worker、runner、审计和 GUI 的链路验收。

Phase A 的最终传输合同是 [BUNDLE_TRANSPORT_V1.md](BUNDLE_TRANSPORT_V1.md)：保留 forecast artifact v1 的逻辑身份，新增 `atlas.quant.bundle/1`；不是把 artifactId 改成 manifest hash。早期 [SCALING_DESIGN.md](SCALING_DESIGN.md) 中“Phase A 直接引入 artifact v2／Parquet”的建议已被这个合同替代。

## 1. 先固定预测对象和训练语义

当前 `asset_price` 为每证券生成一个价格目标，但 `validation._train` 合并同一截止前所有有效证券样本，拟合**一个 pooled 双输出 F**。两个输出分别估计下一开盘与目标开盘相对当前状态的标准化变化；不是每证券独立选一个 F。每个目标还原价格时继续使用该 origin 的已知价格与 gross scale。

新增整池容量必须保留：

- 一个全池数据／成员版本、一套官方日期与 observation schedule、一套截止／标签成熟规则。
- 每个训练折在完整训练样本上拟合截尾、median 填补、标准化、去相关；基线使用同一目标、input mask 和折计划，单独选择／拟合。
- 训练损失仍为当前估计器的行权重；选模、终段指标和增量比较为日期等权。不能借扩容把训练也悄悄改为日期等权。
- 横截面 `rank`／`zscore` 的域是当前明确的完整研究池；缺失不补零，重复值排名、标准差定义和有效计数保持现实现。
- 完整预测包括 input invalid、model unavailable 与日历尾部未成熟 origin。原始 origin plan 在模型拟合前冻结；不以成功返回的预测反推计划。
- 同时执行时仍只有一个现金账户与净持仓账本，按日期统一处理所有 forecastId；不能把子池净值相加。

`per_target` 模型、行业模型、分组模型、逐股调参、日期等权训练都是可研究的新定义，**不在本次透明扩容之内**。未来若提供，需明确 `modelScope`、分组时点、样本最低要求与全新实验预算；不能称为原 pooled 模型的分布式实现。

## 2. 当前直接放大的具体问题

代码位置：`statistical_quant/targets.py`、`validation.py`、`models.py`、`risk.py`、`core.py`，以及 `factors.py`、`provider.py`。

1. `build_samples` 在每个日期／目标内反复 pandas `.loc`，创建 61 日历史窗口、因子小数组及 Python dict。单资产有 N 个 origin／观察日；PCA 每个目标又可能有 N 条腿，不能用单资产的线性估计覆盖它。
2. panel、每个因子宽表、样本 X/y/meta、主／基线记录、快照与报告可能同时存在。Phase A 解决的是有界传输和解析，数值核心仍是全内存 pandas／Python 对象；把 `.npy` 放到磁盘而随后整表 `copy()` 不能宣称内存已受控。
3. `_train` 每折重新取 mask、复制训练矩阵、计算相同预处理。auto 实际是 8 个有限配置，其中 HGB 为两个独立输出模型，不能按一次单输出拟合预算。
4. provider 现缓存键含整个 symbols/start/end/fields；不同池、重叠区间或新增字段不能有效共享，且缓存仅 1 小时。整池冷读按证券获取 daily/adj_factor，现 512 次真实尝试预算容不下 300 股的最少 601 次，更不用说 1000 股的 2001 次。此处仅为当前循环计数，不是接口额度声明。
5. Phase A 上限仍是 50 证券、110,000 输入／样本、25,000 主预测、256 MiB 父 bundle、1,000,000 集合行与 900 秒计算。不能只把 symbols 上限改为 1000。

## 3. 可复核的数量和资源计划

以下按 T 个交易日、61 日预热、每日观察、20% 终段估算；实际计划必须在冻结日历和 DSL lookback 后重新精确计算。输入缺行、长 lookback、形成失败与尾部预测不能被估计式隐藏。

| 计划例子 | 输入格点 N×T | 样本约 N×(T−61) | 主终段预测约 | 主＋基线预测约 |
| --- | ---: | ---: | ---: | ---: |
| 300 股／3 年（T=750） | 225,000 | 206,700 | 41,400 | 82,800 |
| 300 股／5 年（T=1250） | 375,000 | 356,700 | 71,400 | 142,800 |
| 1000 股／5 年（T=1250） | 1,250,000 | 1,189,000 | 238,000 | 476,000 |

forecast 行数量还另有同量 plannedOrigins、模型／目标审计及快照行。1000 股／5 年仅上述三类集合就约 1,964,000 行，已超过 Phase A 的 1,000,000 行。每5日观察能减少样本／预测，但这是用户明确选择的另一观察频率，不能暗中用它替换日频研究。

假设 K=12 个行情／来源数值列、F=32 个因子、S=5 个状态特征，均 float64：

| 数组，未含模型和对象开销 | 300×750 | 1000×1250 |
| --- | ---: | ---: |
| 行情数值列 8×N×T×K | 21.6 MB | 120 MB |
| 因子矩阵 8×N×T×F | 57.6 MB | 320 MB |
| 训练输入原始矩阵 8×样本×(F+S) | 61.2 MB | 352.0 MB |
| 双标签 16×样本 | 3.3 MB | 19.0 MB |

这里 MB 是十进制；不是 RSS 保证。全长 X 原始副本、imputer/winsor/scaler 输出、sklearn 内部数组、feature mask、索引、Python records、allocator 与文件缓存都需另外计量。最大训练窗口一般小于全历史，但 boolean fancy-indexing 会新分配当前训练矩阵。应按进程 RSS 实测控制准入，不能只相加 `.nbytes`。

**Phase A 已测证据（只证明原规模）**：固定 50 股／8 年／32 因子／auto 的合成研究有 104,350 输入、19,700 主预测和同量基线、394 台账日、0 成交；原报告 32,994,262 B，快照 24,473,216 B，36 片约 59.88 MB，228.28 秒，峰值 RSS 约 791 MB；独立标准库审计 424,135 项通过。该实验未调参挑选结果；它不是 300／1000 股测试，也不能用线性外推承诺耗时。

按该 JSON 字节密度粗估，300×750 的报告、快照和 coverage 约为百余 MB，可能仍在 Phase A 256 MiB 内；1000×1250 很可能超过。准入必须使用实际列与小样序列化得到上界，加运行时硬 cap；不能把估计值当完整产物大小。

## 4. 数据与特征先做一次，模型共享不可变矩阵

### 数据集层

新增独立采集／导入任务，不让模型运行现场逐股抓全部历史。键至少含 provider/endpoint/字段集、证券、日期区间、供应商修订快照、调整规则、日历根、单位与 PIT 规则版本。采集可按证券／月份分区，但完成后生成一份明确的完整数据集清单；若不完整，逐证券／日期原因必须保留。

原始读取缓存与研究快照分开。缓存更新可以生成新版本；已提交研究继续引用原快照，不能 TTL 到期后回放时补取最新数据。财务／PCD／EXT／MODEL 仍逐行验证 available_date 和 source/path；没有真实数值的字段目录不得生成伪列。

第一垂直切片使用 deterministic synthetic 或显式上传的完整数据集。真实 Tushare 采集放在独立验收门槛：先验证当前授权入口的字段、分页、历史范围、实际尝试与速率预算，再选择按证券或日期读取。**本设计不假设账号有任何新增接口权限**。当前池回看与历史成分研究要分开命名；只有有证据的成分生效历史才允许 PIT membership。

### 内存与本地 panel cache

首轮用 NumPy float64 `.npy`／memmap 与显式 null/有效性掩码即可，不必立即引入 Arrow。数组 axis 固定为官方日期×完整证券；字符串身份存一次，样本采用整型 dateIndex/securityIndex/originIndex。private cache manifest 记录 dtype、shape、排序、source root、每文件 hash 和创建版本；文件以临时路径写完、fsync、验证后原子发布，只读打开。

避免重复 materialize：provider frame 冻结后转数组并释放；特征按依赖 DAG 写只读列；同一候选／折只读取需要的训练行。横截面节点可对完整当日 300／1000 数组一次计算，不需要把整个 T×N 全部驻留。计算过程受 memory budget 监控，超过预算明确失败并保留已验证缓存；不静默降到 float32、不丢字段、不删证券。

### DSL 计算切分

把已验证 AST 编译成具有 `temporal/cross_section/pointwise` 节点类型的 IR；公共子表达式只算一次。时间序列节点按证券块、时间 halo 计算；横截面节点按完整日期做 barrier/reducer。嵌套 `ts_mean(rank(returns(close,5)),20)` 必须先完成全池 rank，再滚动20日；`rank(ts_mean(...))` 顺序不同，不能只检查最外层函数。

cache key 必须含完整股票池顺序／membership、日历、source root、AST/IR 版本、数值规则和缺失策略。不同池的 rank 不能共用输出缓存。时间分区 halo 由 AST 的精确 lookback 推导；chunk 最终只输出所属 core 日期，避免重复边界样本。

## 5. 模型与验证计算预算

记 B=1（无因子基线）或2，候选数 C，内折 I、外折 O、终段再拟合次数 R。完整训练次数上界近似：

`B × [C × I × (O + 1) + O + R]`。

R 必须由实际 observation schedule/refitDays 和失败重拟合规则精确模拟，不能仅 `终段日数/refitDays` 后向下取整。样本无效导致某些 fit 失败仍占尝试预算。每次 fit 包含双标签；HGB 内部会拟合两个回归器。主／基线共享目标与 immutable feature state，但不会共享选出的冠军。

首切片采用现有 Ridge 两个 alpha，I=O=2，20交易日 refit。若终段约138日，则 R 约7，上界约42次 pooled fits；auto C=8 则约114次。实际 fit/task plan 在运行前列出，不通过减折、删候选或漏基线缩短运行。

推荐先保持每 fit 单线程、一个 fit worker；数据、特征和模型一份共享只读磁盘缓存。证明 RSS 安全后才开放2个候选 worker；BLAS线程×候选进程必须统一预算。不能两个 worker 各自加载数份完整 Python panel 后以“并行”掩盖内存翻倍。

预处理在同一训练 mask 上可缓存；key 包含 mask root、列顺序、winsorize/impute/standardize/decorrelation 设置。复用全局 median/quantile/相关计算必须产生与参考训练折一致的结果。均值、方差、相关可用合并统计量，但全局 median 不是分块 median 的平均。第一步继续精确 quantile/median；若加入近似 quantile，应是显式新数值版本。

不要求一开始分布式训练所有算法。Ridge 的充分统计量归约可作为后续优化，但默认求解器、截距与数值稳定性要独立证明；不能无声明用条件数更差的正规方程替代。ElasticNet 与 HGB 保持当前单机全池训练，超内存明确不支持。不得平均局部子模型冒充同一个 F。

全局模型被冻结后，预测可按 origin 批次执行并按计划顺序写 bundle；日期 loss 先聚合 sum/count，再对日期等权平均。均值的均值会在各块有效证券数不同的情况下改变指标。bootstrap 按完整日期合并、保持原重叠标签块规则；不把股票×日期当 IID。

## 6. PCA 与组合执行必须另外设容量门槛

第一垂直切片只做全池单资产目标的预测／因子增量，保留篮子20腿上限；这不是把 PCA 策略悄悄换成单资产模型。目标类型必须由配置明确选择。

当前 PCA 形成矩阵为 W×N，完整 SVD 通常为 `O(min(W,N)^2×max(W,N))`；构造投影 `M=I−B B+` 和 q 至少有 N² 输出。每个 target 还重复 loadings 审计，序列化可能达到每形成期 `O(N²×k)`；每 origin/target 用61日全腿状态的朴素实现为 `O(61×N²)`。把 N=1000 直接代入并不只是“多跑一点”。

若下一步确需全池共同成分，应单独设计 `hedgeFit` 存一份 B、q/投影矩阵分片、各 target 引列的逻辑 artifact 新版本；状态计算改矩阵批量运算。在 k 小时可用 `M v = v−B(B+v)` 避免为部分运算 materialize M，但全目标冻结 q 与执行腿仍需有完整可审计表示。随机/近似 PCA、稀疏对冲、筛掉腿会改变数值或策略，须显式版本化，不属于压缩。

风险层现每天对全池协方差做 PSD 特征分解（volatility_target 时）。即使只持少数股票，直接 1000×1000 `eigh` 仍有立方复杂度；预测整池通过不代表执行整池通过。下一切片应对 active/proposed legs 的等价风险计算证明与参考全矩阵一致，或明确新增低秩风险模型版本。净暴露、因子暴露、T+1、原子篮子、借券费用及日权益恒等式都按统一账户重放，不能独立股票分片结算。

## 7. 协议与预算的精确下一步

新增版本化 capacity profile，放在提交／计划元数据，不隐式改变 sourceStrategy 的预测参数。服务端产生不可变 `resourcePlanId`，绑定 normalized strategy、dataset root、membership root、engine/dependency/feature IR 版本和精确 origin/fold 计划。用户研究配置与资源调度记录分开哈希，但产物必须引用二者。

建议首个 profile `pooled_asset_300_v1`（**拟议值，须基准校准**）：

| 字段 | 首切片拟议硬门槛 |
| --- | --- |
| target/model | asset_price；pooled；Ridge两配置；forecast-only |
| 证券／期间／因子 | 完整300股；3年；≤16因子 |
| 输入与 samples | 各≤250,000；计划不得自动抽样 |
| 主 forecasts | ≤60,000；基线同量完整保留 |
| 模型与折 | C=2，I=O=2；refitDays≥20；逐项预先列fit计划 |
| 单 fit/阶段/父预算 | 单 fit≤300s；纯计算阶段≤900s；父计算总wall≤1800s；不含未授权采集 |
| 内存／临时盘／并发 | worker RSS≤3GiB；private临时盘≤4GiB；1fit槽/1BLAS线程 |
| 传输 | 优先维持 bundle/1 的256MiB/256片/1m行；若精确计划超过则明确拒绝 |

这些是实验准入起点，不是 SLA；若真实基准不通过，就调整计划或实现，不能把300自动切成50股。父预算累计失败／重试尝试；重启不重置使用量。heartbeat只维持领取租约，不增加计算预算。

1000股日频5年需要下一 profile，不能沿用上表假装已支持：预计输入约125万、主预测约24万，需更大 row/index/storage 预算及内存证明。建议此时把 dataset 作为独立已提交 bundle 根复用，forecast/execution 只引用原 dataset 根；无需每次在每个报告里重传百万行行情。原 bundle/1 manifest 不接受这些新增引用，必须新增明确 transport/manifest 版本并保留旧解析器；不能同名协议偷偷改变允许的document集合。新 profile 的大小／索引总数／manifest层级预算在300股测量后定值。

阶段调度记录至少含：`planId/stageId/attemptId/leaseFence/inputRoots/outputRoots/status/consumedCpuSeconds/consumedWallSeconds/peakRss/bytesRead/bytesWritten`。拟合审计 JSON 不是可恢复 sklearn 模型；首次只在已验证特征或预测分片边界恢复。若需保存 fitted model，另建安全、版本锁定的结构格式，不加载不可信 pickle。失败后继续已提交数据读取与产物上传不重算；重新拟合须原计划允许且计入父预算。

## 8. 最有价值的首个垂直切片与自动验收

**交付目标**：从一份已冻结、完整300成员／3年数据，到全池横截面因子、一个 pooled 双输出 Ridge、完整嵌套检验与因子基线，最后实际 Worker 分片提交、GUI 全池分页、CLI 下载和独立审计。只做 forecast-only 首切片；随后另验一个全池账户的执行，不把第一步称为整池可交易系统。

顺序：

1. 建 `PanelStore` 与精确资产目标数组 builder，维持原 date/security/origin 排序。50股用原实现作 oracle；对齐全部特征、标签、计划、选模、预测与缺失原因。
2. 编译全局 DSL 依赖图并缓存；验 `rank`、`zscore` 及两种嵌套方向。故意将证券块分为不均匀尺寸、加入停牌/缺字段，证明结果不依赖块大小。
3. 在一个 worker 的全局训练矩阵上跑实际完整300股，预声明16因子和两Ridge候选；不得换池挑结果。记录所有 fit 次数、RSS、时间、失败与覆盖，不只记录性能最好的成功样本。
4. 用同一 snapshot 做 future perturbation：截止之后价格/财务披露变动不得改变此前特征、q、fit与预测。修改池成员必须改变依赖该全池 rank 的特征根。
5. 核对每 origin 主/基线覆盖、date-balanced loss 与旧独立审计；缺模型导致输出mask不同仍完整报告。分片损坏/遗漏/重复必须无法finalize，最大边界超预算必须明确失败。
6. 实际 Worker＋runner：分片续传、取消、丢ACK、租约过期、进程重启，全部不得重复模型拟合或发布partial。GUI请求单页只读对应chunks，完整300目标可遍历且统计总数与manifest一致。
7. 独立清洁依赖环境复跑首切片；锁定版本/seed时逻辑结果可复现。实现优化允许预先声明的浮点容差，但先证明预测／选择／门槛决策一致；若浮点变化导致排序、入场或artifactId变化，必须作为数值版本变更记录，不能谎称字节相同。

进入1000股阶段的门槛是以上完整300闭环与资源证据，而不是目录中已有1000个名称。PCA全池、事件稀疏条件、财务覆盖、volatility-target执行与更大auto搜索各有自己的可用数据及计算门槛；这些能力不能从单资产Ridge验收自动继承。
