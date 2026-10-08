# Atlas Quant

Atlas Quant 是嵌入 Atlas 的开源统计量化研究工作区。先建立条件价格模型、保存完整预测，再用独立的对冲、风险和成交配置检验执行结果。代码采用 [Apache-2.0](LICENSE)，第三方数据许可另计。

本 financial 分支新增[冻结财报离线准备内核](docs/FINANCIAL_INPUT_PACKAGE.md)：16 个公式已通过 synthetic 多期数据回归，支持默认严格单位证明和显式、始终未核验的用户单位声明。它尚未接入线上目录或 F。两家公司 2024 年报的窄核验不能推广为全市场可用；真实每日面板仍缺可核验的官方日历来源，其他多期依赖也须分别满足。

[Atlas Quant](https://atlas-aletheia.com/quant/) · [源码](https://github.com/eprestonyi/atlas-quant) · [Discussions](https://github.com/eprestonyi/atlas-quant/discussions) · [Issues](https://github.com/eprestonyi/atlas-quant/issues)

**v0.6 已于 2026-10-08 部署，完整私有复现包与 Studio 确定语义已完成正式验收**。发布证据见 [v0.6 验收记录](docs/RELEASE_V06.md)，既有分片和真实数据研究见 [v0.5](docs/RELEASE_V05.md) 与 [v0.4](docs/RELEASE_V04.md)，当前服务状态以 [health](https://atlas-aletheia.com/quant/api/health) 为准。大规模整池研究与四库历史覆盖仍在继续建设；实施进度与未完成项见 [REBUILD_PLAN.md](docs/REBUILD_PLAN.md)。

v0.5 加入完整分片产物、按需报告和独立分片审计。[传输协议](docs/BUNDLE_TRANSPORT_V1.md) · [验收过程与失败修复记录](docs/BUNDLE_ACCEPTANCE_A.md)

## 研究协议

```text
V[t,h] = F_h(X_t) ≈ E[P[t+h] | I_t]
e[t,h] = P[t] - V[t,h]
P[t+h] - P[t] = -e[t,h] + (P[t+h] - V[t,h])
```

V 是指定期限的预期市场价格或冻结数量篮子状态，不必是当前内在价值。篮子的每条腿、单位、形成截止日和数量在一次预测中固定；PCA 或回归对冲负责定义目标，不能直接代替预测器。

收盘后产生信息，下一交易日开盘才能入场。因此 F 同时估计预期入场值和目标日值；执行采用两者之差，不能将收盘到次日开盘已经发生的变化计入预期可赚收益。实际 PnL 由真实模拟成交、费用和每日现金加有符号持仓计算。

[数理与产品协议](docs/FORECAST_RESEARCH_CONTRACT.md) · [精确配置和结果 schema](docs/STATISTICAL_QUANT_SCHEMA.md) · [API](docs/STATISTICAL_QUANT_API.md)

## 工作区

八个独立步骤：**研究范围 → 因子与状态 → 目标与期限 → F 预测模型 → 预测检验 → 对冲与风险 → 执行与成本 → 运行与报告**。

- 股票池逐层取交集、合并、手工增加和剔除，显示每一步计数。1,354 个有真实成员的目录池；超过单次计算范围时要求明确选择子集，不静默截断。[集合规则](docs/UNIVERSE_SELECTION.md)
- 普通研究通过模块选择和因子拖放建立配置；Studio 展开参数、DSL/Python 代码与 AI 审阅。两种界面共用同一研究版本。
- 目标支持单资产价格、固定数量篮子、两腿 OLS 篮子和 PCA 残差篮子。OLS 不等于协整证明，PCA 不保证均值回归。
- F 的状态族包含均值回归、配对相对价值、趋势条件预测、财务条件和事件反应。估计器为无变化、历史漂移、Ridge、Elastic Net、Histogram Gradient Boosting；自动选择比较预先限定的 8 个候选配置。数据不满足所选族的条件时明确失败。
- 预处理只在训练集拟合；按日期进行嵌套验证、标签成熟筛选和顺序样本外检验。终端报告期按预先声明的时钟滚动重拟合，可以使用此前已经成熟的报告期标签。
- 加入预测因子时，另行拟合相同目标、有效样本、验证日期和候选预算的 state-only 对照。对冲因子改变目标，不冒充纯预测因子增量。
- 聚合误差提供日期聚类的循环块 bootstrap 区间与块长敏感性；样本不足明确不可用。它依赖时间序列假设，不是单个价格的预测区间，也不纠正反复试策略的选择偏差。
- 完整预测包含未交易、失效和未成熟记录。报告能追溯 P、V、e、实际值、预测误差、目标定义、模型拟合与成交引用。
- 同一份冻结预测可以独立改变执行方向、门槛、持仓数、调仓频率、gross/net、个股和因子暴露、历史波动率规模与费用。回放不重新拟合，不向供应商重新取数。

模块目录是版本化职责和可组合配置。**原子模块、参数配方、字段定义、已跑实验、已验证 alpha 是不同事物。** 本次有限模型集与数百种兼容配方没有被称作数百种有效策略。做市、波动率/衍生品、复制关系/结构套利保留独立工作区边界，本版未实现。

## 数据与因子

Financial DB 包含 **PCD / MKT / EXT / MODEL** 四个逻辑库。[四库架构](docs/ARCHITECTURE.md)

397 个内置 DSL 配方覆盖量价、日度估值及公告时点财务指标。PCD 目录有 17,073 个字段，其中 4,870 个具有数值类型资格；每个数值字段可发现三个待映射变换。15,007 个基础目录条目不等于填充了这些历史序列，更不等于有这些 alpha。

- **MKT**：Tushare `trade_cal/daily/adj_factor`，按需调用 `daily_basic`。
- **EXT 财务适配器**：21 项 `fina_indicator` 指标，公告后的首个官方交易日才可用。`fd_` 是兼容前缀，不是第五个数据库。
- **PCD**：精确证券—主体—记录—字段—单位映射及保守可知时间。现有覆盖证据只观察到 Apple 三个事实，没有已映射的 A 股历史事实面板。
- **EXT/MODEL 自有序列**：可上传带逐值可知日期和来源声明的数值列；支持导入不代表既有外部共识数据库已经接通。

来源明确选择合成教学、自有上传或 Tushare。失败不会回退为合成行情。目录 `ready` 表示适配器/表达式可用，实际证券和日期覆盖仍在运行时检查。当前指数成员不能当作历史成员。[数据规范](docs/DATA.md) · [来源证据](docs/DATA_SOURCES.md)

托管 Tushare 由运营方配置已有授权接入，是否开放见 health。代码许可不授予数据再分发权；仓库不附私有行情。数据来源：Tushare数据。

## 本地复现

需要 Python 3.12 与 Node.js 24 或以上。源码根目录运行：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r engine/requirements.lock.txt
.venv/bin/python -m pip check
npm ci

# 第一步：完整预测研究与私有冻结输入。
.venv/bin/python scripts/local-run.py engine/examples/statistical-quant.json --source demo --output private/forecast.json --snapshot-output private/input.json
.venv/bin/python scripts/audit-report.py private/forecast.json

# 第二步：只复用原预测和原输入进行执行。
.venv/bin/python scripts/replay-execution.py private/forecast.json private/input.json --output private/execution.json
.venv/bin/python scripts/audit-report.py private/execution.json --source-report private/forecast.json
```

样例为固定 seed 的 **SYNTHETIC 教学数据**，不能证明市场规律。相同依赖和配置的预测报告可逐字复现。`audit-report.py` 只使用 Python 标准库，独立核对产物哈希、预测恒等式、交易引用、费用、T+1 数量、现金、持仓与净值，不调用引擎辅助函数或数据供应商。

v0.5 的分片目录保存完整预测、去因子对照、拟合前观察计划、报告及冻结输入。目标目录必须不存在，私有输入不会提交到仓库：

```sh
.venv/bin/python scripts/local-run.py engine/examples/statistical-quant.json --source demo --bundle-output private/forecast-bundle
.venv/bin/python scripts/audit-bundle.py private/forecast-bundle
.venv/bin/python scripts/replay-execution.py --source-bundle private/forecast-bundle --bundle-output private/execution-bundle
.venv/bin/python scripts/audit-bundle.py private/execution-bundle --source-bundle private/forecast-bundle
```

`audit-bundle.py` 使用标准库逐片复核原始字节、完整覆盖、引用和现金账本，不导入研究引擎。浏览器按已提交索引筛选与翻页。完整报告 JSON 用于阅读与分析；新增的私有复现包同时包含冻结输入与分片清单，可在本机严格导入：

```sh
python3 scripts/extract-bundle.py atlas-quant-run-bundle.tar private/reproduced
```

导入完成前会独立审计，已有目录不被覆盖。执行记录包还需要原始预测目录，详见[私有复现包](docs/BUNDLE_EXPORT.md)。旧版单包报告继续保留 JSON 下载。

执行覆盖文件只能包含 `execution`、`portfolio`、`costs`。例如保存为 `private/execution-overrides.json`：

```json
{"portfolio":{"rebalanceDays":5,"netExposureLimit":0.2},"execution":{"minEdgeBps":20}}
```

```sh
.venv/bin/python scripts/replay-execution.py private/forecast.json private/input.json --overrides private/execution-overrides.json --output private/execution-2.json
```

`--source upload --dataset private/market.json` 使用 `{rows,provenance}`。真实 Tushare 可通过环境变量 `TUSHARE_TOKEN`，或 `--config /absolute/private/config.json` 使用已有私有接入；不要将 token 放进代码或策略。配置必须位于仓库外且权限 0600。`--no-cache` 用于需要证明新请求时的明确冷读取。[数据格式](docs/DATA.md)

`--snapshot-output` 才会本地保存冻结输入（0600）；报告导出不包含该原始输入。浏览器托管研究将其保存在私有 R2，以支持之后同数据回放；它与临时原始上传有不同的保留规则。

## 全栈开发与验证

```sh
npm run build
node scripts/dev.mjs
```

另一个终端运行：

```sh
.venv/bin/python scripts/dev-runner.py
```

打开 `http://127.0.0.1:8895/quant/`。开发服务仅监听 loopback，使用公开开发密钥，Miniflare 数据随进程结束丢失。可用相同 `PORT` 指定另一独立预览端口；开发 runner 可显式接收外部 `--config`，只复制数据接入配置，不更改正式队列或密钥。

```sh
PYTHONPATH=engine .venv/bin/python -m pytest -q engine/tests
npm test
npm run check
npm run build
node web/tests/statistical-dom.mjs
```

CI 运行 Python/Node 回归、真实 DOM 操作及两次确定性预测，再用冻结输入独立执行和标准库审计。Node 集成测试包含实际 Miniflare、Python 子进程、R2 保存和 JavaScript JSON 往返；测试使用明确合成数据。真实提供商、浏览器、生产部署和持续服务分别验收，不由测试替代。

## 边界与历史兼容

| 项目 | v0.5 已发布范围 |
|---|---|
| 研究范围 | 单次 1–50 只沪深 A 股；日频；北交所目录可发现，托管引擎暂不支持 |
| 冻结目标 | Pair 恰好两腿；PCA 3–20 腿；显式固定数量 |
| 输入 / 输出 | 最多 32 因子、110,000 输入行、25,000 完整终端预测；不截断完整产物 |
| 历史 / 期限 | 最多 8 年、2,200 交易日；预测期限 1–60 日 |
| 资源 | 单计算槽；计算 900 秒；分片传输总预算 300 秒，每请求最多 60 秒；单片硬上限 8 MiB，总计最多 256 MiB / 256 片 / 100 万条记录 |
| 工作区 | 同时 1 个实验、每天 20 次提交；具体限制以 API 校验为准 |
| 用户代码 | 浏览器隔离 Pyodide；受信服务端只执行受限 DSL 配置 |
| AI | 显式选择的真实 Workers AI 审阅，和规则检查分开；建议须用户应用 |

执行采用小数复权研究单位、理论借券、声明的固定费用。券源、实际融资、手数、涨跌停排队和容量没有完成实盘验证。一次预测改善或正收益不能证明套利成立；`qualified/deploymentQualified` 保持 false。没有实盘下单。

分片上限是传输边界，不是扩大计算范围的承诺。50 股 / 32 因子等上限仍生效；大报告通过完整分片保存，未截掉失效、未成熟或对照记录。旧单包研究继续按原协议读取。

模型拟合产物保存版本、输入列、样本边界、参数、变换与线性系数等审计记录；它不是通用序列化的 HGB 模型包。执行回放使用冻结预测，不重建模型。

工作区由 HttpOnly cookie 识别，尚无 Atlas 账号统一同步/恢复。清除 cookie 前应导出研究。社区公开的是因子定义、署名、许可和 Fork 关系，不公开私有报告或原始数据。浏览器 Python 首次从固定 CDN 加载 Pyodide，与托管环境不同。[安全](SECURITY.md) · [贡献](CONTRIBUTING.md)

历史 v0.3 `stat_arb` 的 z-score 规则和 v0.2 `legacy_long_only/factor` 保留原 schema 与结果语义，只在历史入口显示。它们不会被伪装成新的 F 预测产物。[v0.3 合约](docs/CONTRACT.md) · [历史统计套利规则](docs/STAT_ARB.md) · [v0.2 方法](docs/METHODOLOGY.md)

部署由 Cloudflare Worker、D1/R2 和独立 Python 主机组成。增量迁移、租约、加密断点投递及兼容回滚见 [OPERATIONS.md](docs/OPERATIONS.md)。单主机运行不等于高可用，托管状态需实时读回。
