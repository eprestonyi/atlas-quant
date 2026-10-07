# Statistical Quant frontend 0.4

Native ESM entry `main.js` registers reusable editors and a separate `quant-workspace/`. This is an implementation record; browser acceptance and numerical validation are separate.

## New workspace

- Default schema 2 / `research.mode=statistical_quant`; default source is real Tushare, and source availability loading is different from an unavailable service. No automatic synthetic substitution.
- Page-state preservation keeps same-page keyboard focus across full DOM renders, focuses headings on route changes, restores each visited page’s scroll/expanded details, and keeps the skip-navigation link out of the router. Loading failures have explicit retry states and do not masquerade as empty results.
- Eight independent routes: `#quant/universe`, `state`, `target`, `model`, `validation`, `risk`, `execution`, `report`. `#quant/studio/{step}` expands configuration; code/AI and community are separate pages. The shared code editor is embedded without the historical research header; code-project saving is separate from research saving. Desktop vertical navigation, mobile step selector, collapsible protocol summary. Automatic desktop expansion collapses at the narrow breakpoint; only real user clicks override it. Run-report routes select step 8 and link to Studio reports.
- Paged complete universe discovery, AND/OR/subtraction and explicit single-stock selection, with current constituent limitations. No implicit first-20 subset. New mode permits one asset and up to 50 selected assets. Metadata persisted according to the exact versioned universe contract. Snapshot unknown fields are rejected visibly; explicit re-resolution creates the canonical three-key snapshot. Direct manual/imported members do not invent unresolved subset metadata. Normalization deep-copies nested saved metadata.
- Searchable, paged factor/state/module/recipe discovery; original availability is preserved. Usable factor cards and typed module cards have native drag/drop plus ordinary buttons. Missing-mapping factors cannot drag. Input symbols are feature encodings, not position instructions.
- Asset / OLS pair / PCA / fixed-quantity targets have explicit members. Target selection and step validation are separate from free sidebar navigation.
- Model family, estimator, mature training window and nested time validation are explicit. Conditional entry and future forecasts are distinct from current state and execution.
- Risk parameters cover gross, net, per-symbol weight, volatility target and selected-factor exposure limits. The limits are selected-universe prior-close z-score exposures, not asserted market beta neutrality. Execution-only overrides do not refit forecasts.
- New private experimental save, optimistic version update, reload, copy, run/poll, source labeling and comparison APIs. Actual workspace counts are separate from catalog counts. Saved comparisons and manifests are paged and readable after refresh.
- Existing universe/DSL/PIT mapping and browser Python/AI code editors are reused; new-route guards prevent those components from routing schema-2 drafts to historical pages. Old stored drafts and saved records retain historical semantics.

## Reports

- Forecast-first ledger includes origin/entry/target dates, current state P, expected entry, future V, P−V gap, expected remaining change, realized values, forecast error and validity. All/most-recent, target, status and query filtering are paged.
- Single-record details use a time sequence, numerical definitions, frozen-quantity leg table and human-readable state. Identity fields and raw JSON are folded audit details.
- Joint normalized forecast diagnostics, no-change comparison, independently fitted state-only feature ablation, failed/negative incremental findings and per-target price-unit errors remain visible. Newly declared date-equal metrics show their weighting; feature-ablation coverage distinguishes matched, unmatched and unavailable predictions, and keeps metric/interval bias-sign conventions explicit.
- Aggregate date-clustered block-bootstrap intervals are labeled historical mean-loss/bias intervals, never individual future-price confidence bands. Missing intervals remain unavailable.
- Frozen target definitions, fit chronology, training preprocessing and state-effect audits use actual engine fields. Complete fold evidence and factor-ablation baselines are read from immutable `forecasts.diagnostics`, while compact report envelopes remain supported.
- Execution is a separate tab; forecast-only output does not fabricate equity or trades. Real fills reference forecastId. Reuse sends only the forecastArtifactId and execution/portfolio/cost overrides.
- Structured risk limits, filtered risk/blocked-exit dates, actual exposure and factor details precede raw audit fields.
- Report rendering must never mutate server-owned report/artifact arrays. Filtering copies before sorting. Export freshly reads persisted `/runs/:id/export`; the immutable forecast download is separately available.

## Verification commands

`node web/tests/statistical-dom.mjs` runs actual DOM input, click and native DataTransfer event handlers with explicit API doubles: eight pages, numeric-input retention, stored version identity, exact pool-card route regression, factor drop plus unavailable-factor restriction, incomplete-target next-step blocking, empty numeric validation, no-ID control focus, keyboard-operable add-button equivalence, return scroll/expanded-state restoration, skip-link report preservation, and failed catalog/report retry recovery. This is not visual browser evidence.

`ATLAS_QUANT_TEST_URL=http://127.0.0.1:PORT node web/tests/statistical-api.mjs` uses a real local API. It verifies paged modules and recipes, resolves the full CSI1000 without truncation, explicitly selects two members, verifies direct single-asset saving and unknown snapshot rejection, saves/reloads/updates/copies/exports a schema-2 experiment and archives only its own test records. No provider request or claimed numerical run.

`node web/tests/statistical-report-readback.mjs /absolute/private/computed-report.json` reads an existing real engine report. It simulates a compact envelope with complete diagnostics only in the artifact, exercises six tabs, filters, detail buttons and execution overrides (including selected-factor limits) against a recursively frozen input, then checks the entire report and artifact serialize identically. It does not fetch data or recompute results. Private market data never becomes a source fixture.

During implementation, readback covered the genuine Tushare pair report with 108 forecasts/112 trades and the explicitly synthetic risk/factor-increment report with 552 forecasts/156 trades. The latter has negative factor increment and remains shown. An additional actually computed synthetic missing-model report has 552 origins, 534 matured labels in both paths, zero valid full-model forecasts, 534 valid baseline forecasts, no matched forecasts and no trades: the UI shows coverage and an unavailable increment, never zero improvement. Invalid fits retain explicit missing-model explanations. Full browser workflows, visual screenshots, 390px layout, execution reference equality and real provider execution are accepted separately by the integrating parent agent.

Legacy `research-flow.mjs` and `research-dom.mjs` bundle the imported app entry with the locked esbuild dependency before VM/jsdom evaluation, preserving all historical assertions as the production source adopts ESM.

## Deliberate boundaries

Historical residual rules and old long-only sorting are historical tools, not the default new workflow. Market making, derivatives and structural replication are clearly planned independent workspaces. Catalog size is not observed data coverage or a tested strategy count. Theoretical shorts are not verified securities-borrow availability. Browser Python exploration is isolated and is not arbitrary server execution.

## 本轮验收记录与证据边界

本节按 2026-10-08 本轮记录编写。每次下述功能修复均完成回归后再交 root 冻结构建；不把不同候选构建的证据混作最终版本。以下三个层次互不替代：本地 DOM/API 检查、已有真实浏览器操作、最终构建的浏览器复验。私有行情和完整报告不进入公开测试夹具。

| 项目 | 本轮已经完成 | 最终构建仍需 root 确认 |
| --- | --- | --- |
| 八步编辑与路由 | DOM 实际 input/change/click；输入保留、下一步校验、保存身份、切页焦点与返回状态通过 | 在当前构建用键盘连续走一遍，确认无焦点丢失、控制台错误 |
| 完整集合与明确成员 | 真实本地 HTTP 17 次：中证 1000 全部 1000 个成员；明确两成员、直接单资产、保存/读回/复制/导出；拒绝未知快照字段 | root 曾在浏览器完成沪深 300 ∩ 银行 24 只，再减北京剩 14 只，明确选择两成员；排队快照修复后的当前版本运行状态单独核对 |
| 因子加入 | DOM 原生 DataTransfer 与普通“加入”按钮等价；root 已真实原生拖入 5 日动量并保存 v2 | 不把随后因快照结构失败的任务记为成功计算；无需为截图重复请求供应商 |
| 预测与独立执行 | 只读真实 Tushare 报告 108 预测/112 交易腿；root 较早 0.4 候选已在浏览器完成纯预测 108 行/102 成熟，以及同产物独立执行 148 交易腿 | 这些是不同产物，不能混作同一报告；最终构建复核 artifact 身份及导出顺序 |
| 比较创建与读回 | DOM 控件创建比较、生成 URL、清空内存后按 URL GET 读回、导出链接检查通过；root 已真实创建并完整刷新读回一份比较 | root 实测发现首帧误示空列表，现已修复并通过延迟响应测试；最终构建复验首帧和下载内容 |
| 手机 390×844 | root 已观察到页面没有横向溢出；该次同时发现摘要默认展开和报告步骤错误，现已修复并通过响应式状态 DOM 回归 | 在最终构建重新核对折叠摘要、手动展开、报告第 8 步及 Studio 入口 |
| Studio 代码区 | DOM 确认一个 H1、代码项目保存存在、旧研究保存/运行/名称控件不存在 | 最终构建 Python 运行、规则检查、AI 审阅手动应用和项目保存读回 |
| 预测不可变性 | 三份真实计算的现存报告递归冻结后走六页、筛选、明细与执行改参，整个报告和 forecast 序列化不变；真实数据及两类明确合成报告分别记录 | 浏览器下载与服务端完整产物逐条/规范 JSON 比较；不得用页面排列替代原始序列 |

上述“真实计算的合成报告”表示实际运行数值引擎，但输入是明确标注的合成数据；不构成市场表现证据。缺模型例中未来标签已经成熟，模型仍可能无效。最终上线、线上身份及长期服务可用性不由本前端测试宣布。

## 可复现浏览器验收步骤

入口以 root 当前预览地址为准，例如 `http://127.0.0.1:8904/quant/`。使用同一浏览器私有会话，优先复用已有预测和执行记录，不为验收重复抓取行情。记录地址、构建标识、视口、控制台错误和对应产物 ID；截图本身不能证明 API 写入或数值计算成功。

### 比较创建、刷新与导出

1. 从左侧“研究比较”进入 `#quant/compare`，点击“刷新产物”。等待加载完成。
2. 选择“预测产物与误差”或“同预测的独立执行”。至少勾选两项、最多八项；未完成的执行记录不可选。只有一项时“保存并生成比较”应禁用。
3. 点击“保存并生成比较”。应跳转 `#quant/compare/<comparisonId>`，表格按类型显示预测误差或净收益/回撤/成本。
4. 查看可比性提示。数据、目标或预测身份不同的结果只能并列展示；不能因 UI 成功生成表格就宣布受控比较。
5. 完整刷新浏览器，再从“已保存的比较”打开同一项。核对 ID、成员、类型、可比性和指标未改变。
6. 点击“导出比较”，读取 JSON 的 `kind`、`members`、`controlledComparison` 与各项指标，确认不是只下载链接页面。

自动回归：`node web/tests/statistical-dom.mjs` 中 `comparisonCreateAndReadback`。这是 DOM + API 替身证据，最终上述步骤仍需要真实浏览器/真实私有记录。

### 同一预测的独立执行

1. 打开已有 `#runs/<runId>`，记录顶部 `forecastArtifactId`，进入“独立执行”。纯预测研究应明确没有持仓、交易或净值。
2. 展开“复用这份预测，创建新的执行方案”。只调整成本、执行方向、仓位与风险中的一个已支持参数；例如滑点。原始预测和模型配置不在这个表单内。
3. 点击“复用预测运行执行”，应进入一个新的运行记录。等待完成后核对原 `forecastArtifactId`、预测行数及“未重新拟合”证据。
4. 查看交易引用的 `forecastId`、真实成本、风险超限/受限退出日期。零交易是合法结果，不得为了使图表出现而更改参数重跑。
5. 下载两个完整预测产物，确认内容/身份不变；完整执行报告的执行配置和结果应记录本次差异。若要比较，选择该产物下的两份已完成执行。

自动回归：三类 `statistical-report-readback.mjs` 输入覆盖纯报告阅读、成本改参、因子暴露上限改参以及不可变性。实际任务提交/持久化和浏览器下载是独立验收项。

### 窄屏摘要与报告定位

1. 使用没有手动点击摘要的页面，在桌面宽度至少 1250px 打开 `#quant/state`，摘要自动展开。
2. 切到 390×844，摘要应自动折叠；切下一页再返回仍应折叠。首次直接以窄屏访问同样默认折叠。
3. 用手指或键盘手动展开“当前研究协议”，切页再返回，应保留这次真实选择。本轮保留范围是当前页面会话，不宣称跨浏览器持久化。
4. 打开 `#runs/<runId>`，顶部步骤选择器应显示“8. 运行与报告”；“Quant Studio”链接应指向 `#quant/studio/report`，不能指回股票池。
5. 确认文档没有横向溢出。宽表可以在其自身容器内横滚，不应撑宽整页。切页焦点仍到标题，但不出现覆盖整个标题的巨大边框。

### Studio 单标题及独立代码项目

1. 打开 `#quant/studio/code`。正文只有一个“代码与 AI 审阅”H1，不应出现旧 `QUANT STUDIO / DATA`、第二套研究名称、“保存策略”或“运行研究”。
2. 切换 Factor DSL/Python/策略 JSON。代码项目的“保存代码项目/保存新版本”与“新草稿”属于代码项目；JSON 配置需明确检查应用，不等于运行任意脚本。
3. Python 可用无行情的小例子验收：`import math` 后设置 `result = {"sqrt": math.sqrt(9)}`。输出应为 3；该步骤不请求供应商。
4. 用规则检查审阅一段代码，保存代码项目，重新从项目列表打开，核对语言、内容与版本。AI 审阅是另一个真实服务调用；返回建议必须主动应用，不能静默覆盖代码。
5. 可视样式与 Python/AI 实际服务成功应分别记录，不用“有编辑器”证明运行时已工作。

### 键盘、加载失败与长页返回

1. 只用 Tab/Shift+Tab/Enter 走步骤与“加入”按钮，确认原生拖动存在等价按钮操作；不可用模块按钮保持禁用并解释条件。
2. 在没有 id 的因子角色或风险复选框上修改，触发重绘后焦点应留在对应控件。删除该控件后允许转到页面标题。
3. 在长页面展开高级设置并滚动，访问另一页再返回，展开状态与滚动位置恢复。输入内容不得被背景刷新覆盖。
4. “跳转到主要内容”只改变焦点，不能改路由或清空当前报告。
5. 临时阻断模块/报告请求：应显示错误和“重新加载/重试读取报告”，而不是无限 spinner、假空结果或上次筛选结果。恢复网络后使用同一重试入口，不重新提交研究。

## 当前明确限制及未覆盖验收

- 市场研究边界仍是日频沪深 A 股协议，单次 1–50 个明确成员、最多 32 个额外因子；PCA/固定篮子腿数与窗口有独立上限。几百票池/大量字段目录不是无限回测容量。
- 集合目录通常是当前成分；历史成员资格未验证。跨库字段只有真实数值、PIT 可用时间、身份映射满足条件时才可计算；schema-only 和待映射项不是数据覆盖。
- “最优估计器”仅表示既定候选和时间验证预算下的选择。没有已经证明的独立 alpha、单笔未来价位置信带、自动获利承诺或自动实盘交易。
- 因子增量只比较双方有效且成熟的配对预测；覆盖失衡和模型失败必须单列。尚无单独的因子增量显著性区间，不能相减两个基准区间替代。
- 多空执行仍采用理论借券及声明成本；不是核实券源后的券商撮合。历史协方差和池内因子 z-score 暴露限制不是未来波动承诺或市场 beta 中性证明。
- 大表采取分页；完整报告仍作为一个私有结果读取到内存。当前没有声明无限规模报告的流式渲染能力。
- 导入数据仅在当前页面内存绑定，刷新后需要重新导入；草稿和保存研究不会悄悄复原一个未持久化的数据集。已完成预测的执行复用使用服务端冻结输入，与临时编辑器数据区分。
- 代码项目与研究配置是两类记录。Python 只在浏览器隔离环境探索；不会作为任意服务端策略执行。AI 依赖实际服务，并保留显式应用建议步骤。
- 当前私有工作区通过会话识别；清除网站数据可能失去原工作区访问。没有在本轮宣称多成员组织权限、跨设备身份迁移或社区治理流程已经完成。
- 做市、衍生品与结构套利只是独立工作区边界提示，本轮不能运行。
- 本轮没有用真实屏幕阅读器、真实移动触摸设备或跨浏览器矩阵完成全面无障碍认证；DOM 焦点回归不代替这些检查。最终生产发布和长期运行由 root 的独立流程验收。


## 最后异步状态修复与可读性整理

这一轮功能改动与格式整理分别记录：

- `workspace.js` 为研究列表、配方目录、比较产物和已保存比较区分未加载、加载中、成功空结果及失败。首帧和请求期间不伪报 0 项；比较产物与比较历史独立完成请求。
- 比较/研究详情按当前路由身份显示。异步读取另一研究时不展示之前的记录，离开页面后迟到响应不会把用户导航回来；失败有可重复读取入口。
- 保存捕获提交快照和当前草稿身份。响应返回时，只有未继续修改的同一草稿才替换为服务端版本并清除 dirty；之后输入仍保持未保存状态，切换研究不被旧响应覆盖。运行引用实际保存返回的实验版本。
- `reports.js` 将执行改参草稿绑定运行身份。同一 forecastArtifactId 下的不同执行报告不会继承上一报告尚未提交的表单值；原始产物继续不可变。
- 完成功能回归后，使用固定 **Prettier 3.6.2** 整理 `web/quant-workspace/` 的六个 JS 模块和 CSS，增加职责说明。未格式化旧整库，未给生产引入运行时依赖。复现命令：`npx --yes prettier@3.6.2 --write 'web/quant-workspace/*.js' 'web/quant-workspace/*.css' --single-quote --trailing-comma es5 --print-width 100 --tab-width 2`。

格式化后，DOM 测试包含受控延迟响应、真实空结果、失败重试、详情迟到响应，以及保存途中继续输入和切换草稿。三份现存计算报告的六页冻结回归分别通过 53、58、62 项检查，包括运行级执行草稿隔离；未发供应商请求。这些检查不能替代最终构建的浏览器视觉和真实保存/刷新验收。
