# 数据来源与导入规范

Atlas Quant 区分合成教学、用户上传和 provider 数据。来源标签描述获取途径，不代表策略有效，也不自动授予公开分发数据的权利。

| 来源 | 结果标识 | 当前入口 |
|---|---|---|
| 确定性合成数据 | `SYNTHETIC_EDUCATIONAL_ONLY` | 浏览器教学模式、本地 `--source demo` |
| 用户上传 | `USER_PROVIDED_UNVERIFIED` | 浏览器 CSV/JSON、本地 `--source upload` |
| Tushare Pro | `PROVIDER_DATA` / `TUSHARE_PRO` | 个人本地 `--source tushare`；运营方配置授权后的托管入口 |

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

日历完整性、股票身份、日期范围、重复记录、OHLC 和复权完整性均被校验。停牌/缺失数据不填充。公开文档的接口积分和频次可能变化；实际积分、频次和访问权限以当前官方文档、账号与接口返回为准。限流与权限错误明确返回，系统不购买权限，也不绕过限制。[官方权限频次表](https://tushare.pro/document/2?doc_id=290)

`stock_basic` 的当前上市列表和目录中的指数/行业成员快照不是历史股票池。目录保留完整成员、来源日期与快照哈希；推荐计算子集只是代码升序前 20 只，不代表收益排名。研究由用户明确选择子集，当前成员不证明历史成员资格，也不消除存活或事后选择偏差。

## 财务与跨库字段

Financial DB 包含 PCD、MKT、EXT、MODEL 四个逻辑库。Tushare 日行情归 MKT，供应商财务指标归 EXT；`fd_` 命名兼容旧适配器，不是第五库，也不表示已核对公司原始披露。[四库架构](ARCHITECTURE.md)

财务来源为官方 [fina_indicator](https://tushare.pro/document/2?doc_id=79)。当前按所选因子请求 21 个指标：`eps,bps,ocfps,roe,roa,roic,grossprofit_margin,netprofit_margin,debt_to_assets,current_ratio,quick_ratio,assets_turn,inv_turn,ar_turn,or_yoy,netprofit_yoy,ocf_to_or,fcff,fcfe,ebit,ebitda`，表达式中加 `fd_` 前缀。

财报按公告日后的首个官方交易日进入特征；报告期末不是可用日。适配器按年度有界取数并读取前两年的历史用于初始可用值，不把季度/累计数据自动当作 TTM。同证券、公告日、报告期的冲突字段隔离为 null 并保留冲突摘要；较旧报告期的迟到更新不能覆盖较新的报告期。原始披露版本是否完整由供应商历史覆盖决定，当前未独立核验。

`income`、`balancesheet` 和 `cashflow` 的只读访问已单独验证，但其所有字段尚未自动形成时点因子。当前可运行财务配方来自明确登记的 `fina_indicator` 字段。

外部上传数值列使用 `pcd_`、`fd_`、`ext_` 或 `model_` 加 1–60 个小写字母、数字或下划线。每行非空值须有对应的 `<alias>__available_date`（YYYYMMDD），并在 provenance 显式登记：

```json
{
  "externalFields": {
    "ext_example": {
      "source": "MY_AUTHORIZED_VENDOR",
      "path": "dataset.metric",
      "dataType": "decimal",
      "availabilityPolicy": "point_in_time_asof",
      "availableDateColumn": "ext_example__available_date"
    }
  }
}
```

这是 provenance 的局部示例，不含实际数值。允许的数值类型为 `number`、`decimal`、`integer`。字段缺失、未来可知日期、无效日期、字符串、布尔值和非有限数会被拒绝；不会将文本字段自动编码成有效因子。`ext_`/`model_` 上传支持不代表外部历史数据库已经接通。MODEL 的历史输出同样只能在其当时真正生成后使用，不能把事后重算值回填成历史预测。

PCD 目录含 17,073 个字段，其中 4,870 个为 decimal/integer；可检索不等于有观测。当前生产只观察到 Apple 三个事实，没有 A 股已映射事实，不能把它们绑定到其他证券。PCD 连接以 `strategy.dataBindings.pcd` 精确声明字段、单位、证券—主体—记录：

```json
{
  "pcd_example": {
    "fieldId": "REPLACE_WITH_REAL_FIELD_ID",
    "unitCode": "REPLACE_WITH_REAL_UNIT",
    "records": [{"ts_code": "600000.SH", "entityId": "REAL_MATCHING_ENTITY", "recordId": "REAL_RECORD"}]
  }
}
```

以上仅为结构示例，不能直接运行。真实连接器检查身份、字段、单位、期间和版本；相同期间的多口径须显式选择或先聚合。可知时间取原文发布时间、取得时间、观察值时间、选值时间和记录创建时间的保守最大值，再移至下一个交易日。UNKNOWN 发布时间不会按报告期回填。原始精确十进制值留在 PCD，研究矩阵转换为 float64。

声明可知日期不等于独立核验了来源；上传者提供的时间与授权属于声明。来源覆盖和真实连接证据见 [DATA_SOURCES.md](DATA_SOURCES.md)。

## 授权与公开使用

代码开源许可不包括行情分发许可。Tushare [服务协议](https://tushare.pro/document/1?doc_id=405) 对个人使用、账号转让和商业使用有具体约束；[官方价格表](https://tushare.pro/document/2?doc_id=290) 区分个人与机构服务。因此，已有 token 可成功调用，不足以证明可以向 Atlas 所有用户提供共享数据服务。

托管 Tushare 在运营方配置授权后通过 `TUSHARE_PUBLIC_AUTHORIZED=true` 开启，当前状态由 `/quant/api/health` 的 `capabilities.tushareHosted` 返回。已有运营者授权接入配置；此部署事实与适用于其他部署者的第三方数据条款分开。私有代理和个人本地运行不改变数据许可。使用者应只上传和处理有权使用的数据，也不要把有再分发限制的原始数据提交到开源仓库。

早期真实 Tushare 到生产研究队列的脱敏摘要见 [tushare-validation.json](tushare-validation.json)。v0.2 的 [source-validation.json](../data/source-validation.json) 单独记录三只 A 股、2,001 行、公告时点财务指标和 14 个候选的完成实验。这些证据只证明各次取数与计算成功；原始行情与完整私有报告没有随开源代码发布。

使用 Tushare 数据的产品应标明“数据来源：Tushare数据”。[官方 FAQ](https://tushare.pro/document/1?doc_id=122)

## 保留与隐私

浏览器上传对象先按工作区/任务存入私有存储。成功完成或成功取消任务后删除服务端原始上传对象。失败等已终止任务的残留上传对象在任务创建超过 30 天后进入定时清理范围；每次最多清理 100 个，要求运营方启用并保持调度正常。

排队/运行任务不在该定时清理查询中。因此，“30天”是残留终态任务的清理阈值，不是覆盖所有状态的硬性最长保留保证。

策略、代码项目、实验元信息、审计记录和运行报告单独保留，不适用上述原始上传删除规则。报告包含衍生价格、成交和持仓；当前没有一键删除整个工作区或恢复丢失 cookie 的公共流程。发布因子只公开定义和贡献元信息，不附带这些私有数据。

v0.4 的成功预测研究另外保存**私有冻结研究输入**，包括计算时实际使用的完整数值、来源与交易日历，以便之后不重新取数地复用预测进行执行。它与临时上传对象是不同对象，不会随成功任务的原始上传清理而删除；它和完整预测产物按研究记录保留。仅受信 runner 能在有效执行租约下取得原始冻结输入；普通报告和预测下载不包含它。失败/取消任务的未引用快照进入超过 30 天的定时清理范围。产物的工作区所有权、预测内容哈希、冻结数据哈希及读取时的对象 SHA-256 均被检查。

来源 `dataFingerprint` 保留供应商规范化记录的指纹；预测产物的 `dataFingerprint` 是所选完整精度数值加官方日历的引擎指纹，两者有意区分。冻结对象同时保留 `sourceDataFingerprint` 和研究指纹，不能用新一轮供应商返回值代替旧输入。执行复用报告仍保留原来源的采集证据，不将复用时间说成新的取数时间。

本地 CLI 输出由本地使用者管理。可选 provider 缓存用工作区/凭据指纹隔离，使用 0600 JSON 文件，最多每个缓存身份 32 个文件；1 小时是缓存复用有效期，不代表文件会在 1 小时后自动删除。缓存可能含有受数据许可限制的行情，勿上传公开目录。
