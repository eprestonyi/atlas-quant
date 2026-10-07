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

1. 保持 `docs/CONTRACT.md` 中的数据结构与前后端一致；发生契约变化时同步修改消费者。
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

## 安全问题

敏感问题按 [SECURITY.md](SECURITY.md) 报告。不要在公开 Issue 中放置令牌、用户数据或可直接攻击线上系统的细节。
