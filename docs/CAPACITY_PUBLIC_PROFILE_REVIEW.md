# 300 证券公开 forecast-only profile：最小实施审查

状态：公开300 profile仍为方案，尚未接入或上线；本文第5节的显式快照索引策略已在独立capacity worktree实现并通过本地Worker测试。依据本 worktree 的代码及 [首次300证券实验](PHASE_B_300_BENCHMARK.md)。不新增1000证券 profile，不扩大900秒计算或300秒单轮投递预算。

## 1. 最小公开边界

公开的是“完整研究池 pooled 单资产双端点预测与因子增量”，限定 `asset_price`、Ridge两个候选、至多300证券/3年/16因子、250,000输入/样本、60,000主预测、2×2验证、refit≥20和 forecast-only。默认50证券路径保留。profile 独立于预测参数，服务端审批后的 profile ID/版本随 experiment/run/claim/产物保存，不能由 runner 在完成时自行宣称更大额度。

首个真实闭环宜接受**先独立准备并完成验证的冻结数据集**。数据可来自明确上传或既有授权 provider 采集；合成演示必须单独标识，不能作为真实研究失败的回退。预测任务只引用完整数据根，不把现场600余次接口读取与900秒拟合混成一个不透明任务。现 bundle/1 要求 report/forecast/coverage/snapshot，不能用空预测冒充独立数据集；独立数据准备须有自己的小 manifest/提交合同。

UI 必须显式选择该能力，保存完整300成员与池快照，显示数据可用性/完成覆盖；不能截前50。事件、财务、PCA全池、auto和执行暂不从这个 MKT+Ridge验收自动继承。已有账户仍可查看、下载和比较预测。300产物的执行接口应明确拒绝 `EXECUTION_PROFILE_NOT_SUPPORTED`，不可让用户在排队后才遇到普通50证券错误。

## 2. 必须一起改的边界

| 位置 | 需要的最小改造 |
| --- | --- |
| `edge/statistical-quant/validation.mjs`、`api.mjs` | 独立 profile 准入、实验版本绑定、provider/dataset来源约束；共享 Python/JS 接纳拒绝 fixture |
| `edge/runner-claims.mjs`、Python `runner.py` | runner 声明 profile/version 能力；不匹配的 runner 跳过任务；只在明确新 profile 下调用 capacity API，仍单 slot、硬900秒 |
| `provider.py` 与数据准备层 | 显式数据预算及完整300成员校验；不可直接把全局 MAX_SYMBOLS/MAX_ROWS 提高；来源cache按证券/区间/字段/修订版本复用 |
| `runner_artifacts.py` | `freeze_input`/`restore_input` 目前会调用旧110k/50验证器；需验证原数据 profile 的显式恢复路径，保留17位精度、PIT、source/data hash；不能只给 `_prepare_data` 传大上限 |
| `edge/bundles/manifest.mjs` | 当前25k预测/110k快照硬拒绝；准入上限从服务器绑定的 profile 取，旧 profile 不变；bundle字节/分片/总行上限先不扩大 |
| `edge/bundles/records.mjs` | 单资产目标自身只有1条腿，target.symbols上限无需扩大；但本例每条 `hedgeFits.targetIds` 有300个ID，现50个及8KiB metadata限制均会拒绝。只对已验证asset profile开放至300及32KiB该类元数据，或另建紧凑引用表，不能全局放开所有记录 |
| 用户报告/下载/来源恢复 | 继续 manifest 固定版本分页；可下载原始私有包后独立审计；旧 sourceStrategy 中没有调度profile，不应从symbols数量猜测授权 |

现 provider 按证券读取 `daily`+`adj_factor`，300证券至少 `1+2×300=601` 次尝试前的正常请求，已超过512预算；若再读daily_basic则至少901，重试还要另计。不能假设多代码或按日期批量端点可用；需先验证现授权入口的参数、最大响应与完整性。最小安全实现可以保留已知逐证券读取，改为独立、可恢复、有明确总尝试和速率预算的数据准备任务；以后再引入经实证的批量读取。失败、停牌缺行、无返回证券、权限不足都应保留明确原因，未完整准入的数据根不能进入预测队列。

## 3. 当前哪些 D1 行确实有用途

`edge/bundles/storage.mjs::uploadChunk` 对所有集合调用 `recordIndex`，再以 `json_each` 写入 `quant_bundle_records`。每行除主键外还涉及ID唯一索引和5个查询索引。当前300包有366,500集合行；其中234,900行 snapshotRows 占64.1%。

**snapshotRows 没有用户页或引用 JOIN 用途。** `pages.mjs` 不允许访问它，`user-api.mjs` 从公开 transport 集合中排除它；runner重放、完整导出和文档哈希使用 R2 chunk receipts，不查询行情行索引。它目前仅参与 `verify.mjs` 的统一集合 count 和 `quant_bundle_record_ids` 对 `(trade_date,ts_code)` 的跨片唯一性检查。不能删掉索引后只用声明行数代替这项唯一性保证。

已实现的窄优化是显式 `snapshot_sorted_v1` 验证策略，未来公开profile可绑定它；目前仍受原50证券/110,000输入等上限约束：

1. 冻结前按 `(trade_date,ts_code)` 严格排序，完整快照和 source fingerprint 对应此顺序；服务端准入绑定此策略，旧包按原规则接受。
2. 上传时逐条验证日期/证券，要求片内 key 严格递增；服务端计算 count/firstKey/lastKey，随已核验 SHA 的 chunk receipt 原子落库。不得接受 runner 自报边界代替实际扫描。
3. finalize 按 descriptor ordinal 核对 start/count连续性、所有 receipts，且前片 lastKey 严格小于后片 firstKey；由此拒绝跨片重复和乱序。空片禁止，缺行是否允许仍按研究数据/PIT合同，不伪造“满格数据”。
4. `verifyRecords` 对这个集合改为核对服务端receipt统计；其他集合仍核对真实索引count。R2原始字节/完整文档hash、owner、lease和取消栅栏全部保留。

这将本例持久逐行索引从366,500减到131,600，而不是减少输入或预测数量。snapshot仍完整存储和可审计；无需逐行D1查询的物理索引不是研究证据本身。新策略需保存到 stage 的不可变服务端验证版本或等价元数据中，防止同一个 staging 中途变更规则。

**plannedOrigins 先保留。** 它当前被主/基线按ordinal联结以验证每条 date/target/entryDate/targetDate与独立计划一致，还用于日期单调校验，并且本身是已公开的可分页集合。未来可用有界的计划/主/基线 co-iteration 代替SQL，但那是新的流式验证器及分页实现；仅对三者数量/hash做比较不等价。首步删除snapshot索引已经消除最大无查询用途集合，无需同时重写这条完整性边界。

## 4. 交付时必须实际验证的反例与预算

- snapshot同证券日重复在同片及跨片、首尾次序交换、合法但缺少一天、缺片/重片、伪造count或边界、内容变更但旧hash：按合同逐一拒绝或明确保留缺失，不能静默补零。
- 主/基线换序、丢失invalid/tail origin、目标/拟合引用缺失，均保持原finalize拒绝；完整旧包行为不变。
- profile伪造、旧runner误领300、执行接口借300预测绕过限制、跨owner数据根、传输期间取消、已成功片丢ACK、进程重启：不能重拟合、越权或发布partial。
- 在真实Worker上量化每片 R2 PUT、D1写入、hash/JSON校验、finalize各自耗时、字节、读写行数；保留网络和服务端时间，不用总耗时推断某一瓶颈。
- 300包约128.4MB，明显大于已测50股包。少写64.1%索引不保证传输快64.1%，仍需完整56片真实上传、提交、报告遍历和私有包审计。300秒单轮投递耗尽应保留加密spool与准确缺片，下一轮只续传；不重新计算，不无界续租。

建议下一垂直切片先完成“snapshot索引策略+已有50股包回归+固定300包真实Worker投递”，保留现数值profile为实验能力；验证预算后再接独立真实数据准备、公开选择与runner调度。若投递仍过慢，依据分阶段测量继续优化，不先扩大timeout或减少计划覆盖。

## 5. 已实现的窄传输协议：snapshot_sorted_v1

此改动没有修改Python、provider、runner、原始bundle字节、逻辑artifact身份或公共研究准入。它只改变服务端如何保存冻结行情的唯一性证据。`atlas.quant.bundle/1`仍是传输格式，`snapshot_sorted_v1`是另一个明确命名的物理索引验证策略，不是新的forecast版本。

新阶段的选择通过已有 `POST /runner/bundles/begin` 增加一个可选字段：

```json
{
  "id": "leased-job-id",
  "leaseToken": "current-lease",
  "bundleId": "unchanged-manifest-sha256",
  "manifestText": "unchanged canonical manifest text",
  "snapshotIndexStrategy": "snapshot_sorted_v1"
}
```

- 服务端必须显式配置 `BUNDLE_SNAPSHOT_SORTED_V1="true"` 才能准入新有序阶段。默认或显式 `indexed_v1` 保留旧逐行索引路径。未知值、null和客户端提供的 `indexValidation` 均拒绝。
- begin响应增加 `snapshotIndexStrategy`，与 `stageId` 一起回显已冻结策略。已有阶段恢复时，即使能力flag关闭也继续其原策略；省略字段不会降级。显式请求不同策略返回409。该策略仅适用于含snapshot的新预测包；独立执行包继续已有路径。
- 无schema迁移。服务端在已有 `stage.metadata` 中保留 `indexValidation={version:1,snapshotRows:{strategy:"snapshot_sorted_v1",receipts:{...}}}`。这些字段不进入manifest、report或forecast hash，也不接受客户端摘要作为证据。
- 每个ordinal的摘要为 `{start,count,sha256,firstKey,lastKey}`。key是固定ASCII `YYYYMMDD|NNNNNN.SH/SZ`；日期/证券格式采用与原逐行索引相同的验证器，不新增“真实交易日或数据完整”的认证。
- begin在插入stage之前按所有已声明snapshot chunks计算完整最坏摘要大小。`indexValidation`硬上限64KiB；连同原metadata的最坏总长度仍不得超过512KiB。最多256摘要，不能上传到末尾才发现没有摘要空间。
- 上传先校验原始JSON字节、SHA和真实行数，再逐条验证实际记录key严格升序。实际首尾key由服务端扫描产生。R2原文写入后，`JSON_SET`基于D1当前metadata更新单个ordinal，与原chunk receipt插入在同一事务内，并受同一stage/lease/status栅栏保护；并发不同片不会读取后整对象覆盖另一片摘要。实际D1事务失败会一起回滚两种凭据。
- 同片精确重试返回幂等结果，并复核已保存R2内容及摘要。允许按任意网络顺序上传；finalize仍按manifest ordinal顺序验证receipt的start/count/hash，以及前片lastKey严格小于后片firstKey。缺片、重复、乱序、计数或哈希不符不能发布。新策略下snapshot逐行索引必须为零，避免同一阶段混用证据路径。
- 完整原始snapshot仍在R2；最后仍流式核验所有文档SHA。其他集合的完整索引、plannedOrigins逐项关联、原始页面/来源读取和发布事务均保留。缺行情行可以被原样保留为缺失；排序证据不代表每个证券日都有行情。

### 本地验证边界

新增 `tests/snapshot-index.test.mjs` 的19例使用真实Miniflare D1/R2和实际HTTP路由，覆盖：显式准入及冻结策略；反向到达与并发摘要合并；重复ACK；旧默认及旧乱序唯一快照；同片/跨片重复与乱序；重新计算完整hash仍不合法的内容；缺行情日保留；缺片、伪计数、伪摘要hash、R2损坏；D1 trigger导致真实事务回滚；R2写后取消；租约失效；plannedOrigins错配；manifest顺序/空snapshot/原110k限制；最坏metadata预留。原15例bundle/fencing测试同时通过；完整 `npm test` 为134/134，`npm run check`及`git diff --check`通过。

本地开发环境通过ignored的`node_modules`和`.venv`链接复用既有依赖。完整套件最初因该worktree缺少`.venv/bin/python`而失败，并使其共享队列的后续断言受影响；补齐链接后从全新Miniflare实例重跑，134例全部通过。这不是一次新的独立依赖安装验证。

根审查又使用已冻结的 50 股八年大案例实际走完本地 Worker/D1/R2：36 个分片、59,882,995 字节，19,700 条主预测及同量基线。全部 104,350 行 snapshot 保留原字节，但不再生成对应逐行 D1 索引；其他 80,235 条索引完整保留。提交、分页、完整 32,994,262 字节报告下载通过，报告 SHA-256 仍为 `0c6eaf474bf1c4c8c3fd59c7d9bce7dc6e34fea5576c47715b941cd6e8f14a88`，bundle/forecast 身份不变。此次本地上传约 2.98 秒，不能推定为云端时延；无重新拟合或 provider 请求。私有回执为 `private/sorted-large-20261008.json`。

以上证明本地实现、完整大包与兼容边界。尚未修改生产flag、runner协商或公开300准入，尚无新策略的云端时延结论；也没有把50股投递冒称300包已完成生产投递。下一阶段仍须在保持完整数据与900秒计算/300秒单轮投递预算下验证公共大包链路。
