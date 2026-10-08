/** Whole-asset forecast domain derived from frozen scope/calendar, never output rows. */
import { validateExpression } from "../factor-language.mjs";
import { validateChunk } from "../bundles/manifest.mjs";
import { canonical, fail } from "./common.mjs";
import { marketAssetTargets, marketHedgeMetadata } from "./hedge-index.mjs";

const reject = (message) => fail("MARKET_FORECAST_COVERAGE", message, 409);

export async function marketOriginDomain(strategy, calendar, symbols) {
  const warmup = Math.max(
    0,
    ...strategy.factors.map((f) => validateExpression(f.expression).lookback),
  );
  const start = Math.max(61, warmup + 1);
  const eligible = calendar.slice(start);
  const boundary = Math.floor(
    eligible.length * (1 - strategy.validation.holdoutFraction),
  );
  if (boundary < 1 || eligible.length - boundary < 10)
    reject("冻结日历无法形成完整报告窗口");
  const holdoutStart = eligible[boundary],
    origins = [];
  for (
    let i = start;
    i < calendar.length;
    i += strategy.research.observationDays
  ) {
    if (calendar[i] >= holdoutStart)
      origins.push({
        date: calendar[i],
        entryDate: calendar[i + 1] ?? null,
        targetDate: calendar[i + 1 + strategy.target.horizonSessions] ?? null,
      });
  }
  if (!origins.length || origins.length * symbols.length > 80000)
    reject("完整资产预测范围超过注册预算");
  const targets = await marketAssetTargets(symbols);
  const hedgeFits = [];
  let lastFit = -100000;
  for (
    let i = start;
    i < calendar.length;
    i += strategy.research.observationDays
  ) {
    if (i - lastFit >= strategy.model.refitDays) {
      hedgeFits.push({ date: calendar[i], informationCutoff: calendar[i - 1] });
      lastFit = i;
    }
  }
  return {
    holdoutStart,
    hedgeFits,
    holdoutEnd: calendar.at(-1),
    sampleRows:
      Math.ceil((calendar.length - start) / strategy.research.observationDays) *
      targets.length,
    origins,
    targets,
    expectedRows: origins.length * targets.length,
    baselineRequired: strategy.factors.some((f) => f.role !== "hedge"),
  };
}

export async function verifyMarketCoverage(
  env,
  stage,
  parsed,
  admission,
  read,
) {
  const domain = await marketOriginDomain(
    admission.strategy,
    admission.manifest.calendar,
    admission.manifest.scope.symbols,
  );
  const meta = parsed.metadata;
  if (
    meta.coverage.source !== "samples_before_model_fitting" ||
    meta.coverage.holdoutStart !== domain.holdoutStart ||
    meta.forecast.diagnostics?.holdoutStart !== domain.holdoutStart ||
    meta.report.validation?.holdoutStart !== domain.holdoutStart ||
    meta.coverage.baselineRequired !== domain.baselineRequired
  )
    reject("报告时钟或对照需求与来源独立推导结果不同");
  if (
    (domain.baselineRequired &&
      meta.forecast.diagnostics?.factorIncrement?.baselineValidation
        ?.holdoutStart !== domain.holdoutStart) ||
    meta.report.capacity?.holdoutStart !== domain.holdoutStart ||
    meta.report.capacity?.baselineRequired !== domain.baselineRequired ||
    meta.report.capacity?.forecastRows !== domain.expectedRows ||
    meta.report.capacity?.completeGridRows !==
      admission.manifest.calendar.length * domain.targets.length ||
    canonical(meta.report.capacity?.symbols) !==
      canonical(domain.targets.map((t) => t.symbols[0]))
  )
    reject("容量或对照报告时钟与完整来源不同");
  const clocks = [meta.forecast.diagnostics, meta.report.validation];
  if (domain.baselineRequired)
    clocks.push(meta.forecast.diagnostics.factorIncrement.baselineValidation);
  for (const clock of clocks) {
    if (
      clock?.holdoutEnd !== domain.holdoutEnd ||
      (clock.selectionAudit &&
        clock.selectionAudit.terminalSelectionCutoff !== domain.holdoutStart)
    )
      reject("报告终端窗口与完整来源日历不同");
  }
  if (
    meta.report.capacity?.sampleRows !== domain.sampleRows ||
    meta.report.capacity?.inputRows !== admission.manifest.rowCount
  )
    reject("容量样本或输入数量与完整来源不同");
  if (parsed.collections.get("targets").rowCount !== domain.targets.length)
    reject("目标定义未覆盖完整冻结股票池");
  const expected = new Map(domain.targets.map((t) => [t.id, canonical(t)]));
  for (const descriptor of parsed.collections.get("targets").chunks) {
    const raw = await read("targets", descriptor);
    const rows = await validateChunk(
      typeof raw === "string"
        ? raw
        : new TextDecoder("utf-8", { fatal: true }).decode(raw),
      descriptor,
    );
    for (const row of rows) {
      if (expected.get(row.id) !== canonical(row))
        reject("资产目标定义缺失、重复或超出冻结股票池");
      expected.delete(row.id);
    }
  }
  if (expected.size) reject("完整股票池资产目标缺失");
  const collections = [
    "forecasts",
    "plannedOrigins",
    ...(domain.baselineRequired ? ["baselineRows"] : []),
  ];
  if (
    !domain.baselineRequired &&
    (parsed.collections.get("baselineRows")?.rowCount ?? 0) !== 0
  )
    reject("无附加预测因子时出现未声明对照");
  const targetIds = canonical(domain.targets.map((t) => t.id)),
    clock = canonical(domain.origins);
  for (const collection of collections) {
    if (parsed.collections.get(collection)?.rowCount !== domain.expectedRows)
      reject("主预测、对照或拟合前计划遗漏完整资产行");
    // Exact ordinal lookup keeps this bounded to at most 80k rows per query.
    // The expected Cartesian product comes from immutable input, not staged
    // target/plan records. Null endpoints retain every invalid/tail origin.
    const mismatch = await env.DB.prepare(
      `SELECT r.ordinal FROM json_each(?) d CROSS JOIN json_each(?) t
      LEFT JOIN quant_bundle_records r ON r.stage_id=? AND r.collection=?
      AND r.ordinal=CAST(d.key AS INTEGER)*?+CAST(t.key AS INTEGER)
      WHERE r.ordinal IS NULL OR r.date IS NOT json_extract(d.value,'$.date') OR r.target_id IS NOT t.value
      OR json_extract(r.metadata,'$.entryDate') IS NOT json_extract(d.value,'$.entryDate')
      OR json_extract(r.metadata,'$.targetDate') IS NOT json_extract(d.value,'$.targetDate') LIMIT 1`,
    )
      .bind(clock, targetIds, stage.id, collection, domain.targets.length)
      .first();
    if (mismatch) reject("完整资产预测顺序、证券、日期或尾部端点不一致");
  }
  await verifyMarketHedgeFits(env, stage, parsed, domain, read);
  return domain;
}

/** Compact indexes never replace validation of the complete immutable raw rows. */
export async function verifyMarketHedgeFits(env, stage, parsed, domain, read) {
  const collection = parsed.collections.get("hedgeFits");
  if (!collection || collection.rowCount !== domain.hedgeFits.length)
    reject("完整资产构造拟合时钟缺失或重复");
  const indexed = await env.DB.prepare(
    "SELECT ordinal,date,metadata FROM quant_bundle_records WHERE stage_id=? AND collection='hedgeFits' ORDER BY ordinal",
  )
    .bind(stage.id)
    .all();
  if (indexed.results.length !== domain.hedgeFits.length)
    reject("完整资产构造拟合索引不完整");
  const ids = domain.targets.map((t) => t.id);
  let count = 0;
  for (const descriptor of collection.chunks) {
    const raw = await read("hedgeFits", descriptor);
    const rows = await validateChunk(
      typeof raw === "string"
        ? raw
        : new TextDecoder("utf-8", { fatal: true }).decode(raw),
      descriptor,
    );
    for (const row of rows) {
      const expected = domain.hedgeFits[count];
      if (
        !expected ||
        row.date !== expected.date ||
        row.informationCutoff !== expected.informationCutoff ||
        row.status !== "valid"
      )
        reject("资产构造拟合时钟或状态与冻结来源不一致");
      const metadata = await marketHedgeMetadata(row, ids);
      const index = indexed.results[count];
      // Both old full metadata and the new compact metadata remain readable.
      // Neither representation authorizes a subset: raw IDs were checked above.
      const legacy = { targetIds: ids };
      if (
        index.ordinal !== count ||
        index.date !== row.date ||
        ![canonical(metadata), canonical(legacy)].includes(
          canonical(JSON.parse(index.metadata)),
        )
      )
        reject("资产构造拟合索引与完整原始引用不同");
      count++;
    }
  }
  if (count !== domain.hedgeFits.length)
    reject("资产构造拟合分片未覆盖来源时钟");
}
