# Atlas Quant v0.2 API

同源 API 根路径为 `/quant/api`。JSON 错误格式为 `{"error":{"code":"...","message":"..."}}`。以下契约面向当前源码；部署能力以 `GET /health` 为准。浏览器写请求使用 `Content-Type: application/json`，不允许跨站请求；private 端点需要先通过 `GET /session` 建立 HttpOnly 工作区 cookie。

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
| `GET /universes/:id` | 完整成员、推荐计算子集、日期与快照哈希 |
| `GET /research-presets` | 4 个研究目标、15 个精选因子包与对应策略模板、5 个模型组合预设 |
| `GET /factors`、`GET /factors/:id` | 兼容因子列表与单个内置、社区或字段配方详情 |

字段和因子分页支持 `q,database,category,availability,page,pageSize`；`pageSize` 为 1–100，默认 30。可用性为 `all`、`ready`、`needs_mapping`、`unavailable`，`schema_only` 兼容 `needs_mapping`；`unavailable` 返回当前空目录；未知值返回 400。字段搜索将 `%`、`_` 作为文字匹配。股票池支持 `q,category,page,pageSize`。

`FD` 是财务供应商适配器的兼容筛选键，其 `logicalStore` 为 `EXT`；`PCD_DERIVED` 对应 `MODEL`。`COMMUNITY` 表示贡献来源，不是 Financial DB 的第五库。数值字段的原值、rank、zscore 配方可浏览，但仍需真实时点数据；非数值字段不生成这些配方。

`ready` 不代表股票覆盖、收益或人工审核通过。社区外部字段表达式可以仍是 `needs_mapping`。因子目录包括最近最多 1,000 个公开社区条目；兼容 `/factors` 列表有更小上限，使用分页目录发现扩展条目。

## 私有策略与实验

| 方法 / 路径 | 请求或行为 |
|---|---|
| `GET /strategies` | 当前工作区的保存策略 |
| `POST /strategies` | `{strategy}`；创建版本 1 |
| `PUT /strategies/:id` | `{strategy,version}`；版本冲突为 409 |
| `DELETE /strategies/:id` | 删除自己保存的策略 |
| `GET /runs` | 当前工作区任务摘要 |
| `POST /runs` | `{strategy,dataSource,dataset?}`；返回 202 与 `{job}` |
| `GET /runs/:id` | 任务状态，完成后含 result |
| `GET /runs/:id/export` | 完整报告 JSON |
| `POST /runs/:id/cancel` | 取消自己的排队或运行任务 |

`dataSource` 必须显式为 `demo`、`upload` 或 `tushare`。上传的 `dataset` 为 `{rows,provenance}`，最大 24 MiB；请求包络最大 26 MiB。完整结果最大 24 MiB，超限明确失败，不静默截断预测或交易。每工作区同时 1 个实验、每天最多 20 次提交；全局队列最多 30 个。

完整策略例见 [research-v02.json](../engine/examples/research-v02.json)。`schemaVersion` 保持 1，新增字段兼容已有策略：

- `universe.symbols`：3–50 个独立沪深 A 股代码；start/end 为 `YYYYMMDD`，范围最多 8 年。
- `factors`：1–32 项 `{id,expression,direction}`，唯一 id；受限因果 DSL，方向 ±1。
- `model.mode`：`auto` 或 `manual`；manual 仅允许一个模型系列；auto 比较实际勾选系列。
- `model.candidates`：`factor_score,ridge,elastic_net,hist_gradient_boosting,bayesian_ridge,huber,random_forest,extra_trees` 的非重复子集。
- `model.target`：`forward_return`（默认）或 `forward_excess_return`；horizon 1–20，metric 固定 `rank_ic`。
- `portfolio`、`costs`：明确初始资金、topN、权重上限、调仓间隔和双边/卖出费用；允许目标权重合计不足 1 并保留现金。
- `graph`：可为空图；非空时需完整连接六个合法阶段。图不能绕过引擎配置检查。

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

runner 的 `POST /runner/claim`、`/runner/heartbeat`、`/runner/complete` 使用独立 bearer 服务凭据，不能用浏览器工作区冒充。领取返回租约与有限任务；完成 `{id,leaseToken,result}` 或 `{id,leaseToken,error}`。完成/取消竞争、过期租约和相同结果的重试由状态与哈希校验处理。部署者配置与投递恢复见 [OPERATIONS.md](OPERATIONS.md)。
