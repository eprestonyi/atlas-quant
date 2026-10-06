# 数据来源与导入规范

Atlas Quant 区分合成教学、用户上传和 provider 数据。来源标签描述获取途径，不代表策略有效，也不自动授予公开分发数据的权利。

| 来源 | 结果标识 | 当前入口 |
|---|---|---|
| 确定性合成数据 | `SYNTHETIC_EDUCATIONAL_ONLY` | 浏览器教学模式、本地 `--source demo` |
| 用户上传 | `USER_PROVIDED_UNVERIFIED` | 浏览器 CSV/JSON、本地 `--source upload` |
| Tushare Pro | `PROVIDER_DATA` / `TUSHARE_PRO` | 个人本地 `--source tushare`；公共托管默认关闭 |

合成价格和日历用于测试流程。合成日历只去除周末，不模拟中国法定节假日、停牌或真实涨跌停。provider 报错时不会退回合成数据。

若上传的 provenance 声明为合成数据，结果保留 `SYNTHETIC_USER_UPLOAD_UNVERIFIED` 标识，不会因为经过上传入口而变成真实行情。

## CSV 与 JSON

浏览器支持 CSV、JSON 行数组，或 `{rows,provenance}` JSON。本地 CLI 的 `--dataset` 文件使用 `{rows,provenance}` 对象。

```csv
ts_code,trade_date,open,high,low,close,vol,amount,raw_close,adj_factor
600519.SH,20230103,100,102,99,101,10000,101000,101,1
```

上面只示范字段与单位，数值为人工示意，不能作为真实行情；一行也不足以运行研究。

| 字段 | 要求与单位 |
|---|---|
| `ts_code` | 浏览器为 `000001.SZ` / `600000.SH` 等沪深 A 股；匹配策略股票池 |
| `trade_date` | `YYYYMMDD`；每个股票/日期最多一行 |
| `open/high/low/close` | 一致口径的有限正价格，高低价关系有效 |
| `vol` | 有限非负数，**手**；1 手按 100 股换算 |
| `amount` | 有限非负数，**千元**；浏览器 CSV/JSON 必须提供 |
| `raw_close` | 原始未复权收盘价；使用复权字段时每行都需提供 |
| `adj_factor` | 正复权因子；必须与 raw_close 一起提供，不会凭此自动重算上传 OHLC |

若上传同时省略 `raw_close` 与 `adj_factor`，适配器明确按 `raw_close=close`、`adj_factor=1` 处理并提示公司行动风险。这是未复权假设，不是已验证的复权数据。

若提供复权字段，**上传者必须已经将所有 OHLC 转成一致的研究价格口径**。适配器不会依据上传的 adj_factor 再次乘除价格。仅上传原价和一个因子却把它称为复权数据是不正确的。

可选日度字段包括 `turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv`。比例、估值和股本字段应遵循原提供方单位；Tushare 的换手/股息率为百分数、股本为万股、市值为万元。缺少因子所需字段会失败，空值不会自动成为零。

推荐 JSON 附带：

```json
{
  "rows": [],
  "provenance": {
    "source": "我的已授权数据来源",
    "license": "适用的数据许可说明",
    "asOf": "2026-09-30",
    "tradingDates": ["20260928", "20260929", "20260930"]
  }
}
```

示例 rows 故意为空，需替换为完整数据。`tradingDates` 应为区间内完整、唯一、升序的交易日列表，覆盖所有观测日期。未提供时使用上传日期并集，并提示无法发现所有股票共同缺失的交易日。声明的来源/许可保存为用户声明，不变成独立核验事实。

底层 API/本地适配器另支持显式 `amountDerivation: "vol*close*100/1000"`，在缺少成交额且 `raw_close==close` 时生成估算成交额。该值标记为 `APPROXIMATION_NOT_OBSERVED`，不是实际成交额；浏览器导入表单当前要求直接提供 amount。不要用复权 close 估算原始成交额。

## Tushare 适配器

官方接口：[daily](https://tushare.pro/document/2?doc_id=27)、[adj_factor](https://tushare.pro/document/2?doc_id=28)、[trade_cal](https://tushare.pro/document/2?doc_id=26)、[daily_basic](https://tushare.pro/document/2?doc_id=32)。

适配器先取得 SSE 完整交易日历，再获取所选股票的 daily 与 adj_factor；所选因子引用日度基本面字段时额外获取 daily_basic。它保留 `raw_close`，并逐股票使用：

```text
research_OHLC = raw_OHLC * adj_factor / first_observed_adj_factor
```

这使价格成为以本次区间首个观测因子为基准的复权研究单位。成交量和成交额保留提供方原单位。不同取数起点会改变归一化单位，不能直接把研究数量当作券商股数。

日历完整性、股票身份、日期范围、重复记录、OHLC 和复权完整性均被校验。停牌/缺失数据不填充。公开文档的接口积分和频次可能变化；目前 daily_basic、adj_factor、trade_cal 通常需要至少 2000 积分，实际权限以当前账号和接口返回为准。限流与权限错误明确返回，系统不购买权限，也不绕过限制。[官方权限频次表](https://tushare.pro/document/2?doc_id=290)

`stock_basic` 的当前上市列表不是历史股票池。本版研究由用户指定股票池，没有依赖当前列表宣称消除了存活偏差，也没有历史指数成分服务。

## 授权与公开使用

代码开源许可不包括行情分发许可。Tushare [服务协议](https://tushare.pro/document/1?doc_id=405) 对个人使用、账号转让和商业使用有具体约束；[官方价格表](https://tushare.pro/document/2?doc_id=290) 区分个人与机构服务。因此，已有 token 可成功调用，不足以证明可以向 Atlas 所有用户提供共享数据服务。

公共托管 Tushare 默认关闭；只有运营方已取得适用的明确授权后，才能考虑开启公共能力。私有代理和个人本地运行不是对数据许可的绕过。使用者应只上传和处理有权使用的数据，也不要把有再分发限制的原始数据提交到开源仓库。

私有运营者已完成一次真实 Tushare 到生产研究队列的技术验证，脱敏摘要见 [tushare-validation.json](tushare-validation.json)。这证明该次请求与计算成功，不授予公共供数权利；原始行情与完整私有报告没有随开源代码发布。

使用 Tushare 数据的产品应标明“数据来源：Tushare数据”。[官方 FAQ](https://tushare.pro/document/1?doc_id=122)

## 保留与隐私

浏览器上传对象先按工作区/任务存入私有存储。成功完成或成功取消任务后删除服务端原始上传对象。失败等已终止任务的残留上传对象在任务创建超过 30 天后进入定时清理范围；每次最多清理 100 个，要求运营方启用并保持调度正常。

排队/运行任务不在该定时清理查询中。因此，“30天”是残留终态任务的清理阈值，不是覆盖所有状态的硬性最长保留保证。

策略、实验元信息、审计记录和运行报告单独保留，不适用上述原始上传删除规则。报告包含衍生价格、成交和持仓；当前没有一键删除整个工作区或恢复丢失 cookie 的公共流程。发布因子只公开定义和贡献元信息，不附带这些私有数据。

本地 CLI 输出由本地使用者管理。可选 provider 缓存用工作区/凭据指纹隔离，使用 0600 JSON 文件，最多每个缓存身份 32 个文件；1 小时是缓存复用有效期，不代表文件会在 1 小时后自动删除。缓存可能含有受数据许可限制的行情，勿上传公开目录。
