# Atlas Quant API and legacy compatibility

同源 API 根路径为 `/quant/api`。JSON 错误格式为 `{"error":{"code":"...","message":"..."}}`。以下契约面向当前源码；部署能力以 `GET /health` 为准。浏览器写请求使用 `Content-Type: application/json`，不允许跨站请求；private 端点需要先通过 `GET /session` 建立 HttpOnly 工作区 cookie。

## 新建收益研究

统计研究版本化入口使用 `/experiments`、`/runs/:id/report` 与可移植函数 API，详见 [STATISTICAL_QUANT_API.md](STATISTICAL_QUANT_API.md)。新默认协议是 [asset-return-study/1](ASSET_RETURN_CONTRACT.md)：共享因子定义、逐证券独立模型、简单收益或历史波动率标准化响应。`forecast`、同期 `association` 与明确的未来因子情景有不同信息边界。F/4 输出一个响应，不要求预期入场与未来价格两个值。

Runner 必须声明 `returnStudyFormats:["asset-return-study/1"]`；仅返回旧预测格式不能领取新任务。新报告通过 `studyProtocol` 与 observation/panel schema 分流，保留完整日期 × 资产面板与逐资产详情。新协议关闭交易执行，旧 `/executions` 回放不适用。实际开放能力以 health 和准入结果为准。

下文的 v0.3 `stat_arb` / v0.2 长仓配置及其旧 `/runs` 语义仅用于兼容；目录、工作区权限与代码审阅接口仍按各端点适用范围使用，不把残差交易模板冒充新因子研究。

## 发现目录

| 方法 / 路径 | 返回与用途 |
|---|---|
| `GET /health` | 构建版本、runner 状态与 `capabilities`，不含凭据 |
| `GET /session` | 当前工作区、runner、能力与源码链接；不存在时创建浏览器工作区 |
| `GET /catalog` | 兼容目录，含内置/社区因子、模型、模板、数据源与限制 |
| `GET /data-sources` | Financial DB 四库、适配器、字段计数和覆盖元信息 |
| `GET /fields` | 分页字段定义；`items,total,page,pageSize` |
| `GET /factor-catalog` | 分页内置、社区与数值字段派生定义；包含 availability、依赖字段和来源 |
| `GET /universes` | 分页成员池摘要；列表不返回全部成员 |
| `GET /universes/:id` | 完整成员、日期与快照哈希；历史推荐子集元数据不会自动成为运行成员 |
| `GET /universe-options` | 小体积分类选项、精选入口、目录版本；不加载全量成员 |
| `GET /research-presets` | `strategies` 为 3 个残差基准，`packs` 为 15 个因子包；`legacyStrategies` 与 `modelPresets` 仅用于历史长仓研究 |
| `GET /factors`、`GET /factors/:id` | 兼容因子列表与单个内置、社区或字段配方详情 |

字段和因子分页支持 `q,database,category,availability,page,pageSize`；`pageSize` 为 1–100，默认 30。可用性为 `all`、`ready`、`needs_mapping`、`unavailable`，`schema_only` 兼容 `needs_mapping`；`unavailable` 返回当前空目录；未知值返回 400。字段搜索将 `%`、`_` 作为文字匹配。股票池支持 `q,category,page,pageSize`。

`FD` 是财务供应商适配器的兼容筛选键，其 `logicalStore` 为 `EXT`；`PCD_DERIVED` 对应 `MODEL`。`COMMUNITY` 表示贡献来源，不是 Financial DB 的第五库。数值字段的原值、rank、zscore 配方可浏览，但仍需真实时点数据；非数值字段不生成这些配方。

`ready` 不代表股票覆盖、收益或人工审核通过。社区外部字段表达式可以仍是 `needs_mapping`。因子目录包括最近最多 1,000 个公开社区条目；兼容 `/factors` 列表有更小上限，使用分页目录发现扩展条目。

## 私有策略与实验

| 方法 / 路径 | 请求或行为 |
|---|---|
| `POST /universes/resolve` | `{selection}`；完整成员、逐步集合计数与哈希；请求最多 200,000 字节 |
| `GET /strategies` | 当前工作区的保存策略 |
| `POST /strategies` | `{strategy}`；创建版本 1；请求最多 200,000 字节 |
| `PUT /strategies/:id` | `{strategy,version}`；请求最多 200,000 字节；版本冲突为 409 |
| `DELETE /strategies/:id` | 删除自己保存的策略 |
| `GET /runs` | 当前工作区任务摘要 |
| `POST /runs` | `{strategy,dataSource,dataset?}`；返回 202 与 `{job}` |
| `GET /runs/:id` | 任务状态，完成后含 result |
| `GET /runs/:id/export` | 完整报告 JSON |
| `POST /runs/:id/cancel` | 取消自己的排队或运行任务 |

`dataSource` 必须显式为 `demo`、`upload` 或 `tushare`。上传的 `dataset` 为 `{rows,provenance}`，最大 24 MiB；请求包络最大 26 MiB。完整结果最大 24 MiB，超限明确失败，不静默截断预测或交易。每工作区同时 1 个实验、每天最多 20 次提交；全局队列最多 30 个。

默认研究流程分为 **股票池 → 基准与因子 → 观察与信号 → 交易与成本 → 检验与报告**。新策略例见 [stat-arb.json](../engine/examples/stat-arb.json)。`schemaVersion` 保持 1；新界面明确提交 `research.mode:'stat_arb'`，缺失 mode 的旧策略保持 `legacy_long_only`，不会被自动解释为多空研究。

- `universe.symbols`：本次明确参与计算的 3–50 个独立沪深 A 股代码；start/end 为 `YYYYMMDD`，最多 8 年。目录可包含北交所身份，但本轮托管策略执行仅接受 `.SH`、`.SZ`。
- `universe.selection`：version 1 的 includeGroups/excludeGroups/includeSymbols/excludeSymbols。组内 AND、组间 OR，先加入个股，再最终剔除。解析返回完整成员，超过 50 只不截断；继续过滤或明确选择子集后才能运行。
- `universe.resolutionHash,snapshotHash,subsetPolicy`：与 selection 一并保存，subsetPolicy 为 `all` 或 `explicit`。保存不等于成员核验；提交任务时重新解析。规则或目录哈希变化返回 409 `UNIVERSE_CHANGED`；集合外成员或伪装全量返回 400 `UNIVERSE_SUBSET_MISMATCH`。当前成员快照不构成历史成员证明。[完整集合契约](UNIVERSE_SELECTION.md)
- `factors`：残差模式允许 0–32 项 `{id,expression,direction}`。id 为 1–100 个 ASCII 字母、数字、下划线或连字符；使用受限因果 DSL，direction 为 ±1。它们作为形成截止日的**对冲暴露**，不预测单股收益，也不按方向决定个股排名。常数、缺失、线性依赖或占满残差自由度的暴露会被剔除并记录。
- `research.observationDays`：1–60 个交易日的观察采样间隔；独立于 `portfolio.rebalanceDays`（1–60）。`research.baseline` 可保留 `{id,name,version}` 来源，不表示已验证收益。
- `preprocess.decorrelation`：`none` 或 `drop_correlated`；correlationThreshold 为 0.5–1。残差路径按形成截止日的暴露截面处理相关性；`winsorize/standardize` 兼容字段属于旧预测路径，残差暴露按自身规则中心化、缩放。
- `portfolio`：initialCapital 为 10,000–1,000,000,000；rebalanceThresholdBps 为 0–10,000 的整篮权重变化带。残差模式不使用 TopN 选股，topN/maxWeight 兼容占位字段不决定篮子。
- `costs`：commissionBps 0–100、slippageBps 0–200、sellTaxBps/transferBps 0–100、minCommission 0–1,000。残差默认分别为 2.5、3、5、0.1、5；是固定可编辑情景，不代表实际券商报价或历史费用表。
- `graph`：可为空；非空仍使用兼容的六阶段合法图契约。五步界面是用户流程，图不能绕过配置检查。

`statArb` 参数：

| 字段 | 范围 / 默认 |
|---|---|
| method | `market_residual`、`pca_residual`（默认）、`factor_residual` |
| formationDays / residualWindow | 60–504 / 20–252，默认 126 / 60；残差窗口不超过形成窗口 |
| components | 1–10，PCA 不超过股票数减 2；默认 min(2, N−2) |
| refitDays | 1–126，默认 20；仅在实际观察日重新估计 |
| entryZ / exitZ / stopZ | 0.25–6 / 0–5.9 / 0.3–10，默认 2 / 0.5 / 4；exitZ < entryZ < stopZ |
| maxHoldingDays / maxHalfLife | 1–252 / 1–252，默认 20 / 60 |
| grossExposure | 0.1–2，默认 1；目标净权重为 0，实际漂移单独报告 |
| shorting / borrowAnnualBps | 仅 `theoretical`；借券年费情景 0–10,000 bps，默认 300 |

三个模板分别为篮子均值残差、PCA 残差、PCA 加风格暴露；`factor_residual` 是额外可选方法，至少需要一个因子。等权共同成分投影不等于股票 beta 中性；AR(1) 半衰期不是协整检验。

残差报告 `research.singleStockReturnForecast:false`、`predictions:null`，包含形成期载荷与误差、残差状态、对冲敞口、费用、完整交易/每日账本及附加因子的同条件基线比较。`statArb.signals.rows` 仅保留最近 5,000 个留出期事件，`totalRows,truncated` 明示范围；它不截断交易和每日账本。方法与阈值事先固定，最后约 30% 的可研究日期用于报告；`selection.qualified` 与 `deploymentQualified` 固定 false，`evidenceStatus:'UNVALIDATED_THEORETICAL_STAT_ARB'`。实际券源、融券条件、价格限制撮合未验证，不能据此宣称可实盘或盈利。[数值规则](STAT_ARB.md)

历史策略 [research-v02.json](../engine/examples/research-v02.json) 保留 `legacy_long_only` / `factor` 路径：1–32 因子，8 类模型的有限候选，`forward_return` / `forward_excess_return` 目标，horizon 1–20，metric 为 rank_ic；manual 模式只选一个模型。该路径才保留单股分数、收益目标预测、TopN 长仓和旧时间切分；这些输出不作为新残差篮子的预测或对冲模型。

PCD 的 `dataBindings.pcd` 最多 32 个别名。每个绑定含 `fieldId,unitCode,records`；records 为 1–50 项 `{ts_code,entityId,recordId}`，股票必须在本策略内。空对象、错误类型、重复记录或身份冲突会失败。绑定只是精确读取请求，不证明对应证券有事实；连接器还校验记录内容、单位、期间与时间。

数据来自上传时，外部数值特征还需每行可知日期及 provenance 映射，详见 [DATA.md](DATA.md)。token、代理 URL、服务凭据不得写入策略或 dataBindings。

## 代码与检查

| 方法 / 路径 | 请求或返回 |
|---|---|
| `POST /expressions/lint` | `{expression}` → `valid,diagnostics,fields,lookback,availability`；不执行研究 |
| `GET /code/projects` | 当前工作区最多 100 个代码项目 |
| `POST /code/projects` | `{name,language,code}`，language 为 `python` 或 `dsl` |
| `GET /code/projects/:id` | 当前工作区项目详情 |
| `PUT /code/projects/:id` | `{name,language,code,version}`；原子版本检查，冲突 409 |
| `DELETE /code/projects/:id` | 删除自己项目 |
| `POST /code/review` | `{language,code,mode,strategy?}` → `{review}` |

项目名最多 80 字符、代码最多 40,000 字符；原始空白保留。owner 由 cookie 推导，不能通过 JSON 指定另一所有者。别人的 id 读取或修改返回 404。

review 的 `mode:'manual'` 只做规则检查，`providerExecuted:false`。`mode:'ai'` 调用真实 Cloudflare Workers AI binding；未配置为 503 `AI_UNAVAILABLE`，provider 失败为 502 `AI_PROVIDER_FAILED`，不会返回凭据或底层错误。每工作区每天最多 20 次 AI 请求，全局每天 500 次。

成功响应含 provider/model、codeSha256、findings 和 patches。最多 30 项 findings、12 个 patches；`before` 必须是本次完整原代码的非空子串。用户确认时界面还检查匹配是否唯一，不自动应用，也不自动运行代码。模型 JSON 无法解析时可能返回纯文字摘要和空补丁；`providerExecuted:true` 只证明调用发生，不证明模型建议正确。

浏览器 Python 不存在公共服务端 `/execute` 端点。前端把代码和显式 rows 传给隔离 Pyodide worker；输入最多 110,000 行，默认执行预算 60 秒，组件允许 1–180 秒，当前界面选择 120 秒。stdout/stderr 分别保留末尾 50,000 字符，DataFrame 输出最多 1,000 行，序列化结果超过约 1 MB 返回截断提示。这些浏览器输出限制不截断托管引擎的完整报告。

## 社区与 runner

`POST /factors` 发布名称、描述、DSL、方向、分类、作者、许可、sourceUrl 及可选 forkOf。贡献立即标记 `community_unreviewed`；语法审核不替代数据授权或研究验证。`DELETE /factors/:id` 仅作者原工作区可用。每天最多 10 次发布。[贡献说明](../CONTRIBUTING.md)

runner 的 `POST /runner/claim`、`/runner/heartbeat`、`/runner/complete` 使用独立 bearer 服务凭据，不能用浏览器工作区冒充。领取返回租约与有限任务。新版 runner 先持久保存小写 UUID `requestId`；相同 ID 重试返回同一任务与租约，服务端回显 `claim:{requestId,status,jobId?}`。空队列或终态返回 `job:null`，终态不会改领另一任务；省略 ID 保留旧版行为。完成 `{id,leaseToken,result}` 或 `{id,leaseToken,error}`。完成/取消竞争、过期租约和相同结果的重试由状态与哈希校验处理。部署者配置与投递恢复见 [OPERATIONS.md](OPERATIONS.md)。
