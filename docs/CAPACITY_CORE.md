# 完整证券池数值原型

状态：本地 Phase B 原型，尚未接入公开 API、runner 队列或 GUI。当前托管服务的 50 证券、32 因子及既有数值预算不变。本文记录已实现接口和可复核边界；完整 300 股资源证据另行生成，不能由小规模等价测试推定。

## 接口

```python
from atlas_quant.capacity import run_capacity_research

result = run_capacity_research(
    strategy, data, provenance,
    profile_id="pooled_asset_300_v1",
    cache_dir="/private/new-research-cache",
    plan_sink=save_complete_origin_plan,
)
```

`data`、`strategy`、逐行 PIT 和来源合同沿用现有研究引擎。只有显式 `profile_id` 调用新准入；`run_research`、普通 `validate`、provider 和 hosted runner 未提高上限。未知 profile 明确拒绝。此接口是进程内库函数，不是独立的硬资源隔离器。

| Profile 约束 | 本地实现 |
| --- | --- |
| 目标与拟合范围 | `asset_price`，全部研究池行共同拟合一个双输出模型 |
| 模型 | 现有 Ridge 的两个固定 alpha 候选；不使用独立证券子模型 |
| 证券、期间、因子 | 至多 300、至多 1098 个日历日、至多 16 个因子 |
| 完整数据行、样本、主预测 | 分别至多 250,000、250,000、60,000；不截断 |
| 验证 | 2 个内折、2 个外折，`refitDays >= 20`；同原引擎的成熟标签、滚动窗口和终段规则 |
| 执行 | `execution.enabled=false`；未证明整池组合执行能力 |
| 数值 | float64，原有缺失处理、训练行权重、日期等权评分与单线程拟合 |
| 库函数预算 | cache 400 MiB、进程峰值 RSS 3 GiB、阶段间检查总时长 1800 秒 |
| 基准独立监督器 | 整个子进程 900 秒、单次 fit 300 秒、RSS 3 GiB、全部产物 400 MiB、系统余盘至少 500 MiB |

`result.capacity` 记录 profile、完整成员、精确样本与预测数量、panel/feature 根、资源计划 ID、实际拟合次数、每次拟合耗时、cache 字节数、峰值 RSS 和 `hostedApiEnabled:false`。这些调度字段不进入 forecast v1 的逻辑身份。

## 实现与精确等价

`PanelStore.prepare` 先执行原有完整数据/PIT 校验，再以日期升序、证券升序的完整格点存储只读 `.npy` memmap。缺行保留 NaN，不前向或后向填充。缓存身份包含实际字段字节和 NaN 掩码、日历、证券池、来源、可用日期审计和版本，不能仅靠供应商声明或文件名命中。

`FeatureGraph` 将受限 DSL 编译为带版本的公共子表达式 DAG。时间算子按证券计算完整时间历史；横截面算子每次读取完整当日证券池。当前没有时间分区或近似分位数。`ts_mean(rank(...),20)` 与 `rank(ts_mean(...,20))` 按各自依赖顺序计算。不同证券块大小不改变排名域；不会将 50 股局部排名拼成全池排名。

资产样本构造器复用原状态公式、观察频率、下一开盘及目标开盘标签和所有无效 origin。X/y 使用列连续的 Fortran 内存顺序，以保持原 pandas 样本布局；这一细节会影响 BLAS 末位舍入，不能只检查输入数值接近而忽略产物 ID 变化。

主模型与状态基线共用原有 pooled 选择及验证函数，因子基线仍单独选模、单独拟合。完整预测 artifact 包括无效、未成熟和缺模型行。模型拟合前生成完整 origin plan，不用返回成功行反推计划。小规模测试逐项比较特征、X/y、meta、目标定义、完整 artifact、所有逻辑 ID、最终选择及因子增量，不仅比较聚合指标。

缓存写入 private 临时目录，文件写完并 fsync、核验 SHA/形状/dtype 后原子发布。读取已发布缓存重新核对 manifest 与所有数组哈希。失败不覆盖已有缓存或源数据；不加载 pickle。样本数组也是写完后发布，但目前不支持跨进程中断后的模型恢复。

独立复核在固定 300 股实验之后发现并修复两项资源边界：缓存命中也必须在读取数组前服从本次调用的字节预算；样本写入失败必须删除仅属于本次调用的未发布 staging。13 项独立反例现全部通过，覆盖缺证券/缺格、四类状态、完整 51 股横截面、基线仅移除因子列以及资源异常不被模型选择吞掉。此后源码已变化，原 300 股实验的 sourceRoot 和资源测量仍指向当时冻结版本，不能当作修复后重新测量。

## 固定 300 股资源实验

```bash
PYTHONPATH=engine python scripts/benchmark-capacity.py \
  --output private/capacity-300-first --plan-only

# 确认同机没有其他大型研究、内存和磁盘满足预算后执行。
PYTHONPATH=engine python scripts/benchmark-capacity.py \
  --output private/capacity-300-first
```

计划固定为 2023-01-01 至 2025-12-31、300 个合成代码、783 个工作日日历日期、234,900 行、16 个预声明表达式及 32 个 DAG 节点；numpy seed 为 `20261008`。观察间隔 1 日、目标 5 日、训练窗口 504 日、重拟合 20 日、20% 终段和 2×2 验证固定，不搜索证券池或挑选收益。合成代码并非沪深300成分；工作日日历未冒充官方交易日。provider 调用数为 0。

监督器要求新输出目录，不覆盖旧实验。它写入 `admission.json`、`progress.json`、`summary.json`、`supervisor.json` 和完整 `bundle/`。报告/快照采用现有 bundle/1，不重复写整包 JSON；超预算保留失败证据并明确失败。成功后可用 `scripts/audit-bundle.py` 独立核验完整计划、原始字节、引用和数值关系。

拟合预算同时记录正常重拟合计划和较宽的失败重试上界。原引擎在模型不可用时可能每个观察日再尝试，原型没有悄悄改成只在每20日重试。实际每次进入估计器的调用都会计数。

## 尚未解决的容量边界

memmap 并不表示全流程常量内存：原数据 frame、准备后的 panel、样本 meta、sklearn 的训练折复制、预处理和完整预测 Python records 仍存在。峰值 RSS 必须实测。当前 library 只在阶段/fit 边界检查资源，硬中断来自 benchmark 子进程监督器；未接入生产计算调度。

此原型未扩展 provider 请求预算、PCA 全池形成、执行/风险账户、1000 股 profile、HGB/auto 大搜索或真实财务覆盖。普通执行回放仍使用旧 profile 验证器，因此新 300 股 artifact 当前只作为本地 forecast-only 研究产物，不能提交旧公开执行接口。后续整池闭环与可恢复计划见 [PHASE_B_RESEARCH_CAPACITY.md](PHASE_B_RESEARCH_CAPACITY.md)。
