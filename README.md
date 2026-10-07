# Atlas Quant

Atlas Quant 是嵌入 Atlas 的开源量化研究工作台：从股票池、数据字段和因子搭建日频研究流程，比较统计与机器学习模型，检查逐股预测和完整账本，并分享因子定义。代码采用 [Apache-2.0](LICENSE)，行情与第三方贡献的许可另计。

[打开 Atlas Quant](https://atlas-aletheia.com/quant/) · [GitHub](https://github.com/eprestonyi/atlas-quant) · [Discussions](https://github.com/eprestonyi/atlas-quant/discussions) · [Issues](https://github.com/eprestonyi/atlas-quant/issues)

本文描述 v0.2 源码。线上构建、runner 心跳、托管 Tushare 和 AI 能力以当前 [health](https://atlas-aletheia.com/quant/api/health) 返回为准；配置可用、一次实验成功和持续服务可用是不同证据。

## 能做什么

- 普通模式从股票池与研究目标开始；Quant Studio 分别提供数据、因子、标签、模型、验证、组合、报告与代码工作区。15 个精选因子包对应 15 个策略模板，包括财务质量、增长改善和现金流质量；分组是研究组织方式，当前引擎仍使用扁平因子集。
- 397 个内置配方、50 个家族，覆盖量价、日度估值与公告时点财务字段；每次最多 32 个因子。社区支持 DSL 定义、署名、许可与 Fork。
- 8 类模型、14 组有限配置：因子基线、Ridge、ElasticNet、Histogram Gradient Boosting、Bayesian Ridge、Huber、Random Forest、Extra Trees。
- 两种目标：未来开盘到开盘收益，或相对所选股票池平均收益；按唯一日期嵌套 walk-forward，清除跨窗标签，最后约 20% 留作独立 holdout。
- 留出期逐股分数、预测/实际目标、最新排序、历史风险、候选验证、净值、RankIC、费用、交易与每日现金/持仓；完整 JSON 导出。
- 分页浏览 Financial DB 字段和 1,354 个真实成员股票池快照。PCD 目录含 17,073 个字段，其中 4,870 个具有数值类型资格；每个数值字段生成 3 个待映射变换定义，共 14,610 个，加上 397 个内置配方构成 15,007 个基础目录条目（社区新增另计）。**定义数量不是历史数据覆盖，也不是已验证 alpha 数量。**
- 浏览器 Python 通过独立 Pyodide worker 运行用户代码；规则检查与可配置的 Cloudflare Workers AI 代码审查分开呈现。AI 返回待确认的补丁，不自动执行研究。

计算使用 **Atlas 自有日频研究引擎 + scikit-learn**，没有 LEAN 或 Backtrader 执行集成。成交采用次日开盘的复权研究单位，尚未模拟全部 A 股手数、涨跌停和市场容量规则；没有真实券商下单或持续实盘账户服务。

“优选”只表示预设候选在指定验证窗口中得分最高。`NO_VALIDATED_EDGE` 会保留在报告中；正的窗口平均 RankIC 也不代表统计显著、全局最优或盈利。[研究方法](docs/METHODOLOGY.md)

## Financial DB 与数据

Financial DB（FD）包含 **PCD / MKT / EXT / MODEL** 四个逻辑库：原始披露、市场数据、外部研究与供应商指标、内部计算与预测。它们可以共享基础设施，来源和版本边界仍然独立。`fd_` 与 `FD` 是供应商财务适配器的兼容标识，不是第五个库。[架构](docs/ARCHITECTURE.md)

来源必须明确选择：合成教学、自有上传或 Tushare。失败不会退回合成数据。Tushare 市场适配器按需请求 `trade_cal`、`daily`、`adj_factor`、`daily_basic`；财务适配器按所选字段请求 `fina_indicator` 的 21 项指标，公告后首个官方交易日才可进入特征。

PCD 字段目录可完整发现，但本次真实覆盖仅观察到 Apple 的三个财务事实，没有已映射的 A 股事实面板。文本、列表和嵌套记录可浏览，不能直接变成数值特征。证券池保存真实成员、快照日期与哈希；当前成员不等于历史成员，推荐子集只是代码升序前 20 只。[来源验收](docs/DATA_SOURCES.md)

托管 Tushare 使用运营方配置的已授权接入，部署通过 `TUSHARE_PUBLIC_AUTHORIZED=true` 开启；是否已启用见 health 的 `capabilities.tushareHosted`。部署者需自行确认其服务与使用范围获得适用授权。开源代码许可不授予第三方数据再分发权，仓库不附带私有行情。[导入与数据边界](docs/DATA.md)

## 本地复现

需要 Python 3.12 和 uv。源码根目录运行：

```sh
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r engine/requirements.lock.txt
uv pip check --python .venv/bin/python
.venv/bin/python scripts/local-run.py engine/examples/research-v02.json --source demo --output private/demo-report.json
```

该样例使用固定 seed 的 **SYNTHETIC 教学价格**，比较全部 8 类模型。锁定依赖在干净 Python 3.12.13 环境验证。输出为 `{"result": ...}`；失败为 `{"error": {"code": ..., "message": ...}}` 且退出码非零。`private/` 已被 Git 忽略。

自有数据使用 `{rows,provenance}` JSON，股票池与日期应匹配策略：

```sh
.venv/bin/python scripts/local-run.py engine/examples/research-v02.json --source upload --dataset private/market.json --output private/upload-report.json
```

本地个人 Tushare 可通过环境变量输入 token。以下用交互式隐藏输入，避免把凭据写入命令历史：

```sh
read -r -s TUSHARE_TOKEN
export TUSHARE_TOKEN
.venv/bin/python scripts/local-run.py engine/examples/research-v02.json --source tushare --output private/tushare-report.json
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

| 项目 | v0.2 范围 |
|---|---|
| 研究市场 | 每次 3–50 只沪深 A 股；日频、只做多 |
| 因子 | 每次 1–32 个；单窗口 1–252 日，总回看不超过 504 日 |
| 历史 | 最多 8 年、2,200 个交易日、110,000 行 |
| 有效样本 | 因子预热与预测期后至少 180 个研究交易日，并需足够截面观测 |
| 预测 / 调仓 | 预测 1–20 日，调仓间隔 1–60 日 |
| 上传 / 报告 | 上传 100–110,000 行，最大 24 MiB；完整报告最大 24 MiB |
| 工作区 | 同时 1 个实验，每天 20 次提交，最多 200 个策略、100 个代码项目 |
| 代码 / AI | 代码最多 40,000 字符；AI 每工作区每天 20 次、全服务每天 500 次 |
| 社区发布 | 每工作区每天最多 10 次；分页目录包括最近最多 1,000 个公开社区条目 |

额度与具体参数以当前 API 校验为准。原始上传在成功或取消后删除；其他终态任务的残留上传创建超过 30 天后按定时批次清理。策略、代码、报告和审计记录单独保留。[保留说明](docs/DATA.md)

## 验证证据

```sh
PYTHONPATH=engine .venv/bin/python -m pytest -q engine/tests
npm test
```

[v0.2 数值验收](docs/engine-v0.2-validation.json) 记录 50 只股票、8 年、32 因子、14 配置的合成压力实验：本次机器约 288.63 秒，完整报告约 16.14 MB，现金、费用与持仓独立核对通过。该样例用于资源与记账验收，不证明收益效果或线上时延。

[真实来源验收](data/source-validation.json) 单独记录私有 provider 请求、字段覆盖和三只 A 股、2,001 行行情、公告财务指标、14 配置的完成实验；不公开原始行情和完整私有报告。[v0.1 数值记录](docs/technical-validation.json)、[v0.1 Tushare 记录](docs/tushare-validation.json)、[v0.1 发布快照](docs/release-validation.json) 保留为历史证据；其中旧测试计数与当时关闭的托管能力不用于推断 v0.2 部署状态。

托管部署由浏览器、Cloudflare Worker + D1/R2、出站轮询的 Python runner 组成；配置、AI binding、恢复与单主机可用性见 [OPERATIONS.md](docs/OPERATIONS.md)。
