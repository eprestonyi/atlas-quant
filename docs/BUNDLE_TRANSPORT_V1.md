# Phase A：完整研究产物分片传输协议

状态：Phase A 实施合同；**尚未完成验收，不是已部署能力**。本轮保留策略 schemaVersion=2、forecast artifact schemaVersion=1、原 artifactId 和数值语义；新增传输格式 `atlas.quant.bundle` version `1`。`bundleId` 是传输 manifest 的身份，绝不是 forecastId／artifactId。50 股、32 因子、25,000 预测、110,000 样本、900 秒、单计算槽保持不变。

## 预算与编码

- 未压缩 UTF-8 JSON；不引入 Parquet 或解压协议。
- manifest 原始字节最多 512 KiB；chunk 目标 4 MiB、硬上限 8 MiB。
- 每 chunk 最多10,000项、各collection合计最多1,000,000项；超预算明确失败，不裁剪。
- 每 bundle 最多 256 chunks；manifest 加所有唯一 descriptor 对应的 chunk 字节总和最多 256 MiB。
- chunk 是完整 JSON 数组，数组项不跨 chunk；某个单项超过 8 MiB 时明确失败，不切断记录。
- root metadata、chunk 和 recipe 中所有数值有限。Python v1 canonical 规则：递归整数值 float 转整数（含负零）、对象 key 排序、UTF-8、无多余空格、allow_nan=False。保留原始 canonical 字节；Worker 不重序列化数值后计算 v1 hash。
- 同一逻辑产物可以有不同 chunk 布局／bundleId，但原 forecast artifactId 必须不变。默认 writer 使用固定字节／行序规则，重试必须复用同一 manifest 与字节。

## Manifest

`bundleId = SHA256(manifestText 的 UTF-8 原始字节)`。manifest 本身不含 bundleId；所有 hash 是 64 个小写十六进制字符。上传时 manifestText 作为字符串保留，不能在服务端 parse/stringify 后代替原字节。

```json
{
  "format": "atlas.quant.bundle",
  "version": 1,
  "kind": "forecast",
  "forecastArtifactId": "<64hex>",
  "predictionConfigHash": "<64hex>",
  "dataFingerprint": "<64hex>",
  "documents": {
    "forecast": {"parts": [], "sha256": "<原 artifactId>", "byteLength": 123},
    "report": {"parts": [], "sha256": "<完整 report canonical hash>", "byteLength": 456},
    "snapshot": {"parts": [], "sha256": "<完整 snapshot canonical hash>", "byteLength": 789},
    "coverage": {"parts": [], "sha256": "<coverage canonical hash>", "byteLength": 100}
  },
  "collections": [
    {
      "id": "forecasts",
      "document": "forecast",
      "path": "/rows",
      "rowCount": 250,
      "chunks": [
        {"ordinal": 0, "start": 0, "count": 250, "sha256": "<64hex>", "byteLength": 12345}
      ]
    }
  ],
  "totals": {"chunkCount": 1, "chunkBytes": 12345}
}
```

实际值必须自洽；示例数字仅展示结构。kind 为 `forecast` 或 `execution`。forecast bundle 有四个 documents；execution 有 forecast/report/coverage，不能覆盖来源 snapshot。报告仍包含原 forecasts 对象，只是在传输 recipe 中引用，而不是物理重复保存。

每个 document 的 parts 是以下指令的有序数组：

```json
{"literal":"{\"rows\":"}
{"collection":"forecasts"}
{"literal":",\"schemaVersion\":1}"}
```

- literal 是需要原样拼接的 canonical JSON 语法片段；所有 literal 总量受 manifest 上限约束。
- collection 输出 `[`，按 ordinal 输出各 chunk 去掉外层 `[]` 后的原始内容，以逗号连接，最后 `]`。空集合输出 `[]`、没有 chunks。
- 仅 report 在 `/forecasts` 的位置允许 `{"document":"forecast","wrapArtifactId":"<原 artifactId>"}`。它将 forecast 文档最外层 `{}` 的内部内容包装成 `{"artifactId":"<id>", ...}`；artifactId 在原 v1 canonical 对象中恰好排在其他顶层字段前。不能任意 slice、循环引用或引用其他 document。
- forecast 文档是原 artifact **删除 artifactId 字段后** 的完整 canonical JSON，其 SHA256 必须等于原 artifactId。report recipe 添加回这个字段后与原 report 对象一致；snapshot 与 coverage 是各自完整对象。
- collection 的 document/path 是实际 JSON Pointer。finalize 必须核对真实拼接位置；不能只相信声明 path。collection 恰好被其所属 document 引用一次。所有 descriptor 均必须可达、ordinal 连续、start/count 连续、没有重叠或漏片；不允许在 literal 中用另一份大型 rows 替代 collection。
- 解码小 skeleton 可以用于元数据检查；完整 document 校验必须按原字节流进行，禁止把所有 chunks 拼成一个大字符串再 JSON.parse。

固定 collection 名称如下；存在相应数组时一律抽出，包括空数组。未出现的可选路径不生成集合。

| id | document | path |
| --- | --- | --- |
| forecasts | forecast | /rows |
| targets | forecast | /targetDefinitions |
| modelFits | forecast | /modelFits |
| hedgeFits | forecast | /hedgeFits |
| perTarget | forecast | /diagnostics/perTarget |
| outerFolds | forecast | /diagnostics/outerFolds |
| finalTrials | forecast | /diagnostics/finalTrials |
| baselineRows | forecast | /diagnostics/factorIncrement/baselineRows |
| baselineModelFits | forecast | /diagnostics/factorIncrement/baselineModelFits |
| dailyLosses | forecast | /diagnostics/factorIncrement/dailyLosses |
| baselinePerTarget | forecast | /diagnostics/factorIncrement/baselineValidation/perTarget |
| baselineOuterFolds | forecast | /diagnostics/factorIncrement/baselineValidation/outerFolds |
| baselineFinalTrials | forecast | /diagnostics/factorIncrement/baselineValidation/finalTrials |
| equity | report | /equity |
| trades | report | /trades |
| riskLedger | report | /execution/ledger |
| decisions | report | /execution/decisions |
| snapshotRows | snapshot | /rows |
| plannedOrigins | coverage | /origins |

root metadata 超过预算时明确失败；Phase A 不接受任意未知 collection 或未经版本定义的外部路径。

## 完整覆盖与语义校验

coverage 对象为：

```json
{
  "schemaVersion": 1,
  "source": "samples_before_model_fitting",
  "baselineRequired": true,
  "holdoutStart": "20250102",
  "origins": [
    {"date":"20250102","targetId":"target_...","entryDate":"20250103","targetDate":"20250110","inputValid":true}
  ]
}
```

新研究的 origin 计划由构造完整 samples 后、任何预测拟合前的独立 sink 捕获；不进入原 artifact，不改变其身份。计划包括无效输入与尾部未成熟 origin。Worker 校验唯一 date/targetId、顺序、主预测逐项 origin/date endpoints 匹配；baselineRequired 时基线也必须覆盖同一计划，不因模型失败删除记录。有效预测须有 target/modelFit 引用；invalid `targetId=unavailable` 仅对应计划中 inputValid=false 的形成失败，不伪造目标定义。trade 必须引用同一产物中存在的 forecastId。完整计划不等于数据源真实性认证。

执行复用来源 coverage，不从成交或成功预测重建。旧单包 artifact 没有独立 origin 计划，允许兼容转换时 source=`legacy_artifact_derived`，明确证据等级；不能标为 pre-fit。新计算的 bundle 不得使用该兼容等级。

finalize 顺序：核对全部实际 chunk 字节／长度／数组项数及合法字段 → 以有界流重建所有 canonical document hash → 核对 layout／plan／唯一性／模型目标与成交引用 → 标 verified。提交完成前 staging/verified bundle 不可公开读取或用于 execution。

## Runner API（路径相对 /quant/api）

所有接口使用现有 runner Bearer 授权；owner 从有效 lease/job 推导，不能由请求指定。

claim 请求新增 transportFormats:["atlas.quant.bundle/1"] 能力声明；支持时schema2 job返回 resultTransport:{format:"atlas.quant.bundle",version:1}。新runner只在该回显存在时走bundle路线，否则保留旧24MiB接口。来源为bundle的execution不得由无bundle能力的旧runner领取；重试已有映射但运行时已降级须报RUNNER_UPGRADE_REQUIRED，不能偷偷领取另一个job。

1. `POST /runner/bundles/begin`：`{id,leaseToken,bundleId,manifestText}`。响应 `{ok:true,stageId,bundleId,status:'staging'|'verified'|'committed',missing:[{collection,ordinal}]}`。exact same manifest 重试返回缺片；同一任务冲突内容 409。
2. `PUT /runner/bundles/:bundleId/chunks/:collection/:ordinal`：原始 JSON 数组 bytes；headers `X-Quant-Job`、`X-Quant-Lease`、`X-Quant-Stage`，Content-Type application/json。实际 SHA/bytes/count 与 descriptor 一致后私有 R2 保存并记录 receipt。响应 `{ok:true,bundleId,collection,ordinal,sha256,idempotent}`。相同片重试幂等，冲突 409。
3. `POST /runner/bundles/finalize`：`{id,leaseToken,bundleId,stageId}`。只有全部校验成功才返回 `{ok:true,bundleId,status:'verified'|'committed'}`，不在此处把 job 改 completed。
4. 现有 `POST /runner/complete` 新增互斥形态 `{id,leaseToken,bundleId,stageId}`，与原 result/error 不能混用。它原子发布已 verified bundle 的 job、forecast、snapshot／execution 引用，终态 receipt 和幂等修复规则保持。仅此后 bundle 可用于用户读取和执行。
5. execution claim 对 bundle 来源增加 `sourceTransport:{format:'atlas.quant.bundle',version:1,bundleId}`。`POST /runner/replay {id,leaseToken,kind:'bundle'}` 返回 `{bundleId,manifestText}`；只允许读取该 execution 的原始来源 forecast bundle。旧 kind=forecast/dataset 保留单包兼容。
6. `GET /runner/bundles/:bundleId/chunks/:collection/:ordinal`：同私有 headers，原始 bytes；只允许当前执行租约读取原来源 bundle 的已提交片，不能指定其他 owner／job bundle。

stageId 是服务端生成、绑定 owner/job/lease 的上传身份；同一 begin 重试返回同 stageId。不同 job 即使 bundleId 相同也不能共享租约或覆盖 staging。bundleId 仅为内容身份，不承担 owner／fencing 语义。

wrong lease 409；cancelled／expired lease 的同身份上传返回明确 terminalDiscard，不允许复活或发布。未知 ACK 保留相同本地 manifest/bytes/request UUID；不能为“重试”新建 bundle。missing 返回值必须逐项属于原 manifest，runner 不据此上传外部路径。

本地新 bundle spool 与旧 completion/snapshot spool 分开：0700目录、0600文件、每片认证加密、AAD直接绑定 queue＋job/lease＋文件名；manifest经认证后以其bundleId及chunk SHA验证内容，形成对bundleId的传递绑定。因bundleId在写完分片后才确定，不宣称每片AAD直接含bundleId。只通过小 manifest／安全 spool reference 跨子进程 Pipe，禁止再次把整报告放入 IPC JSON。计算仍受原900秒硬预算约束。每个待投递bundle额外有单轮300秒总预算，包含最多5次尝试和退避；每请求最多60秒，开始及每15秒（请求边界检查）发送heartbeat并检查本地STOP。超时或本地停止保留spool，只恢复缺片传输；取消／失租必须同UUID终态确认后才能清理，不重新计算。heartbeat不延长计算预算。旧 `.enc` 格式继续可投递。仅匹配 claim terminal receipt 后清除本次 spool；磁盘残片不视为已确认结果。

## 用户 API 与 GUI

保留旧 `/runs/:id` 与 `/statistical-quant/executions/:id` 的完整报告语义；bundle 来源由服务端流式重建返回完整 JSON，不回退整包解析。新增报告端点既可服务 bundle，也可为旧单包报告提供兼容 adapter。

- `GET /runs/:id/report` → `{job,report,transport}`。未完成 report=null；旧单包来源返回原完整 report、transport=null。仅 bundle 来源返回 scalar／小配置与诊断摘要，不包含大数组；transport 为 `{format,version,bundleId,complete:true,logicalArtifactId,collections:{id:{total}},downloadUrl}`。
- `GET /runs/:id/report/pages?bundleId=&collection=&offset=0&limit=25&...` → `{items,total,offset,limit,nextOffset,hasMore,bundleId,related}`。limit1..100；一页最多读取8个必要 chunks，允许返回少于limit并给nextOffset。pages/detail/chart 必须传 bundleId query，并与该 report 的已提交身份匹配，防止混入其他版本。offset基于当前不可变查询顺序；同请求返回同页。支持明确索引过滤 dateFrom/dateTo、targetId、status（all/valid/invalid/mature/unmatured）、id，以及 forecasts scope=all|latest；不支持任意substring扫描。
- forecasts 的默认顺序 date desc，然后 targetId／forecastId 稳定排序；其他集合用存储顺序。latest 用目标稳定组 identity（symbols、construction、PCA projectionColumn），不能用随 refit 变化的 targetId 分组。派生 groupKey/targetLabel 在相关 metadata 中返回，不改原 forecast 行。
- `GET /runs/:id/report/detail?collection=&id=` → `{item,related,bundleId}`；forecastId、targetId、modelFitId、riskLedger date 和试验 ID 可独立检索，不依赖当前缓存页。
- `GET /runs/:id/report/chart` → `{points,totalPoints,samplingMethod,range,bundleId}`，上限1000 points；顺序逐片解码，仅投影 date/equity/benchmark；完整 metrics 使用原结果，不用抽样曲线重算。
- riskLedger filter=all|events|missing；events 保持原GUI含义：风险 breach、exit_pending 或当日存在 risk_limit_exit 成交；missing 由 unavailableRiskInputs 定义。若特定派生过滤尚未实现，显式返回不支持并在GUI不展示控件，不能改变含义。
- downloadUrl 直接指向流式完整 JSON report 导出；浏览器不 fetch 全包再 JSON.stringify/Blob。manifest+chunks 私有目录导出目前只有本地 CLI；runner 可复用云端已提交 bundle 执行，**没有云端完整 bundle 目录导出 API**。snapshot 永远遵循私有访问权限，不混入普通公开报告下载。

分页最多定位8片，但一次只解码1片；保留记录的原 JSON 合计最多8MiB。超过此页预算即缩页并返回 nextOffset，单条超出预算则明确413，不静默截字段。页上限并不允许同时持有8个完整解码片。

索引在每片校验／finalize阶段创建，只保存查询必需的定位字段；不能在每次分页请求重新扫描全部 chunks。用户分页必须先校验 owner 与已提交状态。details不得查询其他bundle记录。预览分页不是不完整计算，UI分开展示两者。

finalize 预载最多256条分片receipt，最多进行512次R2读取，再加固定D1校验／写入；部署需要支持1000次subrequest预算的Paid Worker配置，不能将本地Miniflare通过当作免费Worker预算证明。清理只处理已 cancelled/failed 且超过30天的旧上传，每次最多1个stage；已committed产物不做自动TTL删除。

## 阶段验收责任

- 引擎／runner：bundle writer/reader/spool、pre-fit coverage sink、原v1hash与数值一致、超过24MiB产物有界传输、unknownACK及重启恢复。
- Edge：schema/migration、lease/owner保护、chunk实际验证／stream hash、layout/coverage/refs、finalize与complete原子发布、页只读必要片。
- GUI：真实summary/分页/详情/有限chart/直接download，旧报告兼容，无隐式全量加载。
- 独立 audit：标准库逐片验证manifest、canonical streams、原artifactId、完整计划和全部账本；真实Worker→runner→导出→audit→独立execution对照。

任何库级测试、模拟接口或synthetic证据不得替代Phase A最终全链路验收。此阶段不部署或提高证券／模型语义上限。

CLI 导出布局固定为 `manifest.json` 和 `chunks/<collection>/<ordinal>.json`（ordinal十进制无补零）；目录名不进入manifest。导出目录0700、文件0600；manifest最后发布，无manifest的残片是未完成导出。

## 本地实际复现命令

```sh
.venv/bin/python scripts/local-run.py engine/examples/statistical-quant.json --source demo --bundle-output private/example-forecast
.venv/bin/python scripts/audit-bundle.py private/example-forecast
.venv/bin/python scripts/replay-execution.py --source-bundle private/example-forecast --bundle-output private/example-execution
.venv/bin/python scripts/audit-bundle.py private/example-execution --source-bundle private/example-forecast
```

输出目录必须不存在；包含snapshot的原预测bundle是私有输入。`--output`可另存小receipt；`--overrides`仍仅接受execution/portfolio/costs。原有双文件report+snapshot的CLI用法保持。
