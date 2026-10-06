# Atlas Quant

Atlas Quant 是嵌入 Atlas 的开源日频量化研究工作台：拖动模块和因子，比较有限的统计/机器学习候选，保留可复现的实验与交易账本，并分享因子定义。

代码采用 **Apache-2.0**，见 [LICENSE](LICENSE)。行情及社区贡献因子的许可独立于项目代码许可。

- 产品地址：[atlas-aletheia.com/quant/](https://atlas-aletheia.com/quant/)
- 开源仓库：[eprestonyi/atlas-quant](https://github.com/eprestonyi/atlas-quant)
- 社区讨论：[Discussions](https://github.com/eprestonyi/atlas-quant/discussions) · [问题与建议](https://github.com/eprestonyi/atlas-quant/issues)

当前为已上线的公开测试版。具体研究、数据与运行边界如下。

## 当前能力

六个固定类型的阶段组成研究流程：股票池 → 因子 → 预处理 → 模型 → 组合 → 回测。画布支持移动、拖入因子和合法连接；运行前再次校验配置。

- 24 个内置量价因子；社区可提交白名单 DSL 表达式，标明署名、来源、许可和 Fork 关系。
- 因子基线、Ridge、ElasticNet、Histogram Gradient Boosting；全选时共 7 组有限参数配置。
- 按唯一交易日进行嵌套 walk-forward；清除跨窗标签；最后约 20% 的研究日期保留为独立 holdout。
- 终端留出期的净值、基准、RankIC、费用、逐笔交易、每日资金与持仓账本；策略和报告 JSON 导出。
- 明确选择合成教学数据、自有 CSV/JSON 或本地个人 Tushare 适配器。数据失败不会自动切换为合成数据。

当前使用 **Atlas 自有的复权单位日频研究引擎和 scikit-learn**；没有集成 LEAN 或 Backtrader 执行引擎。系统没有真实券商下单能力，也没有持续运行的实盘/纸面账户服务。

“优选模型”只表示预设候选在指定验证窗口中的得分最高，不保证全局最优、预测有效或盈利。报告中的 `NO_VALIDATED_EDGE` 应保留；正的窗口平均 RankIC 也不等于统计显著性。[方法说明](docs/METHODOLOGY.md)

## 本地运行

需要 Python 3.12 和 uv。在源码根目录运行；下列安装与样例已在干净的 Python 3.12.13 环境验证。

```sh
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r engine/requirements.lock.txt
uv pip check --python .venv/bin/python
.venv/bin/python scripts/local-run.py engine/examples/basic.json --source demo --output private/demo-report.json
```

`demo` 使用确定性 **SYNTHETIC 教学价格**，不含真实行情。输出为 `{"result": ...}`；失败为 `{"error": {"code": ..., "message": ...}}` 并返回非零退出码。`private/` 已被 Git 忽略。

使用自有数据：

```sh
.venv/bin/python scripts/local-run.py engine/examples/basic.json --source upload --dataset private/market.json --output private/upload-report.json
```

`market.json` 必须包含 `rows`，可附 `provenance`；先使策略股票池/日期与数据一致。导入字段、复权和单位见 [DATA.md](docs/DATA.md)。

个人本地 Tushare：在终端私密输入自己的 token，避免把它写进命令历史、策略、报告或仓库。

```sh
read -r -s TUSHARE_TOKEN
export TUSHARE_TOKEN
.venv/bin/python scripts/local-run.py engine/examples/basic.json --source tushare --output private/tushare-report.json
unset TUSHARE_TOKEN
```

本地适配器通过官方 HTTPS API 获取 `trade_cal`、`daily`、`adj_factor`，仅在所选因子需要时获取 `daily_basic`。权限、限流、缺失复权或不完整日历会明确报错。可调用个人 token 不等于拥有向多用户供数的权利；**公共托管 Tushare 默认关闭**，待明确数据授权后才能开放。[数据边界](docs/DATA.md)

## 浏览器工作区

保存的策略、实验和上传数据按服务端工作区隔离。工作区由当前浏览器的 HttpOnly cookie 识别；目前没有 Quant 记录与 Atlas 账号的统一同步或恢复功能。清除网站数据、换浏览器或无痕窗口可能得到新的工作区；请先导出需要保留的策略与报告。

页面可尝试一次性导入同浏览器现有 Atlas 自选标的。这不会把 Quant 的策略、运行记录或工作区凭据同步到 Atlas 账号。

社区因子定义发布后对公众可见，并标记为 `community_unreviewed`。语法通过不代表收益、版权、来源或研究质量经过人工审核。因子发布不会附带原始行情、私有策略或实验结果。[贡献说明](CONTRIBUTING.md)

服务端原始上传对象在任务成功或取消后删除；其他已终止任务的原始上传对象在创建超过 30 天后进入定时清理范围。清理依赖调度正常执行，按批处理；不是精确到时的硬删除保证。策略、运行报告和审计记录不适用这条原始上传清理规则。详情见 [DATA.md](docs/DATA.md)。

## v0.1 范围

| 项目 | 范围 |
|---|---|
| 公共界面市场 | 3–20 只沪深 A 股；日频、只做多 |
| 因子 | 每次 1–12 个；单窗口 1–252 日，组合回看不超过 504 日 |
| 历史区间 | 每次最多 8 年；引擎最多 2,200 个交易日 |
| 有效样本 | 完整因子预热与预测期后至少 180 个研究交易日，并需足够截面观测 |
| 预测 / 调仓 | 预测期 1–20 日；调仓间隔 1–60 日 |
| 浏览器上传 | 100–45,000 行，8 MB 文件上限；报告最大 12 MiB |
| 公共工作区配额 | 同时 1 个实验、每天最多 20 次提交、最多 200 个保存策略 |
| 社区贡献 | 每工作区每天最多 10 次；公共列表最多返回最近 300 个已发布条目 |

费用、初始资金和权重还有输入边界，以当前界面与校验返回为准。低于权重合计 100% 时可以保留现金。

## 验证与开发

```sh
PYTHONPATH=engine .venv/bin/python -m pytest -q engine/tests
npm ci
npm test
npm run check
npm run build
node scripts/dev.mjs
```

构建验证环境使用 Node.js 26.7.0。预览地址为 `http://127.0.0.1:8895/quant/`。另开终端启动本地计算 runner，即可在预览页面执行合成/上传实验：

```sh
.venv/bin/python scripts/dev-runner.py
```

开发服务使用公开的开发密钥与 loopback HTTP，仅供本机使用；不要暴露到公网。Miniflare 开发工作区随预览进程结束而丢失。

托管版由 `web/` 浏览器界面、`edge/` Worker + D1/R2、出站轮询的 Python runner 组成。runner 的私有服务配置与凭据放在仓库外；示例开发凭据不适用于部署。部署、维护及单主机可用性边界见 [OPERATIONS.md](docs/OPERATIONS.md)。

[technical-validation.json](docs/technical-validation.json) 记录早期本地验收：当时干净环境的 73 项 Python 测试通过；样例两次完整结果 JSON 完全相等；20 只股票、8 年、12 因子、每日调仓的合成压力样例在本次机器上约 5.51 秒，完整报告约 3.85 MB。这份证据只覆盖该次本地数值验收。

[tushare-validation.json](docs/tushare-validation.json) 单独记录生产队列中的私有运营者 Tushare 实验：5 只股票、4,540 行、908 个官方交易日、11 次数据请求，完成 127 笔研究成交和 183 个留出期净值点。公开证据不含行情行、凭据或完整私有报告；这次成功不改变公共 Tushare 关闭状态，也不证明策略可盈利或服务持续可用。

[研究方法](docs/METHODOLOGY.md) · [数据规范](docs/DATA.md) · [贡献指南](CONTRIBUTING.md) · [安全说明](SECURITY.md)
