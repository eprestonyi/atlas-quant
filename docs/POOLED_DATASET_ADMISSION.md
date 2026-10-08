# 公共300研究：统一冻结数据集与准入合同

**状态：2026-10-08设计稿，尚未实现、开放或部署。** 本文定义下一垂直切片，不能据此声称已有公共300能力。现有50证券路径保持；独立capacity数值实验和 `snapshot_sorted_v1` 本地实现分别见 [300实验](PHASE_B_300_BENCHMARK.md) 与 [公开profile审查](CAPACITY_PUBLIC_PROFILE_REVIEW.md)。本次只写合同，没有调用provider、重新拟合或修改实现。

本合同与financial工作树的 `FINANCIAL_WORKSPACE_CONTRACT.md`、`financial_statements/dataset.py` 协调：财务准备与市场准备有各自领域校验，但研究只消费**同一种不可变research dataset引用**。prepared财务来源不是另一条可绕过数据集准入的运行入口。

## 1. 最小可上线切片与暂不继承的能力

首切片：用户明确选择完整≤300证券、≤3年，先准备冻结MKT数据，再用全池横截面因子和一个pooled双输出Ridge进行forecast-only研究，保留同窗无新增因子基线、嵌套选模、终段holdout与全部预测。数据准备、预测、分片交付分别有状态和预算。

- 第一公共准入修订为 `pooled_asset_300_v1@public_mkt_v1`，引用已有数值profile `pooled_asset_300_v1`，不改模型含义。首批仅 `asset_price`、`family=trend`、`estimator=ridge`、1–16个MKT价格/量因子；2个既有Ridge配置、2×2验证、refit≥20。其他family须另外验收后显式增加准入修订，不从库函数可调用推断公共支持。
- `execution.enabled`必须false。创建独立执行、复制为执行、comparison/replay的任何入口都在排队前拒绝 `EXECUTION_PROFILE_NOT_SUPPORTED`。不能让300来源落回旧50校验或用截取50只通过。
- 不按50只分组训练，不减少证券、观察频率、候选、折数或基线来适应预算。当前指数成分回看明确是当前固定成员池；没有成分生效历史时不标为历史PIT指数。
- 原有50路径仍接受原来的即时provider/上传/旧单包来源；新profile必须使用ready `datasetRef`，不在900秒计算过程中读取行情接口。
- 第一纵向验收先走一个真实、已冻结且有授权的MKT数据集；分片上传可以先完成工程验收。托管Tushare准备只有在当前入口的端点/速率/权限及一次完整固定池采集验收之后才能打开。合成仅用于单独标识的测试，不能替代失败的真实采集。
- financial来源的prepare/UI可先独立上线；其 `researchBinding` 在组成、准入、证据导出与离线复验全部完成前为false。现 `dataset.py` 实际仍受50证券/110k行情及24MiB金融输入、24MiB金融输出、64MiB共同预算约束；本MKT300实验没有解除这些限制。

## 2. 一个研究引用，几个不能混同的根

唯一研究数据引用DTO：

```json
{
  "datasetId": "owner-scoped-server-uuid",
  "datasetRoot": "<64hex>",
  "format": "atlas.quant.research_dataset",
  "version": 1
}
```

UUID用于owner授权和生命周期；hash用于内容身份。内容相同不授予跨owner读取权限。请求不能传 `ready:true`、任意R2 key、外部下载URL、认证头或客户端“已审核”标志来代替服务端状态。

`datasetRoot`是新typed closure manifest原始canonical UTF-8字节的SHA256，manifest自身不含此根。不要把它改名为以下任一已有根：

| 根 | 含义与保留方式 |
| --- | --- |
| marketRoot | 完整冻结market输入的既有组件内容根；组成前后均保留 |
| dataFingerprint | 现引擎17位float/完整日历的数值输入身份；仍独立验证，不能用manifest hash代替 |
| calendarRoot | 实际sessions、完整覆盖边界、来源/证据版本的根；仅同sessions不代表同证据 |
| inputRoot / packRoot | 财务原始快照与日历根，以及加入声明/单位策略后的包根 |
| preparedRoot | 已计算财务panel/events/assignments等准备结果根 |
| financialDatasetRoot | 现桥绑定marketRoot、金融refs、实际joined rows及字段metadata的组合根 |
| datasetRoot | 上述实际组件、scope、编码/转换策略与全部私有证据闭合关系的传输身份 |
| resourcePlanRoot | 某次研究在该dataset上的特征/origin/fold/资源计划；不是数据根 |

market-only与market+financial都使用同一dataset格式和引用。财务 `financialInputs[{inputId,preparationId,inputRoot,packRoot,preparedRoot,calendarRoot,stateIds}]` 用于**请求创建组合dataset**，不与 `datasetRef` 平行注入run。runner使用的是服务端解析这些refs后得到的包；不得相信传入preparedRoot、`semanticKind`或 `model_fin_*` 数字列就认定经过财务核心。

组合须调用现纯函数 `compose_financial_dataset(strategy, market_dataset, financial_packages, ...)`，重新验证包并prepare；核对重算根。只left join既有行情键，不造停牌价格，不用报告期末反向填充。`state×symbol`来源冲突先拒绝；单位、currency、scope、版本与calendar证据一并保持。trusted proof只能由受控registry解析，公开请求不暴露 `trusted_unit_proofs`。

## 3. Dataset manifest与大输入传输

使用新的 `atlas.quant.research_dataset/1`，**不是给原bundle/1偷偷增加documents**。原bundle/1必须含forecast/coverage，不能用空预测冒充独立数据准备。复用其canonical scanner、SHA、分片预算、lease/owner栅栏与严格排序原语，不复用错误的业务种类。

manifest骨架如下，数字和hash为结构占位：

```json
{
  "format": "atlas.quant.research_dataset",
  "version": 1,
  "kind": "market",
  "scope": {
    "symbols": ["000001.SZ", "600000.SH"],
    "start": "20230101", "end": "20251231", "frequency": "1d",
    "membershipRoot": "<64hex>", "historicalMembershipVerified": false
  },
  "calendar": {"calendarRoot":"<64hex>","componentRoot":"<64hex>"},
  "composition": {
    "version":"market_only_v1", "marketRoot":"<64hex>",
    "financialDatasetRoot":null, "financialInputs":[]
  },
  "schema": {"schemaRoot":"<64hex>","columns":[]},
  "components": [],
  "collections": [],
  "coverage": {"coverageRoot":"<64hex>","plannedSymbols":300,"observedSymbols":300},
  "totals": {"chunkCount":0,"chunkBytes":0,"records":0}
}
```

正式实现时必须提供精确JSON schema及Python/JS共享fixture；上例不能直接通过生产验证。具体约束固定如下：

1. manifest≤512KiB，完整dataset≤256MiB、≤256片、≤1m集合项。JSON行片目标4MiB、硬8MiB、≤10k对象；一次只解码一片。市场ready行严格按 `(trade_date,ts_code)` 排序，并保存服务端实际count/边界/hash回执；不写市场逐行D1索引。
2. scope成员有序且唯一；marketRows、calendar、sourceRequests、normalizedSourceRows、financialStateEvents、financialAssignments、coverage是注册的集合类型。不存在“任何字段名都可上传”的通用执行入口；用户外部列仍走原严格alias/PIT规则。MKT首profile只开放其实际需要的注册列。
3. 大原始财务包不能塞在literal或小元数据中。组件descriptor必须有格式/版本、内容根、精确字节、每片hash和依赖根。需要保留原JSON字节的组件可使用显式 `encoding=raw_bytes` 的有界parts；Worker只流式hash、不把parts拼接后JSON.parse。受信Python准备器按该组件自己的24MiB硬上限重建/解析。typed JSON数组片与raw parts不得相互冒充。
4. 根的依赖图必须是有限、无环、完整闭合的DAG，最多32个组件、深度≤3。所有refs均解析为同owner已ready组件或本stage已验证组件；未知格式、缺依赖、跨owner、循环、hash不符都拒绝。私有证明和日历证据也是组件，不能只留下指向本机路径的字符串。
5. 一次冻结内容记录实际retrieval cohort、端点和字段、参数、单位、复权政策、日历、来源版本。按证券分批取得的数据不声称是供应商原子快照；`upstreamSnapshotAtomic:false`和每请求取得时间必须保留。原始normalized provider table不冒充原HTTP数字token或原始披露。
6. provider每次响应先形成可恢复的私有receipt。最终可将这些行/证据紧凑写入4MiB片，**不要求601个请求变601个最终片**。请求receipt id仍能定位完整规范化来源记录，聚合不能删掉一次请求的参数/时间/内容hash。

浏览器只解析≤512KiB manifest与≤256KiB摘要/页面。上传使用manifest＋分片文件/目录，逐个File流或slice发给对应chunk端点；现64MB行情JSON须先由流式CLI打包，不能在浏览器 `JSON.parse(file.text())` 后切片。未来单tar导入也须有有界header/body扫描器才能开放。financial现≤24MiB源文件原始字节上传可保留作为领域入口，之后进入同一组件闭合机制。

## 4. 准备API与状态：先数据，再研究

所有路径相对 `/quant/api`，owner来自现workspace身份。以下均为待实现接口。

| API | 精确职责 |
| --- | --- |
| `POST /dataset-preparations` | `{requestId,mode,scope,admissionProfile,...}`；mode为market_upload / market_provider / compose；产生preparationId与小计划，不直接ready |
| 同接口market_upload附加字段 | `{manifestText}`，验证结构/预算但不信语义；返回uploadStageId、expectedDatasetRoot、missingParts |
| 同接口market_provider附加字段 | `{providerGrantId,marketFields,adjustmentPolicy,calendarRef?}`；只引用operator授权，不传token或endpoint URL；服务端生成不可变requestPlan |
| 同接口compose附加字段 | `{marketDatasetRef,financialInputs}`；所有组件owner/root已验证，禁止混入新provider请求 |
| `PUT /dataset-preparations/:id/parts/:component/:ordinal` | 原始有界bytes，绑定uploadStageId；实际SHA/长度检查，exact重试幂等，差异409 |
| `POST /dataset-preparations/:id/upload-complete` | 缺片检查后将语义验证任务入队；上传完不是ready |
| `GET /dataset-preparations/:id` | phase、计划/已完成请求数、attempts/预算、覆盖与阻断原因的小摘要；不返回行情列表 |
| `POST /dataset-preparations/:id/cancel` | 取消当前父准备及有效lease；已有ready数据不被撤销/覆盖 |
| `GET /datasets/:id?datasetRoot=...` | 已授权immutable summary、scope/schema/coverage/closure状态与format |
| `GET /datasets/:id/coverage?datasetRoot=...&cursor=...` | 服务端分页的证券/字段缺失原因；固定root，≤100项、≤256KiB |
| `GET /datasets/:id/manifest?datasetRoot=...` | 已ready的小canonical manifest；提供byte hash |
| `GET /datasets/:id/download?datasetRoot=...` | 私有直接流式附件；不能浏览器全量fetch后Blob |

不同mode的字段互斥；未知字段拒绝。`requestId`同owner、同请求body重复返回同准备任务；同UUID不同body409，结果未知时不能自动换UUID。

状态为 `awaiting_upload → queued → preparing → validating → ready`，以及明确 `blocked / failed / cancelled`。blocked表示具体数据/权限/单位/日历问题；不是“暂时没返回”的通用状态。只有私有stage通过全部结构、字节、领域、覆盖和闭合校验后，D1一次事务发布datasetRoot/组件关系/ready与终态receipt。

ready的含义是计划范围完成且可稳定读取，并不意味着每个股票日都有价格、所有金融状态非空或模型有效：

- 第一公共MKT300profile要求每个选中成员至少有合法行情；完全无数据的证券导致阻断并列明，不能静默删成299。个股缺少部分官方交易日仍保留缺行，预测引擎完整网格继续标missing，不补零/前值价格。
- 官方日历需覆盖请求窗口及所需边界；不能用bdate替代后标真实市场。金融公告后的首个可用session不足时明确阻断/缺失，不能猜下一工作日。
- financial `prepared`可以全是明确missing诊断。组成后还要确认请求状态的实际覆盖、可用日和研究训练样本要求；不得仅因16条公式存在或文件prepared就启用运行。
- `completeHistoricalVersionsVerified`、`originalAsPublishedVerified`、`revisionTimeVerified`各按真实来源保留，不能从算术通过推为true。

## 5. D1 / R2 / runner分工

新增独立dataset队列表，避免旧runner将数据准备误当旧schema策略：

| 表（拟议） | 主要键和不变量 |
| --- | --- |
| quant_dataset_preparations | id、owner、request_id唯一、request_hash、mode、scope/plan roots、status、累计预算、结果dataset_id/root |
| quant_dataset_attempts | id、preparation_id、request_id、lease_token/fence、deadline、capability_version、terminal receipt；一次attempt不可换内容 |
| quant_dataset_request_receipts | preparation_id + logical_request_id + attempt_ordinal唯一；先计费许可、随后成功hash/规范化来源/失败码；按冻结plan选择完成结果 |
| quant_dataset_stages / quant_dataset_parts | owner/parent/lease绑定；有界descriptor/实际bytes/count/boundaries/hash；没有未提交可读引用 |
| quant_research_datasets | owner+id、dataset_root、format/version、ready状态、manifest_key、schema/scope/coverage根与小摘要 |
| quant_dataset_components | owner+dataset_id+component_root、kind/version、bytes/locator与依赖根；不是任意URL |
| quant_run_datasets | run_id + dataset_id/root、admission revision、resourcePlanRoot；终态后不可替换来源 |

命名可在实现迁移时统一，但不能把大panel、events或来源依赖树塞D1。每provider请求的≤640条receipt有恢复用途，应保留；250k市场行没有查询用途，不建逐行D1索引。financial事件分页索引由其独立预算控制。

新增 `/runner/datasets/claim` 只面向新runner，携带 `datasetFormats`、`preparationProfiles`和耐久requestId。返回同lease/job身份的小计划；组件按有界页/片读取，绝不通过claim返回64MB dataset。旧runner不知道这个端点就不会误领。forecast现claim另加 `researchProfiles:["pooled_asset_300_v1@public_mkt_v1"]` 和dataset格式能力；没有两者的旧runner跳过300任务，并仍可领后面的旧任务。

同一台runner有共同单slot仲裁：先恢复未知领取/投递，再按公平队列在dataset attempt和forecast间选择。不能因为两个HTTP队列就启动两个大进程。profile/dependency/build能力必须真实匹配；恢复同requestId却降级时明确UPGRADE_REQUIRED，不能领取别的任务。

职责：

- Edge：owner/准备计划/请求幂等/预算许可/lease/fence/分片hash/原子发布与有界读；不在Worker执行pandas或完整财务prepare。
- Dataset runner：唯一provider读取位置；流式规范化/精确排序/日历与来源校验；resolve受审证明并调用金融prepare/composition；提交服务器实际验证的组件和覆盖。model runner没有provider callback。
- Engine：从ready refs下载并逐片校验、恢复PanelStore；全池DAG、成熟标签、训练内预处理、选模和baseline；输出全部origin。财务公式及Decimal证据由现核心负责，不由AI或浏览器重算。
- UI：上传进度/准备状态/根固定的覆盖页面；ready scope只读地绑定研究；无法用编辑证券池绕过dataset的实际scope。需要不同池时创建新的明确dataset view/composition，并重新计算全池rank相关身份。
- CLI/audit：验证完整组件闭合与实际行/来源关系，不接受UI当前页或声明count当全部证据。

## 6. 预算、恢复与取消

以下是建议的第一公共部署硬门槛，仍须实际大包与授权入口验收，不是现provider额度或SLA。

| 阶段 | 拟议硬门槛 |
| --- | --- |
| MKT准备scope | ≤300成员、≤1098自然日、≤250k合法observed rows；只daily+adj_factor+一个固定官方calendar读计划 |
| 请求计划 | ≤640 logical reads；300股冷读通常601项。新增daily_basic/其他端点需另一显式计划/准入，不能默加到该预算 |
| provider实际尝试 | ≤1200次累计，包括重试与结果未知；单logical request最多3次、仅现已定义网络/502/503/504瞬态重试；401/403/429停止，不盲重试 |
| 速率与权限 | `min(operator_verified_rate, 30/min)`；没有已验证授权/速率配置则托管准备不可启用，上传路径不受此假设替代 |
| 单次网络读 | ≤30秒、≤2MiB响应；只白名单请求字段/端点；更大响应明确失败，不只取前若干行 |
| dataset父任务 | 累计≤3600秒主动处理wall；单attempt≤180秒，每个请求边界可checkpoint；deadline由服务端计量，重启不重置 |
| dataset队列/资源 | 每owner最多1个活跃准备、全局最多8个排队父任务；共享单slot；单stage完整输出≤256MiB，raw checkpoint存储另≤128MiB |
| 300模型计算 | supervisor硬900秒（包含本地restore/feature/plan/fit/serialize）、单fit≤300秒；RSS≤3GiB；1模型槽/1 BLAS线程；不采用本地实验API宽松的1800秒父上限作公共许可 |
| 模型规模 | ≤250k inputs/samples、≤60k主预测及同量完整baseline；1–16因子；2候选、2×2验证、refit≥20；实际fit尝试计划在拟合前冻结 |
| 本地磁盘准入 | 实际dataset bytes + 缓存硬400MiB + 预测交付预留256MiB + 一片8MiB + 系统余量500MiB；不足先拒绝，不能写到磁盘满或删除其他证据 |
| 结果bundle交付 | 原256MiB/256片/1m集合项；单轮仍300秒，原有有界续传与加密spool；不能因输入变大而偷偷扩大超时 |

在provider请求发出**之前**领取D1一次性attempt permit并计入父预算；崩溃后不能证明是否发出时仍消耗该attempt，记录unknown而非假0次。响应经过实际hash/语义检查后写耐久receipt；并发/重试不能替换已选择结果。token、service token、原认证头不出现在计划、receipt、日志或导出中。

每个attempt只在已提交的请求/分片边界续接。180秒不足启动一个有完整timeout的请求时提前checkpoint；pending网络不能在释放lease后继续提交。checkpoint原子保存父累计预算和已完成请求集合，再产生可信attempt终态ACK；新attempt用新durable claim UUID恢复相同父plan，不重复已确认请求。ack丢失重取同UUID，不能新领。

排队等待不消耗active wall，但同一retrieval cohort从首次到最后响应限定24小时；超窗明确COHORT_EXPIRED并保留旧receipt，不冒称无缝同快照。即使未超窗仍保留非原子取得/修订未验证标志。用户显式新准备任务可以按相同source缓存策略复用已验证数据，但生成新根和取得时间审计。

取消/过期、旧lease的迟到响应不能发布ready或覆盖新attempt。可保留隔离证据等待清理，但不可用于研究。失败checkpoint30天后有界清理；ready dataset及已发布研究依赖无自动TTL。删除有已提交引用的数据必须保留对应冻结组件或明确拒绝，不能让回放去provider补取最新版本。

## 7. 研究准入与300目标引用

新增run入口仍在现版本化experiment/run API内，只增加明确 `admissionProfile`和 `datasetRef`；不能仅凭 `symbols.length > 50`猜profile。服务端先检查owner、ready/root、scope完全一致、字段来源/复权/日历、profile支持和预算，再生成 `admissionId/admissionRoot` 与queued job。

`admissionRoot`绑定normalized prediction config、datasetRoot、profile修订/硬预算、可用engine/DSL合同版本。dataset源与dataFingerprint真实读取后，runner在任何fit前产生更精确 `resourcePlanRoot`：全池顺序、sample/origin mask、calendar、feature AST/JSON合同根、runtime版本、完整fold/refit计划与fit尝试上界。实际超过已批准上界就失败，不能改折数让它过。

修改位置须成组接入：

1. `edge/statistical-quant/validation.mjs`增加独立profile validator；公共旧策略validator默认50不变。Python同一fixture验证准入差异。
2. jobs/quant_runs保存服务器批准的profile与datasetRef；claim只匹配新capability。runner调用 `run_capacity_research(...profile_id=...)`，硬900秒supervisor不能被heartbeat延长。
3. `runner_artifacts.py`与dataset restore需显式按已批准profile接受250k/300；保留全部17位数字、signed-zero规范化和PIT根。不能先通过旧_validate_panel失败后悄悄改MAX_SYMBOLS。
4. `edge/bundles/manifest.mjs`的25k预测/110k快照上限只在服务器批准该run profile时变为60k/250k。所有legacy stage按原上限；bundle/1格式与原bytes不变。输入snapshot采用已冻结 `snapshot_sorted_v1`。
5. `records.mjs`仅对该asset profile允许单个hedgeFits的≤300 targetIds及≤32KiB该类metadata。目标本身仍1条腿，不扩大PCA/basket symbols数组限额。逐个targetId必须引用本完整targets集合，不接受换一个300元素数组就绕过引用检查。
6. 执行拒绝在所有入口重复核验，包含已有forecastArtifactId的请求、引用复制、download后重上传和旧source路径。forecast-only产物不能通过省略profile得到旧许可。

可视化必须同时显示“选择成员数 / 有行情成员数 / 有效样本数 / 完整origin数 / 有效预测数”，保留invalid和尾部行。不得把preview分页行数叫完整预测数，或把数据ready叫模型有优势。

## 8. 导出与独立审计兼容

不要把新dataset evidence塞进旧固定19集合而仍称bundle/1。首切片继续生成旧数值snapshot＋完整forecast bundle/1，保证现数值回放可自包含；report仅增加小datasetRef/根审计。数据来源完整证据由新的**私有dataset归档**保留。

- 新dataset归档沿用strict USTAR物理要求：manifest first、regular only、固定权限与零时间、无链接/PAX/GNU、精确padding/EOF、有界body、临时目录验证后no-replace发布；顶层manifest的format明确为research_dataset/1，注册路径由该版本决定。
- 现 `extract-bundle.py` / `audit-bundle.py` 不能假装已支持新dataset归档。增加显式format dispatch与独立 `audit-dataset` 后才开放下载复现按钮；旧版本遇新格式明确UNSUPPORTED_FORMAT，不能绕过校验按旧包解压成功。
- DatasetAudit使用标准库＋磁盘SQLite/排序检查，逐片验证实际bytes/行序/重复/全部refs/单位和calendar依赖闭合；不能导入production engine来证明自己的生成结果正确。财务Decimal重算/根/available-date反例属于其独立域验证；preparedRoot字符串相同不替代验证。
- 第一版本允许用户取得两个明确命名的附件：“预测数值复现包”和“研究数据与来源证据包”。CLI显式接收两者、校验datasetRoot和实际snapshot数值根相符后才报告完整来源闭合。只有forecast包时仍可报告数值审计通过，但 `datasetEvidenceClosed:false`；不能说完整财务来源可复现。
- 将来若做单文件总归档，必须另建有声明总预算的envelope容纳两个typed manifest及组件，不能把它们直接拼进旧256MiB tar。该包装不是本切片的必需条件；两个已验证根的依赖集合仍必须完整可下载。
- 非可信本地来源即使hash正确，也不会自动升级为operator-reviewed proof。离线审计分别报告结构/数值/证据完整与信任级别；reviewer registry证书/版本若未随包保留则不能宣称已验证该信任。

金融UI的准备证据下载可使用同一组件manifest/有界导出原语；它只包含财务组件时仍叫“财务准备证据包”，不伪装含MKT的ready research dataset。

## 9. 实施顺序与自动验收门槛

1. **Ready dataset基础设施**：typed manifest/owner refs/分片导入/语义验证队列/原子发布/有界coverage/read。用完整300固定合成＋受控缺日fixture证明浏览器和Worker没有全包parse；生产功能仍关闭。
2. **旧数值保持与新调度**：runner独立capability、durable claim/recovery、dataset下载/restore、900秒隔离和profile validator同步。用50旧oracle、300固定全池rank反例/真实已有benchmark数据；不得拆池重训。
3. **结果闭环**：bundle profile守门、300 target refs、compact snapshot receipts、全部主/基线/origin索引、GUI完整遍历、两类私有附件与标准库审计。沿用已冻结数据，不必重算研究去测每次上传。
4. **真实托管准备验收**：预声明固定池/时间/字段、一次授权冷采集计划，完整记录所有attempt/无数据/错误。601项计划不能挤进forecast任务；没有权限或上游失败时保留明确阻断，不能借合成数据让绿色验收过关。
5. **金融researchBinding另验**：先现50范围内复用相同datasetRef组成，核对真实包/证明/日历/缺历史与全部Decimal证据，再考虑独立300 financial预算。prepared状态与数据集ready之间不可省略组合验证。

必须自动覆盖的阻断反例：

- 非ready根、错owner、错hash、已撤销scope、缺组件、循环ref、凭客户端trust/ready/semanticKind伪造能力均拒绝；修改单个来源值/单位策略/日历证据必须改变对应根及datasetRoot。
- 64MiB级输入每请求≤8MiB；浏览器只载manifest/当前页；Worker读一片便释放，不做整包拼接解析。大输入的单页R2读取数不随总行情行数增长。
- provider第300次前崩溃、失ACK、429、瞬态502、取消、expired lease、24小时cohort超窗、预算最后一次等：成功receipt不重读，未知attempt照计费，旧lease不可提交，partial不可ready。
- 老runner未声明dataset/profile能力不能误领新工作；新runner降级仍能收到同claim的明确终态/升级错误，不另领。旧50与旧completion/spool恢复回归必须通过。
- 错scope/字段、全无行情证券、missing日、未来availableDate、同state×symbol冲突、金融保留alias碰撞、非法单位/currency、提供假preparedRoot均明确拒绝或保留实际missing诊断。
- 300主/基线/计划逐项一致，失效模型和未成熟尾部不能丢；300 targetRefs不可引用未声明ID。主指标继续date-balanced，训练权重和当前pooled语义不变。
- 超900秒/3GiB/400MiB缓存/256MiB输出失败明确，不能减样本；投递单轮超300秒只恢复缺片，不重新fit。完整字节与索引数量须实际计量，不能用减少D1行数线性推算上传提速。
- 归档缺片/坏hash/路径穿越/EOF截断、隐藏金融依赖、旧CLI遇新format必须失败且不发布目录；只有原report包时不能伪称来源闭合。

这些门槛通过前，capability只向内部测试身份展示。目录中已有300/1000成员、已写profile名称、本地一次预测成功或供应商token存在，都不是公共完整研究服务已验收的证据。
