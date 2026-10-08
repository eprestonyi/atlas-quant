# 独立 F 函数：当前时间口径

`atlas-model-function/1` 是可计算的纯 JSON 函数。数值输入、变换、双输出和派生规则见 [函数合同](../contracts/factor-research-model-v1.json)；这些说明不增加函数字段，也不修改已保存文件或内容 hash。

观察时点是官方交易日 `t` 收盘后，当前 `P` 与 `scale` 来自该时点已知状态。当前 schema 1 的两个输出按同一 `P` 和 `scale` 还原：

| 输出 | 还原后的预测值 | 目标时点 |
| --- | --- | --- |
| `output[0]` | `expectedEntry = P + scale × output[0]` | 下一官方交易日 `t+1` 开盘 |
| `output[1]` | `V = P + scale × output[1]` | 该开盘之后 h 个交易日，即 `t+1+h` 开盘 |

因此 h=1 对应第二个后续交易日开盘，不是下一日收盘。交易日以冻结官方日历为准，不能按自然日加减。该口径保留兼容的双输出，即使研究关闭交易执行也不改变标签时间。

`research.observationDays` 默认 1 表示每天观察一次；`target.horizonSessions` 默认 5，控制上述未来目标，二者独立。函数的训练截止不是每个试算输入的观察日期。原报告的 `date`、`informationCutoff`、`entryDate` 和 `targetDate` 保存具体时点；独立数值求值没有日历上下文，不会补造这些日期，也不构成实时预测或重新验证。

编辑仍只产生带 `UNVALIDATED_USER_EDIT` 的新函数，不继承原模型统计检验。时间定义的新设计见 [下一轮版本化建议](FACTOR_RESEARCH_REFRAME.md#下一轮时间目标版本化建议)。
