# 股票池集合规则

`GET /quant/api/universe-options` 返回可用筛选字段、属性选项、精选股票池入口、目录版本和运行限制，不返回全量股票池成员。完整池目录继续通过 `/universes` 分页查询。`POST /quant/api/universes/resolve` 需要当前工作区会话，正文仅包含 `selection`。

```json
{
  "selection": {
    "version": 1,
    "includeGroups": [
      {"id": "beijing1000", "name": "中证1000与北京", "filters": [
        {"field": "universe", "value": "实际中证1000目录ID"},
        {"field": "area", "value": "北京"}
      ]},
      {"id": "industry300", "name": "沪深300与所选行业", "filters": [
        {"field": "universe", "value": "实际沪深300目录ID"},
        {"field": "industry", "value": ["目录中的行业分类一", "目录中的行业分类二"]}
      ]}
    ],
    "excludeGroups": [
      {"id": "exclude_index", "name": "剔除另一指数", "filters": [
        {"field": "universe", "value": "实际待剔除目录ID"}
      ]}
    ],
    "includeSymbols": ["600519.SH"],
    "excludeSymbols": ["000001.SZ"]
  }
}
```

示例里的目录ID和行业名称是明确的占位符，必须通过当前选项接口替换为真实值；不存在的分类不会被猜测或自动映射。若要使用更宽泛的“材料”等研究分类，可以明确组合现有行业选项，或在同一分组增加实际行业股票池条件。

同一条件的多个值取 OR，同一分组的条件取 AND，纳入组之间取 OR。随后并入 `includeSymbols`，减去所有排除组的并集，最后减去 `excludeSymbols`；因此最终剔除优先于显式加入。空纳入规则返回空集合，不隐式选择全市场。属性条件以已登记的A股身份和真实成员为基础，上市状态也可以显式筛选。

允许字段：`universe`、`area`、`industry`、`market`、`exchange`、`list_status`、`is_hs`。纳入及排除合计最多20组，每组最多20条件，每条件最多64值，个股加入或排除列表分别最多6,000项。未知字段、分类、目录ID或证券身份返回明确错误。

解析结果包含规范化 `selection`、完整排序的 `symbols`、成员属性 `members`、每个条件和集合步骤的 `steps`、`symbolCount`、`catalogSnapshot`、`snapshotHash`、`resolutionHash` 与来源池元数据。超过50只时仍返回完整结果，并设置 `requiresSubset: true`，不会自动取前20或前50。运行使用全部结果还是明确子集，由用户另行确认；运行端核对保存的规则、目录版本、结果版本和实际证券代码。

目录身份分为有属性的 `observed_identity` 和已有真实股票池成员资格但属性暂缺的 `missing_identity_metadata`。后者保留成员资格，名称、地域和行业保持 null，不会匹配未知属性。当前来源中4个这样的成员已明确标记。分类和成员均为当前来源快照，`historicalMembershipVerified` 固定为 false，不推断历史成分。

## 服务端目录

`scripts/universe-registry.mjs` 将身份元数据拆成每块至多200条的 D1 `meta` 记录，并在最后写入 `universe_registry` 内容版本。成员继续存放于 `research_universes`。读取时核对身份和成员完整哈希，数据导入中若混有不同版本则返回503，不拼接错误快照。

服务端缓存60秒，每个 isolate 最多保留两个D1绑定的目录；规则查询不新增缓存键。当前完整身份和1,354个池的选项响应约11KB。目录解析允许大集合，研究运行的50证券上限单独执行。

`node --test tests/universe.test.mjs` 覆盖集合算术、全量保留、未知身份、输入边界、SHA256快照、会话隔离、真实D1目录加载，以及规则保存和任务排队前的版本/子集核对。测试不访问行情供应商，也不部署服务。
