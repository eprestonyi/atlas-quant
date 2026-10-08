# 冻结行情＋财务输入：统一 datasetRef 实施草稿

**架构草稿，2026-10-08。HTTP/UI/托管研究路径仍为 PROPOSED，未启用、未部署。** 后续独立分支已实现一个纯离线组件/归档/重算切片，实际范围与限制见 [DATASET_COMPONENTS](DATASET_COMPONENTS.md)；本文 A–F 总方案不能被该局部实现冒充为完成。本文件不改变 v0.7 财务工作区的 researchBindingEnabled:false，也不扩大当前金融桥的 50 证券限制。

核对基线：fresh fetch 的 main **33f003a**（PR10 已合并）；[POOLED_DATASET_ADMISSION](POOLED_DATASET_ADMISSION.md)、[FINANCIAL_DATASET_BRIDGE](FINANCIAL_DATASET_BRIDGE.md)；当前 financial-ui 分支 1736f2d 及未提交的工作区 edge/UI。没有 merge、reset 或修改冻结实现。当前 HTTP/R2 验收是 localhost Miniflare 证据，不是本文新数据集能力的部署证据。

## 1. 最小纵向切片与现有缺口

建议第一 profile 为 **financial_compose_50_v1**：同 owner 的一份冻结市场输入，加 1–8 个已经准备好的财务 input/preparation。使用现有纯桥重新验证、prepare、compose，生成统一 atlas.quant.research_dataset/1 引用及完整私有证据归档。**这一 profile 没有 provider 读取入口。**

首个研究准入收窄到 schemaVersion2/statistical_quant、asset_price、fundamental＋ridge、明确 execution.enabled=false。原有因子/预测期/验证/50证券数值限制保持，不更改模型、删样本或缩减验证来适应资源。其他模型族后续用显式 admission revision 增加。

| 已有文件/函数 | 可复用事实 | 尚缺内容 |
| --- | --- | --- |
| financial_statements/dataset.py：compose_financial_dataset、FinancialDatasetResult、summarize_prepared | 重新计算包、真实 left join、根和逐状态来源；成功后登记原 DataFrame | 没有 hosted dataset 生命周期；to_dataset() 不含完整私有证据 |
| financial_statements/admission.py：assert_composed | 保留命名空间、精确对象/数值/来源检查、forecast-only | JSON 或 DataFrame.copy 不能继承准入，必须在运行 F 的进程内重新 compose |
| financial_runner/trust.py：resolve_input、calendar_scope、proof_scope | 授权 registry 的完整 payload/scope 精确匹配 | 入口绑定 financial job，需提取小型领域信任 helper，不能伪造 job 来复用 |
| financial_runner/publication.py：compute_publication | 真实 prepare、完整事件/依赖/区间/覆盖、分片预算 | prepared financial publication 不是含行情的 research dataset |
| edge/financial 的 registry/publications/integrity/read-model | owner、字节回执、租约、原子提交、完整依赖引用 | 无行情组件、组合数据集根、run→dataset 关系 |
| runner.py：prepare_job/run_job/_child_entry | 隔离进程、耐久领取及交付 | 当前来源仅 demo/upload/Tushare，不能给 job.dataset 多塞字段就绕过 financial 准入 |
| runner_artifacts.py：freeze_input/restore_input | 精确 float snapshot、数值指纹 | 当前仅 schemaVersion1/research_input_v1；restore_input 不能从金融 JSON 建立可信对象 |
| bundle.py：build_bundle/BundleReader；scripts/bundle_audit.py | 既有 forecast/report/coverage/snapshot 分片 | 未建立财务来源闭包；旧审计器只认原 snapshot discriminator |
| edge/bundles/archive.mjs；scripts/bundle_archive.py | 有界 USTAR、严格 header/EOF、no-replace 解包 | 固定 bundle/1 路径及语义，不能把 dataset 改文件名就当旧包 |

本文的“重放”是**离线恢复来源、prepare/compose 和审计**，不是执行复用。execute_forecasts 继续返回 FINANCIAL_REPLAY_NOT_AVAILABLE；初次执行、legacy 和 stat_arb 入口也继续拒绝金融数据。

## 2. 先得到真正冻结的 marketDatasetRef

现 jobs.dataset_key 是临时上传对象，完成后会删除，不能直接当长期 market ref。最小 bootstrap 只实现 market_upload：

1. CLI 接收已有的、至多 24 MiB 的 {schemaVersion:1,rows,provenance} 冻结行情。
2. 明确来源为用户声明或已有规范化来源，保留原字节 SHA、复权/单位/日历/缺行及转换版本。
3. CLI 产生 typed market manifest/parts；浏览器逐片上传，不对整个行情 JSON 调用 file.text()/JSON.parse。
4. Python 语义校验后才发布 ready market dataset；客户端 ready、semanticKind、R2 key、URL、预填 model_fin_* 或金融根一律不能替代它。
5. 已有 owner forecast bundle 的 snapshot 转换可以作为后续 adapter：验证原包并读取已提交 snapshot，绝不重新读取 provider。本切片不以该 adapter 已实现为前提。

规范化数据缺少原 wire receipts 时，只能标 wireEvidence:unavailable；不能从行情价格恢复上游响应、取得时间或原始数字 token。日历 sessions 一致也不证明官方性：使用完整冻结日历及受控 registry，不凭上传的 kind:official 自证。

**不隐式排序旧输入。** 当前 marketRoot 哈希的是整个市场对象。若 bootstrap 需要按 (trade_date,ts_code) 排序，必须生成新的、明确版本的 normalized market component/root，并保留原输入 hash。组成、归档和恢复都使用同一份 normalized 字节。

## 3. 唯一引用与 typed 证据闭包

沿用 pooled 合同的唯一研究引用：

~~~json
{
  "datasetId": "server-owner-scoped-uuid",
  "datasetRoot": "<64hex>",
  "format": "atlas.quant.research_dataset",
  "version": 1
}
~~~

datasetRoot = SHA256(完整 canonical manifest 原字节)，manifest 不含自身 root，也不把 owner UUID 当数据身份。D1 owner/id 负责授权；相同内容 hash 不授予跨 owner 访问。

### 3.1 注册组件

最多 32 个 component roots，组件依赖深度最多三条边。按完整根共享相同组件；不同语义对象不能仅凭某个数值相等而合并。

| proposed type/version | 完整内容 | 依赖 |
| --- | --- | --- |
| market_dataset/1 | 精确 canonical 行情对象、来源/复权/单位/日历；payload 根保持既有 marketRoot | registry_evidence 中的日历证据 |
| financial_input/1 | 原 canonical financial-input 包，保留 inputRoot/packRoot | registry_evidence |
| financial_prepared/1 | 完整 panel/provenance/events/assignments/coverage；依赖可拆片，但必须恢复原事件 ID/lineageHash | 对应金融包＋registry |
| registry_evidence/1 | 完整 calendar/proof payload、版本/scope/审核来源，逐项 hash | 无 |
| research_rows/1 | 组成后的精确行和完整 provenance，保留金融根及 available_date | market＋prepared |
| dataset_schema/1 | 注册字段、单位、可用日列、公式/来源版本 | research_rows |
| dataset_coverage/1 | 成员/行情/状态实际覆盖、缺失理由、观察日与生效日 | research_rows＋prepared |

把多条 proof 记录合成一个 registry_evidence 组件，而不是每条 proof 占一个顶层组件。单 registry ≤256 KiB、合计 ≤32 MiB，同时计入更严格的 64 MiB dataset 父预算。声明仍保留在原 financial package 中，始终 verified:false。

组件 descriptor 的 root 与领域 root 不混同。componentRoot 哈希 descriptor（排除自身 root）；payloadSha256 哈希实际 payload。financial package 的完整字节 SHA **不等于**排除 packRoot 字段计算出的领域 packRoot。

~~~json
{
  "componentId": "financialInput0",
  "type": "financial_input",
  "version": 1,
  "componentRoot": "<64hex>",
  "semanticRoots": {"inputRoot":"<64hex>","packRoot":"<64hex>"},
  "encoding": "raw_bytes",
  "payloadSha256": "<64hex>",
  "byteLength": 1234,
  "dependencies": ["<registry-component-root>"],
  "parts": [{"ordinal":0,"byteLength":1234,"sha256":"<64hex>"}]
}
~~~

仅两个 encoding：raw_bytes 按序拼回完整 bounded 文档；json_records 按数组分片完整恢复一个 canonical array。每个 type 固定允许的编码/集合布局，不支持任意指针、literal 程序、外部 URL、路径或 pickle。正式实现先提供严格 JSON schema 及 JS/Python 共用正反例，上例目前不是可提交 API。

数值canonical版本也须注册：金融package/prepared使用现financial JSON规则，原始1.0/-0.0等字节不可先经JS Number再序列化。不要直接用bundle.encode（会把整数float转int）重新hash金融文档。raw_bytes按原字节SHA，Python领域decoder核对原canonical；json_records的重构与preparedRoot沿用该组件规定的financial编码。manifest descriptor只用受限整数/字符串元数据，跨语言fixture必须覆盖这些数字边界。

### 3.2 无环的根与研究指纹

- marketRoot、inputRoot、packRoot、preparedRoot、calendarRoot、financialDatasetRoot 均保持现定义；datasetRoot 最后包住这些组件和 scope/schema/coverage。
- 不向 compose 返回的 provenance 临时加入 datasetRoot/owner ID：它已经进入 process-local admission 的完整 provenance hash，修改会使准入失效。
- **dataSha256 是研究相关的实际数值指纹，不是 datasetRoot 的别名。** _prepare_data 使用该策略实际涉及的数值/availability 列、日历及金融 commitment；同一 ready dataset 可供不同研究使用。
- 因此 dataFingerprint、predictionConfigHash、resourcePlanRoot 保存到不可变的 run admission receipt，关联 datasetRoot；不要强制它们是可复用 dataset manifest 的固定字段。这是对 pooled 草稿示例的具体细化，须两端先统一。
- 数值运算完成后可在 report 外层加小型 researchDataset link；不要改 forecasts 身份或已准入 data/provenance。

## 4. 可实现的纯计算接口与流程

新增 proposed 模块 research_dataset/compose.py：

~~~python
compose_dataset_components(
    scope, market_bytes, financial_packages, authorized_registry,
    source_preparation_roots, write_part, *, profile,
) -> manifest
~~~

1. 先验证 descriptor 数量、总预算、无环闭包，再逐片验证实际 SHA/长度。
2. 提取并复用精确 registry 信任匹配；HTTP 从不暴露 trusted_unit_proofs。不得把 proof 的 hash 正确当成审核授权。
3. 包的 stateIds/symbols/起止日期必须与原 preparation 完全对应。改变状态选择先创建明确 financial revision，不后台改包。
4. 调用现 compose_financial_dataset；只使用它成功返回的 FinancialDatasetResult。已有 preparation 只是要核对的参考，不能跳过重新 prepare。
5. 每包重算 preparedRoot 与所引用 committed preparation 一致，否则阻断，不覆盖旧准备。
6. 增量序列化来源闭包与派生行，计入共同预算；构建 typed descriptor/manifest，提交新 publication。
7. Edge 验证实际字节、布局、owner、全部引用和预算，再用一个 D1 batch 事务发布 ready、依赖关系及 job 终态。

新增 proposed 模块 research_dataset/restore.py：

~~~python
restore_dataset_for_research(
    strategy, manifest_bytes, read_part, authorized_registry,
    *, expected_dataset_root, profile,
) -> FinancialDatasetResult
~~~

它在**运行 F 的同一 child process**内重复校验并重新 compose，再与归档 research_rows 的逐值/缺失/provenance/financialDatasetRoot 比较。这样产生新的有效弱引用准入，不序列化凭证、不调用私有登记 hook 伪造准入。

随后执行原 pre-fit sample 检查；标签/基本面覆盖不足必须在 fit 前明确失败。dataset ready 只说明来源完整和可读，不能承诺研究有足够样本。全 missing 的财务组成可以保留诊断，但不能启用 fundamental 运行。

行情与财务日历可以有不同证据根，均须保留；首 profile 要求区间内 sessions 精确一致且日历授权已解析，不凭股票后缀推断交易所等价。

## 5. Edge/API 与队列落点

均为 proposed 路径，相对 /quant/api。现 owner、CSRF、限流保持。最小组成请求：

~~~json
{
  "requestId": "uuid",
  "mode": "compose",
  "admissionProfile": "financial_compose_50_v1",
  "marketDatasetRef": {
    "datasetId":"uuid","datasetRoot":"<64hex>",
    "format":"atlas.quant.research_dataset","version":1
  },
  "financialInputs": [{
    "inputId":"uuid","preparationId":"uuid",
    "inputRoot":"<64hex>","packRoot":"<64hex>",
    "preparedRoot":"<64hex>","calendarRoot":"<64hex>",
    "stateIds":["model_fin_cash_asset_share"]
  }]
}
~~~

scope 由 frozen market 推导；所有客户端 hash 都与同 owner 的存储事实比较，不能当新事实写入。

| API | proposed 行为 |
| --- | --- |
| POST /dataset-preparations | 只允许 market_upload / compose；owner+requestId+bodyHash 幂等；返回 preparationId/status/planRoot/limits。没有 market_provider |
| PUT /dataset-preparations/:id/parts/:componentId/:ordinal | upload stage 绑定的 bounded bytes；相同内容重试成功，冲突拒绝 |
| POST .../upload-complete | 缺片检查后入语义校验队列，不直接 ready |
| GET /dataset-preparations/:id；POST .../cancel | 小状态与原因，取消/过期旧 lease 不可发布 |
| GET /datasets/:id?datasetRoot=... | immutable summary/scope/columns/coverage；researchAdmission 与 dataset ready 分开 |
| GET /datasets/:id/manifest?... | 原 canonical manifest bytes＋SHA |
| GET /datasets/:id/coverage?... | root 固定、≤100项/256 KiB；不扫全量行情 |
| GET /datasets/:id/download?... | 完整私有 USTAR direct attachment，identity/no-transform/固定长度，浏览器不全量 fetch 成 Blob |
| 现 POST /statistical-quant/experiments/:id/runs | 增加互斥 {version,dataSource:"ready_dataset",datasetRef,admissionProfile}；不能同时传 dataset/URL/provider 参数 |
| 现 execution/copy/legacy 入口 | enqueue 与 restore 双层金融 forecast-only 拒绝；复制研究不清除来源限制 |

新队列能力 research-dataset/1:financial_compose_50_v1，与 financial-input/v1 分开。不要扩旧 financial_jobs.kind 让旧 consumer 误领。可提取共用 durable claim/spool 机械逻辑，但必须保留旧行为和回归。

内部 proposed runner 路由复用 operator Bearer，但采用独立 namespace；GET 用 lease header，不把秘密放URL：

| 内部路由 | 契约 |
| --- | --- |
| POST /runner/datasets/claim | {requestId,capability,engineVersion}；耐久同UUID返回同job/lease或原终态，小claim不携带数据 |
| GET /runner/datasets/jobs/:id/input | job/lease绑定的来源descriptors、冻结plan/预算；每个part只给注册逻辑ID，不给任意外部URL |
| GET /runner/datasets/jobs/:id/components/:componentId/parts/:ordinal | 按该job已授权闭包读取实际片，校验SHA/length；拒绝跨job/owner |
| POST /runner/datasets/heartbeat / fail | heartbeat不延长父deadline；终态可幂等ACK，不能复活失效lease |
| POST /runner/datasets/publications/begin | canonical manifest原字节或manifestText＋jobId/lease；返回server stageId及missing parts |
| PUT /runner/datasets/publications/:stageId/parts/:componentId/:ordinal | 原始bytes，job/lease/stage三者固定；相同内容重试、不同内容409 |
| POST /runner/datasets/complete | {jobId,leaseToken,stageId,datasetRoot}；完整校验及D1原子发布；同终态同内容ACK |
| GET /runner/research-datasets/:jobId/manifest 及 /components/:componentId/parts/:ordinal | forecast job的live lease＋quant_run_datasets关系；恢复ready闭包，不能绕过run的owner/root |

现 forecast claim 增加明确 datasetFormats:[atlas.quant.research_dataset/1] 与 snapshotFormats:[research_input_financial_v1] 能力。两个能力均真实支持才可领取该profile；只有atlas.quant.bundle/1声明不够。complete也核对job已批准profile及新snapshot版本，不接受客户端自行选金融格式。

新增迁移编号在实施时接 v0.7 的0006之后分配，避免现在占号。最少需要：

- quant_dataset_preparations：owner/request ID+hash、固定来源 refs、planRoot、状态和结果。
- quant_dataset_claims：耐久 request→job/lease/终态回执，终态 UUID 不回收为新领取。
- quant_dataset_publications/parts：owner/job/fence、manifest SHA、实际 bytes/hash/边界回执。
- quant_research_datasets/components：ready manifest、typed refs、有限摘要；原数据不进 D1。
- quant_run_datasets：run/dataset/profile，以及实际 numerical/admission/resource roots。
- quant_dataset_dependencies：保护所有 committed 来源 publication/registry/component；删除请求须拒绝或保留 pinned 内容。

R2 stage 可先存在，但提交前用户/runner都不可读。begin与finalize重新验证引用仍获授权；提交后固定保存当时完整registry字节与审核版本，不在重放时换成最新proof。后续撤销可阻止新admission，但不得静默改写历史数据根或把历史证据称为当前再认证。最后一次事务同时写 ready、依赖和 job 完成；SQL失败全部回滚。已引用对象的物理复用不能逃避“完整可导出闭包”的64MiB计数。

dataset、financial prepare、forecast 共用一个配置在私有 runtime 的机器级 compute.lock（proposed runtime_slots.py），不能各用服务锁而并发开大进程。先恢复未知领取/未ACK交付，再领新工作。计算中断且无完整 manifest 时保留失败，不自动重算；已完成分片交付只补缺片。

## 6. 64 MiB 不是整包 HTTP body，也不是 RSS 保证

本 profile 比未来 pooled MKT300 的256MiB更严格，不借其容量许可。

| 边界 | proposed 硬限 |
| --- | --- |
| 证券 / observed market rows | 1–50 /110000，旧行情校验不变 |
| 财务包数量 / aggregate package bytes | 1–8 /24MiB |
| joined research output | 24MiB，桥默认不变 |
| 整个 typed dataset closure | 64MiB：source、registry、prepared、derived 全计；同一组件导出一次 |
| bridge共同准备预算 | 64MiB保守序列化展开；先为外部registry/closure开销预留，再传剩余额度 |
| manifest / summary/page | 256KiB /256KiB |
| parts | 单片512KiB，最终≤256片；完整JSON记录≤500/片，单记录过大直接失败 |
| registry | 单条256KiB、合计32MiB，并计入64MiB父预算 |
| components / dependency depth | 32 /3 |
| compose任务 / dataset研究 | 600秒无fit /900秒含下载、恢复、compose、plan、fit、序列化 |
| forecast输出bundle | 旧256MiB/256片独立限制；不能因此扩大dataset来源 |

现 financial publication 可有512片；新dataset可以确定性重打包为≤256片而保留逻辑根，不能修改旧publication。重打包/缓存/空间纳入预算，装不下明确失败。

Worker一次只解码一片；raw financial package只在有24MiB上限的Python进程内重组。finalize先取有限receipts，再每片R2只读一次；实际记录≤256次R2＋有限D1读，不按组件数重复扫全文。下载取消后不继续预读。

独立测峰值RSS、总时间与磁盘余量；序列化64MiB不意味着Python只用64MiB。不得因对象展开超限而删依赖、删股票或提高预算。下游恢复仍在研究900秒内，先花600秒准备不让一次研究多获得600秒。

## 7. 旧 bundle 兼容及完整私有重放

### 7.1 新dataset格式，保留旧result transport

dataset archive使用新 atlas.quant.research_dataset/1，绝不向 bundle/1 填假forecast/coverage。

预测结果可继续使用 bundle/1 的现19集合和四个forecast documents。**不向旧版本加financial documents/collections。** 完整来源闭包使用第二份独立dataset附件。

但是financial数值snapshot含义必须显式区分（proposed）：

- schemaVersion:2、fingerprintVersion:research_input_financial_v1；
- joined rows/provenance、source/data fingerprints；
- 小datasetRef和exact financial commitment，无凭据；
- sourceEvidenceClosure:separate_research_dataset_v1。

其金融dataFingerprint仍是现桥的数值＋金融根算法，不把datasetRoot替换进去。旧snapshot version1保持原字节/行为。新capability的completion/reader显式接新discriminator；旧审计器按现限制拒绝它，不能把该失败改成跳过校验。

当前 BundleReader.verify_integrity 证明的是传输/预测覆盖，不是financial信任。新回读/CLI应分别报告 transportVerified、numericalSnapshotVerified、datasetEvidenceClosed。旧restore_input不接新金融JSON；新dispatcher从typed组件重新compose后比较数值snapshot和预测dataFingerprint，仍不放开execute_forecasts。

### 7.2 两个私有附件与CLI

第一版给两个附件：“预测数值包”和“研究数据与来源证据包”。后者的注册USTAR路径是 manifest.json 首个，其后 parts/<componentId>/<ordinal>.bin，固定manifest顺序。raw/json编码由manifest决定，扩展名不允许执行任意内容。

严格 regular file、0600、uid/gid/mtime0、512-byte padding、恰好两块EOF；拒绝链接/PAX/GNU/目录/重复路径/越界路径/尾部额外数据。私有临时目录验证成功后no-replace发布。

现 tarHeader 只认旧chunks路径。应抽小型“已验证entry plan→USTAR stream/header”原语，保持两个独立格式validator；不能全局放宽旧白名单或改成通用tar extraction。

proposed CLI：

- scripts/pack-dataset.py：离线market转换/组件打包，无token/config参数。
- scripts/extract-dataset.py＋dataset_archive.py：严格typed USTAR导入。
- scripts/audit-dataset.py＋dataset_audit.py：stdlib结构hash、闭包、顺序/数量/refs/commitment审计，不导入production engine。
- scripts/recompose-dataset.py：独立阶段调用真实financial core重新算值、missing/coverage/lineage；0provider/fit，信任策略由明确外部输入提供。
- audit-bundle.py增加 --dataset；验证financial snapshot/dataFingerprint与完整dataset匹配。没提供时不得称完整财务来源复现。

registry hash正确不等于审核者可信。导出完整reviewer/version/scope，但离线未知reviewer只能报 trustStatus:unverified；声明可按明确allow_declared恢复，reviewed proof须外部pin/allowlist后才能trusted解码。归档本身不能赋予该权限。

当前DocumentUnitBinding含文档hash与坐标，registry并不包含原PDF字节。归档必须披露originalDocumentIncluded/available；缺PDF时仍可做有边界的算术重算，但不能声称归档自身认证原披露。路径字符串不算完整来源文件。

## 8. 实施顺序、文件与职责

下列均 proposed；现有集成点见第1节。

| 切片 | 新文件 / 窄改动 | 完成门槛 |
| --- | --- | --- |
| A typed codec/archive | research_dataset/{profile,manifest,codec,reader}.py；scripts/{dataset_archive,dataset_audit,extract-dataset,audit-dataset}.py | JS/Python共享schema正反例，bounded DAG、完整hash、strict tar；无HTTP/fit |
| B 纯组成/恢复 | research_dataset/{compose,restore}.py；从financial_runner/trust.py提取领域helper；复用financial_statements/dataset.py | 新进程真实recompose、原preparedRoot比较、完整数值/来源一致；旧financial测试不变 |
| C Edge lifecycle | edge/datasets/{profile,manifest,api,jobs,publications,integrity,read-model,archive}.mjs；加法迁移/worker路由 | 真实D1/R2 owner/fence/rollback/失ACK/保留引用测试，旧队列不受影响 |
| D consumer | dataset_runner/{client,spool,service}.py；runtime_slots.py | 无provider访问，claim耐久、下载hash、加密片恢复、总deadline、共享单slot |
| E run binding | statistical-quant/{api,validation,persistence}.mjs、run关系；runner的prepare_job/run_job/_child_entry；runner_artifacts；bundle completion/CLI snapshot dispatch | 老runner不误领；同child恢复准入；全部执行入口拒绝；两附件根闭合 |
| F UI | financial compose入口、dataset状态/选择页、study source selector | 显示实际scope/覆盖/假设；只提交ready ref；样本准入单独显示；私有流式下载 |

v0.7工作区发布不依赖A–F完成。下一分支统一接main再实施，不把这些proposed修改混进已冻结release。

## 9. 验收与零重复provider计划

**已有冻结证据足够验证传输和组成；不重放六次财报、一笔calendar或四笔market读取。** 本profile API无providerGrant、endpoint、token或attempt许可。

1. **短合成链**：复用实际strict→declared工作区包，配一份同日期明确synthetic行情；market→compose→ready→私有tar→fresh-process恢复。strict6缺失；declared5有效1缺失，比例0.2且unitVerified:false。六日案例须保持“F样本不足”，不能缩短训练门槛。
2. **长合成数值链**：另预先固定足够历史的fixture和策略，16状态经真实prepare，缺历史照实；一次小forecast计算保留所有origin/baseline/tail，再做恢复fingerprint比较。不能把短包悄悄变成长包。
3. **真实冻结两公司**：复用已有私有行情、package、calendar、精确审定proof；通过operator registry绑定；只recompose并对已有真实报告的根/数值指纹，不必再fit。单年报不造TTM，NO_VALIDATED_FORECAST_EDGE不是调区间理由。
4. **准入反例**：裸model_fin_*、伪semanticKind、copy/JSON、改值/来源、错proof/calendar、cross-owner、重叠state×symbol、未来available_date、隐藏live binding均拒绝；policy改变即使值相同也改变对应根。
5. **兼容**：原v1 snapshot/bundle/CLI/执行fixture hash不变；金融新snapshot缺dataset不能完整重放；老runner跳过新capability并仍领旧任务。
6. **持久性**：PUT中取消、验证前后lease过期、claim/begin/chunk/complete丢ACK、SQL trigger abort、坏R2及registry状态变化；无半ready，交付恢复不重算/取数。
7. **资源**：64MiB及+1byte、256/257片、32/33组件、循环/超深图、超大片依赖、真实读次数/RSS/磁盘/deadline。任何失败都不能丢股票、invalid/tail或依赖。
8. **独立审计**：截断/额外/乱序文件、坏root和重新hash后的语义篡改；stdlib完整性与core数值重算分开报告；HTTP下载、consumer发送capture、R2回读不混称。
9. **UI口径**：来源闭合、状态可用、训练样本充足、预测完成、具有优势五种状态分开。无优势是有效结果，不能当取数失败。

financial researchBindingEnabled:true 的gate：A–F完成、加法迁移/回滚演练、私有归档fresh-process恢复、真实认证HTTP读回、一次完整合成forecast链。公开范围仍受逐证券/报告期/字段证据约束；没有全局单位或历史版本认证承诺。
