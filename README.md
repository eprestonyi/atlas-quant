# Atlas Quant

Atlas Quant 是嵌入 Atlas 的开源量化研究工作台：组合股票池，建立共同因子与对冲篮子，检验残差偏离和收敛规则，并核对费用、敞口与完整账本。代码采用 [Apache-2.0](LICENSE)，行情与第三方贡献的许可另计。

[打开 Atlas Quant](https://atlas-aletheia.com/quant/) · [GitHub](https://github.com/eprestonyi/atlas-quant) · [Discussions](https://github.com/eprestonyi/atlas-quant/discussions) · [Issues](https://github.com/eprestonyi/atlas-quant/issues)

本文描述 v0.3 源码。线上构建、runner 心跳、托管 Tushare 和 AI 能力以当前 [health](https://atlas-aletheia.com/quant/api/health) 返回为准；配置可用、一次实验成功和持续服务可用是不同证据。

## 能做什么

- 五步流程：**股票池 → 基准与因子 → 观察与信号 → 交易与成本 → 检验与报告**。Quant Studio 展开同一配置的数据、因子、共同驱动、规则、验证与代码工作区。
- 3 个基准：篮子均值残差、PCA 共同成分残差、PCA 加风格暴露；15 个因子包用于增加、比较对冲暴露。模板固定研究规则，不代表已验证收益。
- 397 个内置配方、50 个家族，覆盖量价、日度估值与公告时点财务字段；残差模式可不加因子，最多增加 32 个。因子在形成截止日提供股票暴露，帮助定义对冲空间；方向不是单股涨跌预测或选股排名。
- 机械入场、收敛退出、止损、最长持有期与半衰期诊断；观察频率、重新拟合和调仓频率分别设置。方法与阈值事先声明，形成模型只用过去信息。
- 记录形成载荷、残差事件、净/总敞口、净值、成本、全部交易和每日现金/头寸。增加因子时报告同条件基线差值；另有相同信号日程的零费用对照。残差事件预览最多最近 5,000 条并标明总数与截断；完整交易和账本不因此截断。
- 从 1,354 个真实成员池构造集合：组内 AND、组间 OR，加入个股后再剔除。完整结果、每步数量和目录哈希可核对；超过 50 只需继续筛选或明确选择本次子集，系统不会自动取前 20 只。[集合规则](docs/UNIVERSE_SELECTION.md)
- 分页浏览 Financial DB 四库字段。PCD 目录含 17,073 个字段，其中 4,870 个具有数值类型资格；每个数值字段提供 3 个待映射变换，加上内置配方共 15,007 个基础目录条目（社区新增另计）。**定义数量不是历史数据覆盖，也不是已验证 alpha 数量。**
- 浏览器 Python 由独立 Pyodide worker 执行；规则检查与可配置的 Cloudflare Workers AI 审查分别呈现。社区支持 DSL 定义、署名、许可与 Fork；AI 补丁由用户确认，不自动运行研究。

新默认研究对象是**对冲篮子的残差是否收敛**。等权共同成分不等于股票 beta 中性，AR(1) 半衰期不等于协整检验。计算使用 Atlas 自有日频研究引擎；没有 LEAN、Backtrader 或券商下单集成。[统计套利规则](docs/STAT_ARB.md)

多空结果使用**理论借券**假设。券源、实际融券条款、保证金和强制买回未验证；成交采用小数复权研究单位，未完整模拟手数、涨跌停队列和容量。它不是普通现金账户的可实盘证明。`qualified` 与 `deploymentQualified` 始终为 false，报告为 `UNVALIDATED_THEORETICAL_STAT_ARB`；净收益或基线改善也不构成盈利承诺。

旧版 8 类模型、14 组有限配置、单股预测和 TopN 长仓保留为 `legacy_long_only` / `factor` 兼容路径。加载旧配置保留原行为，不自动转成残差篮子。旧报告的 `NO_VALIDATED_EDGE` 同样是完整、有效的研究结果。[历史研究方法](docs/METHODOLOGY.md)

## Financial DB 与数据

Financial DB（FD）包含 **PCD / MKT / EXT / MODEL** 四个逻辑库：原始披露、市场数据、外部研究与供应商指标、内部计算与预测。它们可以共享基础设施，来源和版本边界仍然独立。`fd_` 与 `FD` 是供应商财务适配器的兼容标识，不是第五个库。[架构](docs/ARCHITECTURE.md)

来源必须明确选择：合成教学、自有上传或 Tushare。失败不会退回合成数据。Tushare 市场适配器按需请求 `trade_cal`、`daily`、`adj_factor`、`daily_basic`；财务适配器按所选字段请求 `fina_indicator` 的 21 项指标，公告后首个官方交易日才可进入特征。

PCD 字段目录可完整发现，但本次真实覆盖仅观察到 Apple 的三个财务事实，没有已映射的 A 股事实面板。文本、列表和嵌套记录可浏览，不能直接变成数值特征。证券池保存真实成员、快照日期与哈希；当前成员不等于历史成员。目录保留的旧推荐子集只是代码排序元数据，新界面不会自动采用；规则和明确子集一起保存，运行前重新核对。[来源验收](docs/DATA_SOURCES.md)

托管 Tushare 使用运营方配置的已授权接入，部署通过 `TUSHARE_PUBLIC_AUTHORIZED=true` 开启；是否已启用见 health 的 `capabilities.tushareHosted`。部署者需自行确认其服务与使用范围获得适用授权。开源代码许可不授予第三方数据再分发权，仓库不附带私有行情。[导入与数据边界](docs/DATA.md)

## 本地复现

需要 Python 3.12 和 uv。源码根目录运行：

```sh
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r engine/requirements.lock.txt
uv pip check --python .venv/bin/python
.venv/bin/python scripts/local-run.py engine/examples/stat-arb.json --source demo --output private/demo-report.json
```

该样例使用固定 seed 的 **SYNTHETIC 教学价格**，运行预先声明的 PCA 残差基准；合成序列不证明市场收敛或收益。旧模型示例仍保留在 [research-v02.json](engine/examples/research-v02.json)。锁定依赖在干净 Python 3.12.13 环境验证。输出为 `{"result": ...}`；失败为 `{"error": {"code": ..., "message": ...}}` 且退出码非零。`private/` 已被 Git 忽略。

自有数据使用 `{rows,provenance}` JSON，股票池与日期应匹配策略：

```sh
.venv/bin/python scripts/local-run.py engine/examples/stat-arb.json --source upload --dataset private/market.json --output private/upload-report.json
```

本地个人 Tushare 可通过环境变量输入 token。以下用交互式隐藏输入，避免把凭据写入命令历史：

```sh
read -r -s TUSHARE_TOKEN
export TUSHARE_TOKEN
.venv/bin/python scripts/local-run.py engine/examples/stat-arb.json --source tushare --output private/tushare-report.json
unset TUSHARE_TOKEN
```

使用已授权的私有代理或 PCD 时，可加 `--config /absolute/private/config.json`；该配置必须在仓库外、权限为 0600 或更严格。字段单位、复权、PCD 映射与可用时间见 [DATA.md](docs/DATA.md)。CLI 默认预算 900 秒，可用 `--timeout` 设置 30–900 秒。

## 浏览器与开发

```sh
npm ci
npm test
npm run check
npm run build
node scripts/dev.mjs
```

本地地址为 `http://127.0.0.1:8895/quant/`，启动时创建并填充字段/股票池目录。另开终端运行计算服务：

```sh
.venv/bin/python scripts/dev-runner.py
```

开发服务使用公开开发密钥与 loopback HTTP，仅用于本机；Miniflare 工作区随进程结束而丢失。开发 runner 的预算为 600 秒，正式 runner/CLI 默认 900 秒。开发环境没有自动配置真实 AI binding，规则检查仍可用，AI 请求会明确返回未配置。Node.js 26.7.0 为已验证构建环境。

浏览器 Python 与托管数值引擎是两条执行路径。Python 代码在浏览器隔离 worker 中执行，读取显式传入的 `data`，通过 `result` 输出；首次需从固定 CDN 下载 Pyodide 与包。其包环境不同于服务端锁定环境，输出不会自动成为托管回测。服务端只执行受限 DSL 策略。AI 模式会把待审查代码发送给配置的 Cloudflare 模型，补丁由用户选择应用。[API](docs/API.md) · [安全边界](SECURITY.md)

工作区由 HttpOnly cookie 识别，策略、实验和代码项目按工作区隔离。目前没有 Quant 与 Atlas 账号的统一同步或恢复；换浏览器、清除 cookie 前请导出需要的数据。同浏览器 Atlas 自选导入不代表账号同步。社区定义公开后标记 `community_unreviewed`，语法通过不代表质量、版权或收益已经审核。[贡献指南](CONTRIBUTING.md)

## 运行边界

| 项目 | v0.3 范围 |
|---|---|
| 研究市场 | 每次 3–50 只沪深 A 股；日频、理论多空；北交所目录可发现但本轮托管执行不支持 |
| 因子 | 残差模式 0–32 个额外暴露；旧长仓路径 1–32 个；总回看不超过 504 日 |
| 历史 | 最多 8 年、2,200 个交易日、110,000 行 |
| 有效样本 | 残差模式在形成窗与因子预热后至少 90 日；旧预测路径至少 180 个可研究日 |
| 观察 / 调仓 | 分别为 1–60 日；形成窗 60–504 日，残差窗口 20–252 日 |
| 上传 / 报告 | 上传 100–110,000 行，最大 24 MiB；完整报告最大 24 MiB |
| 工作区 | 同时 1 个实验，每天 20 次提交，最多 200 个策略、100 个代码项目 |
| 代码 / AI | 代码最多 40,000 字符；AI 每工作区每天 20 次、全服务每天 500 次 |
| 社区发布 | 每工作区每天最多 10 次；分页目录包括最近最多 1,000 个公开社区条目 |

额度与具体参数以当前 API 校验为准。原始上传在成功或取消后删除；其他终态任务的残留上传创建超过 30 天后按定时批次清理。策略、代码、报告和审计记录单独保留。[保留说明](docs/DATA.md)

## 验证证据

[v0.3 三基线真实数据评估](docs/BASELINE_EVALUATION.md) 保留固定八只股票、同一留出期的全部结果：均值残差 −9.06%、PCA −6.50%、PCA 加风格 +0.70%。这是一次预先固定配置的理论多空实验，未证明可执行套利或显著 alpha。

```sh
PYTHONPATH=engine .venv/bin/python -m pytest -q engine/tests
npm test
```

新路径的投影中性、无未来泄漏、全腿成交、残差状态与费用账本回归测试位于 `engine/tests/test_stat_arb*.py`；接口与股票池保存边界由 `tests/stat-arb.test.mjs`、`tests/universe.test.mjs` 与 `tests/release-boundaries.test.mjs` 验证。

[v0.2 数值验收](docs/engine-v0.2-validation.json) 记录 50 只股票、8 年、32 因子、14 配置的合成压力实验：本次机器约 288.63 秒，完整报告约 16.14 MB，现金、费用与持仓独立核对通过。该样例用于资源与记账验收，不证明收益效果或线上时延。

[真实来源验收](data/source-validation.json) 单独记录私有 provider 请求、字段覆盖和三只 A 股、2,001 行行情、公告财务指标、14 配置的完成实验；不公开原始行情和完整私有报告。[v0.1 数值记录](docs/technical-validation.json)、[v0.1 Tushare 记录](docs/tushare-validation.json)、[v0.1 发布快照](docs/release-validation.json) 保留为历史证据；其中旧测试计数与当时关闭的托管能力不用于推断当前部署状态。以上 v0.2 记录属于历史预测路径，不验证新残差基准的收益。

托管部署由浏览器、Cloudflare Worker + D1/R2、出站轮询的 Python runner 组成；配置、AI binding、恢复与单主机可用性见 [OPERATIONS.md](docs/OPERATIONS.md)。
