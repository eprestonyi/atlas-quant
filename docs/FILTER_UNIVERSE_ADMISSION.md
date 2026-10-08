# 完整筛选集合：冻结、市场准备与容量准入

本分支实现服务端冻结完整筛选集合、实验版本绑定、独立逐请求市场准备计划和本地1000股容量profile。**尚无托管大池运行/独立市场采集consumer**；计划明确 `canStart:false`，大池运行明确拒绝，不自动取50只。

## 完整范围与API

`POST /quant/api/universe-scopes` 接受 `{selection,expectedResolutionHash,expectedSnapshotHash,start,end}`。服务端重新解析、核对预览版本，冻结全部有序members、规则、目录与来源池证据；拒绝客户端symbols/subset。结果为 `{scopeRef:{scopeId,scopeRoot,format:'atlas.quant.universe_scope',version:1},scope,created}`。同owner同root幂等，GET `/universe-scopes/:id` 只读原冻结版本；未来目录更新不会改旧scope。

`POST/PUT /statistical-quant/experiments` 顶层传 `universeScopeRef`，`strategy.universe` 仍携带**完整**symbols/start/end。服务端逐项精确比对；新scope不接受显式子集。PUT未传ref则继承前版本并继续精确核对，不能通过省略ref缩成50只；改变范围须新freeze并显式绑定新ref。每个experiment version在 `quant_experiment_scopes` 独立关联，CAS与新版本及scope关联同事务；copy/list/detail/export保留ref。

运行沿现有单数 `POST /statistical-quant/experiments/:id/run`。顶层ref可省略继承保存版，若提供须完全相同。≤50的现有来源可继续排队并冻结run关联；>50当前返回 `WHOLE_UNIVERSE_PROFILE_NOT_READY`，不排队、不改symbols/model/date。claim带服务端读取的 `universeScopeRef/universeScope`。旧产物/旧显式子集历史可读；新scope永不冒称它们是完整成员。

`/universes/resolve` 继续返回所有symbols/members/步骤/来源版本，但现在 `requiresSubset:false,membershipPolicy:'complete_filtered_set',admissionStatus:'preflight_required'`，不再建议手选50。保存scope本身不把五机制和auto改为trend/Ridge；数值支持列表来自独立profile。

## 市场准备计划：已实现，不访问provider

`POST /market-preparation-plans {scopeRef,profile:'pooled_asset_1000_v1',requiredFields:[...]}` 冻结每项请求的参数/字段/hash/授权scope。响应返回全部scope、预算、blockedReasons、计划ref和 `canStart:false`。GET `/market-preparation-plans/:id/requests?page=1&pageSize=50` 有界分页，≤100项；完整计划有独立root与owner，不从浏览器提交任意URL/token。

逐证券daily+adj_factor，明确需要时追加daily_basic；沪深分别trade_cal。中证1000混合沪深冷读计划为2002次，含daily_basic为3002次。返回原始响应后还需核官方日历实际session相等，否则整单拒绝，不能假设SSE代表SZSE。

固定请求预算：≤3002，每请求实际HTTP最多一次，daily128KiB/adj64KiB/daily_basic256KiB/calendar64KiB，原响应共同512MiB，规范化输入128MiB/300k行，串行最多60次/分钟，单请求30s、总7200s、租约120s/心跳20s。运营限速不证明供应商配额。授权scope参与每个requestKey，缓存不得跨授权使用。缺授权/日期超过366日/成员超过1000/BJ未支持均保留完整scope并给出明确阻断，不截断。非行情/PIT财报字段不能冒充本市场计划。

下一阶段consumer须独立持久claim、intent→单次HTTP→原响应加密fsync→receipt→规范化。响应未知sticky UNKNOWN/manual review，不可用旧TushareClient自动重试；完整HTTP错误保留receipt，权限/429停止新请求。原字节是实际endpoint交付字节，private proxy不冒称原Tushare wire。全体至少有合法行情才可ready；部分缺日保留，不前填价格或删证券。

## 真实本地容量边界

新显式 `pooled_asset_1000_v1` 使用原全池memmap、全横截面rank/zscore、一个pooled模型；没有拆成50股模型。支持≤1000、显式≤366日、≤300k行/sample、≤80k主预测和同量baseline、≤16因子、asset_price、mean_reversion/trend、Ridge、forecast-only、2×2验证和refit≥20。不改变原300或默认50准入。

可重跑命令：`OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 .venv/bin/python scripts/benchmark-filter-universe.py --family mean_reversion --output private/new-unique-directory`。`--family trend` 为另一个固定case。脚本先冻结参数/预算，单进程硬900s、单fit300s、RSS3GiB、总输出400MiB，0provider。1000人为合成代码不是中证1000历史行情；calendar为合成weekday。

已完成mean_reversion case：262000输入、201000样本，41000主+41000baseline（各35000成熟、6000tail），34/34fits，pooled_all_symbols。supervisor22.06s、引擎13.50s、进程peak1.214GB、cache152.8MB；forecast61.45MB、report61.65MB、snapshot64.40MB。完整bundle独立stdlib808127检查通过。因子增量相对MSE为负4.26%，不宣称alpha。所有原结果/预声明输入/逐文件source hash留在private/full-filter-1000-initial。未采样完整共机CPU轨迹，时间仅本机此次观测，不是独占极限。

第二个预声明trend/Ridge case也完整通过：262000输入、201000样本、41000主+41000baseline、34fits；supervisor20.87s、引擎12.44s、进程peak1.162GB、cache154.4MB。其完整bundle独立stdlib同样808127检查通过，保存在private/full-filter-1000-trend。两例均forecast-only、0交易；这证明指定合成输入/参数的整池可计算与产物完整性，不证明真实行情收益、托管准入或硬件独占极限。

auto/非线性需要逐候选真实资源试验与ML owner候选预算hook；配对/PCA/财务/事件另需真实目标/输入PIT能力，不从本测试推广。旧bundle/1的256MiB/1m集合项不扩大；超过输入或输出预算整单失败，不丢invalid/tail/baseline/control行。旧financial dataset/2的50股/64MiB边界不变；全池market准备将有独立明确来源profile，不篡改旧格式。

新增迁移 `0009_filter_universe.sql` 仅scope/实验关联/run关联/计划表。完整托管路径仍须由后续ready市场归档、source restore、服务器批准profile、runner能力匹配和bundle profile准入一起完成；不能只把API数字升到1000。
