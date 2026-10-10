# 贡献 Atlas Quant

欢迎改进因子定义、研究方法、错误案例、文档、数据适配器和界面。项目代码采用 [Apache-2.0](LICENSE)。提交前确认自己有权公开相关内容，并保留第三方必要的版权、来源和许可信息。

开源仓库为 [eprestonyi/atlas-quant](https://github.com/eprestonyi/atlas-quant)。研究思路与功能讨论请使用 [Discussions](https://github.com/eprestonyi/atlas-quant/discussions)，可复现的问题请使用 [Issues](https://github.com/eprestonyi/atlas-quant/issues)，代码改动通过 PR 提交。

## 因子贡献

在社区界面中填写名称、表达式、方向、说明、分类和作者，选择 Apache-2.0、MIT、BSD-3-Clause 或 CC0-1.0；来源链接可选，若基于他人工作应明确标注。使用 Fork 保留 `forkOf` 关系，改进版本发布为新条目。

社区条目通过语法校验后立即发布为 **community_unreviewed**。当前没有“人工审核通过”或“验证获利”的承诺；维护者也没有替作者核验身份或原始授权。作者可以在原浏览器工作区撤回自己的条目，撤回不会改写别人已导出的策略或已保存实验。

好的贡献至少说明：

- 因子衡量什么、输入字段及其可用时间、方向和窗口。
- 缺失值、零分母、公司行动、停牌及适用股票池的处理。
- 如果提供研究结果，说明数据来源、日期、费用、全部尝试次数、模型选择区间和独立留出区间。
- 已知失败场景和参考出处；合成结果必须明确标识为合成。

分享公式与研究说明即可。不要上传 token、账户信息、私有行情缓存、无权分发的历史数据、受保密约束的策略或收益截图中的个人资料。

## DSL 示例

```text
returns(close,20)
ts_std(returns(close,1),20)
close/lag(ts_max(high,20),1)-1
ts_mean(abs(returns(close,1))/max(amount,1),20)
```

支持因果滞后、滚动与当日横截面函数；不执行任意 Python，不支持负滞后、未来窗口、导入、网络、文件、属性访问或下标。完整函数和字段见 [METHODOLOGY.md](docs/METHODOLOGY.md)。用户 Python 可在浏览器代码模式单独执行，但社区服务端因子仍使用此 DSL；保存代码或 AI 审查不等于注册可在服务器执行的插件。

## 字段目录与配方

397 个内置配方由 `engine/atlas_quant/research_registry.py` 登记，并保存在 `catalog.json`。修改时同时检查表达式、依赖字段、回看、单位、方向、可用时间与数据来源，运行数值和 edge 的目录一致性测试。一个字段的多个窗口是多个假设，不应宣传为独立验证的 alpha。

PCD 的 17,073 个字段是结构目录，不能通过改 `availability` 标记伪装成有数据。贡献新连接器时说明实际证券/主体、期间与观测覆盖，提供保守可知时间和版本证据。非数值字段需要单独设计有文档的转换方法；不能用任意编码或补零绕过数值要求。外部和 MODEL 输出也应保留 source/path，MODEL 还需要独立审查历史训练数据与当时生成时间。

## 代码 PR

从一个明确问题开始，描述复现条件和改变后的行为。较大的接口或方法变动先讨论；小修复可以直接提交。维护者 review 是合并代码的过程，不等于当前社区条目发布机制。

1. 保持 `docs/ASSET_RETURN_CONTRACT.md`、`docs/STATISTICAL_QUANT_SCHEMA.md` 和 `docs/STATISTICAL_QUANT_API.md` 中的版本化协议与前后端一致；`docs/CONTRACT.md` 只描述历史版本。发生契约变化时同步修改消费者及共享 fixture。
2. 对数学、记账、权限、隔离和数据完整性变动加入有意义的反例测试。测试应能捕获未来泄漏、错误成交或越权访问等实际失败。
3. 记录运行的检查和未验证部分。保持真实 provider、本地验证、托管执行和前向绩效之间的区别。

```sh
PYTHONPATH=engine .venv/bin/python -m pytest -q engine/tests
npm test
npm run check
npm run build
```

数值方法变动应保留固定 seed 与输入指纹，说明是否改变既有结果；更新版本/验收证据。不要为制造稳定测试而删除费用、未来数据检查或失败状态。

默认不接受把同一 holdout 反复用于因子选择后仍称为独立验证的结果。新的研究尝试要留下记录，或者使用新的未查看区间/前向数据。

## 新模型与模块贡献

社区 DSL 定义的是 X 的可审计变换；新的服务端估计器、目标或执行算法通过代码 PR 扩展，不在用户提交时执行任意 Python。

- 新收益 F/4 说明响应单位、期限与信息时点，只输出一个响应。集合共享因子定义，每个资产独立拟合变换和系数。简单收益与起点已知波动率标准化须分别可复现。历史价格 F/1–3 才保留双输出；不要以占位值拼造兼容。
- 新收益目标区分已知因子预测后续收益、同期关联及未来因子情景；输入与响应的区间端点必须匹配，不能用自身同期收益恒等式制造拟合度。新的篮子目标属于独立版本路径，需另述冻结数量与复制关系。
- 新因子说明观察与交易频率、转换和可知时间。用相同目标、有效样本与候选预算比较有无该因子；模型无法拟合的差异也要显示。
- 新执行模块只能消费保存的预测和输入快照；交易必须引用预测 ID。测试 T+1、无法成交、费用、头寸与现金守恒。调整执行门槛不得悄悄改模型或重新取数。

注册模块时同步更新版本化目录和界面可用状态，列出输入/输出及兼容条件。配方是可复用配置，不因加入目录而获得“精选获利策略”身份。模型序列化、新的市场或衍生品单位等改变需先扩展协议，不能硬塞入现有价格字段。

新收益实现至少验证完整面板、每资产参数隔离、训练期清洗、未来扰动不改变过去拟合，以及 Python/浏览器 F/4 数值一致；执行不得被顺带开放。协议见 [ASSET_RETURN_CONTRACT.md](docs/ASSET_RETURN_CONTRACT.md)。

以下是历史双价格/执行模块的验收，不能对新收益产物运行：

```sh
.venv/bin/python scripts/local-run.py engine/examples/statistical-quant.json --source demo --output private/forecast.json --snapshot-output private/input.json
.venv/bin/python scripts/replay-execution.py private/forecast.json private/input.json --output private/execution.json
.venv/bin/python scripts/audit-report.py private/execution.json --source-report private/forecast.json
```

## 安全问题

敏感问题按 [SECURITY.md](SECURITY.md) 报告。不要在公开 Issue 中放置令牌、用户数据或可直接攻击线上系统的细节。
