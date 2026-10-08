import {
  RAW_ARCHIVE_LIMITS,
  validateRawArchive,
  verifyRawArchive,
} from "./raw-archive.mjs";
/** Content-addressed, lease-fenced publication. No price series enter D1. */
import { NOW, random, parse } from "../runtime.mjs";
import { object, integer, hashBytes, string } from "../financial/common.mjs";
import { canonical, hash, fail, PROFILE, date } from "./common.mjs";
import {
  MARKET_FIELDS,
  DAILY_BASIC_FIELDS,
  MARKET_LIMITS,
} from "./planner.mjs";
import {
  leased,
  loadPlan,
  requireEnabled,
  jobView,
  ownedJob,
} from "./queue.mjs";
import { receiptKind } from "./receipts.mjs";
export const DATASET_FORMAT = "atlas.quant.market_dataset";
export const PUBLICATION_LIMITS = Object.freeze({
  manifestBytes: 256 * 1024,
  chunkBytes: 512 * 1024,
  chunks: 320,
  totalBytes: 128 * 1024 * 1024,
  ...RAW_ARCHIVE_LIMITS,
});
const collections = ["rows", "provenance", "receipts"];
export const outputCollections = (m) => ({
  ...m.collections,
  raw: m.rawArchive,
});
export const datasetRef = (r) => ({
  datasetId: r.id,
  datasetRoot: r.dataset_root,
  format: DATASET_FORMAT,
  version: 1,
});
export function validateManifest(m, job, plan) {
  object(m, [
    "format",
    "version",
    "profile",
    "planRoot",
    "universeScopeRef",
    "scope",
    "calendar",
    "fields",
    "rowCount",
    "sourceKind",
    "collections",
    "rawArchive",
  ]);
  if (
    m.format !== DATASET_FORMAT ||
    m.version !== 1 ||
    m.profile !== PROFILE ||
    m.planRoot !== job.plan_root ||
    canonical(m.universeScopeRef) !== canonical(plan.universeScopeRef) ||
    canonical(m.scope) !== canonical(plan.scope) ||
    canonical(m.fields) !== canonical(plan.fields)
  )
    fail("MARKET_MANIFEST_IDENTITY", "市场输出范围与计划不符", 409);
  integer(m.rowCount, 1, MARKET_LIMITS.maxRows);
  if (
    !Array.isArray(m.calendar) ||
    !m.calendar.length ||
    m.calendar.length > 366 ||
    m.calendar.some(
      (d, i) =>
        typeof d !== "string" ||
        !/^\d{8}$/.test(d) ||
        d < plan.scope.start ||
        d > plan.scope.end ||
        (i && d <= m.calendar[i - 1]),
    ) ||
    m.calendar.length * plan.scope.symbolCount > MARKET_LIMITS.maxRows
  )
    fail("MARKET_CALENDAR", "完整市场日历无效或超过全池预算");
  validateRawArchive(m.rawArchive, plan.requests.length);
  object(m.collections, collections);
  let total = 0,
    count = 0;
  for (const name of collections) {
    const c = m.collections[name];
    object(c, ["chunks", "byteLength", "rowCount"]);
    integer(c.byteLength, 1, PUBLICATION_LIMITS.totalBytes);
    integer(
      c.rowCount,
      1,
      name === "rows"
        ? MARKET_LIMITS.maxRows
        : name === "receipts"
          ? MARKET_LIMITS.maxRequests
          : 1,
    );
    if (!Array.isArray(c.chunks) || !c.chunks.length)
      fail("MARKET_MANIFEST", "缺少完整片段");
    let bytes = 0,
      rows = 0;
    for (const [ordinal, p] of c.chunks.entries()) {
      object(p, ["ordinal", "sha256", "byteLength", "rowCount"]);
      if (p.ordinal !== ordinal) fail("MARKET_MANIFEST", "片段必须连续");
      hash(p.sha256);
      integer(p.byteLength, 2, PUBLICATION_LIMITS.chunkBytes);
      integer(p.rowCount, 1, 10000);
      bytes += p.byteLength;
      rows += p.rowCount;
    }
    if (bytes !== c.byteLength || rows !== c.rowCount)
      fail("MARKET_MANIFEST", "片段累计数不一致");
    total += bytes;
    count += c.chunks.length;
  }
  if (
    total > PUBLICATION_LIMITS.totalBytes ||
    count > PUBLICATION_LIMITS.chunks ||
    m.collections.rows.rowCount !== m.rowCount ||
    m.collections.receipts.rowCount !== plan.requests.length ||
    m.collections.provenance.rowCount !== 1
  )
    fail("MARKET_MANIFEST_BUDGET", "完整数据或来源闭包超过预算");
}
export async function publication(env, jobId) {
  const p = await env.DB.prepare(
    "SELECT * FROM quant_market_publications WHERE job_id=?",
  )
    .bind(jobId)
    .first();
  if (!p) fail("NOT_FOUND", "市场输出尚未创建", 404);
  return p;
}
export async function publicationStatus(env, p) {
  const m = JSON.parse(p.manifest),
    rows = (
      await env.DB.prepare(
        "SELECT collection,ordinal FROM quant_market_parts WHERE job_id=?",
      )
        .bind(p.job_id)
        .all()
    ).results,
    have = new Set(rows.map((r) => r.collection + ":" + r.ordinal));
  return {
    manifestSha256: p.manifest_hash,
    missing: Object.fromEntries(
      Object.keys(outputCollections(m)).map((name) => [
        name,
        outputCollections(m)
          [name].chunks.filter((c) => !have.has(name + ":" + c.ordinal))
          .map((c) => c.ordinal),
      ]),
    ),
  };
}
export async function beginPublication(env, jobId, v) {
  object(v, ["leaseToken", "manifest"]);
  const job = await leased(env, jobId, v.leaseToken);
  requireEnabled(env, job);
  if (job.status !== "running") fail("CANCELLED", "市场准备正在取消", 409);
  const { plan } = await loadPlan(env, job.owner, job.plan_id, job.plan_root);
  validateManifest(v.manifest, job, plan);
  receiptKind(env, v.manifest.sourceKind);
  const text = canonical(v.manifest),
    raw = new TextEncoder().encode(text);
  if (raw.length > PUBLICATION_LIMITS.manifestBytes)
    fail("MARKET_MANIFEST_BUDGET", "市场目录超过预算", 413);
  const root = await hashBytes(raw),
    now = NOW();
  await env.DB.prepare(
    "INSERT OR IGNORE INTO quant_market_publications(job_id,owner,lease_token,manifest,manifest_hash,status,created_at,updated_at) SELECT ?,?,?,?,?,'staging',?,? WHERE EXISTS(SELECT 1 FROM quant_market_jobs WHERE id=? AND lease_token=? AND status='running' AND lease_until>=? AND deadline>=?)",
  )
    .bind(
      job.id,
      job.owner,
      job.lease_token,
      text,
      root,
      now,
      now,
      job.id,
      job.lease_token,
      now,
      now,
    )
    .run();
  const p = await publication(env, job.id);
  if (p.manifest_hash !== root || p.lease_token !== job.lease_token)
    fail("MARKET_PUBLICATION_CONFLICT", "市场输出已冻结为另一版本", 409);
  return publicationStatus(env, p);
}
export function objectKey(job, root, name, p) {
  return `market/datasets/${job.owner}/${job.id}/${job.lease_token}/${root}/${name}/${p.ordinal}-${p.sha256}`;
}
export async function putPart(env, jobId, token, root, name, ordinal, raw) {
  const job = await leased(env, jobId, token);
  requireEnabled(env, job);
  if (job.status !== "running") fail("CANCELLED", "市场准备正在取消", 409);
  const pub = await publication(env, jobId);
  if (
    pub.manifest_hash !== hash(root) ||
    pub.lease_token !== token ||
    (!collections.includes(name) && name !== "raw")
  )
    fail("MARKET_PUBLICATION_CONFLICT", "市场片段归属错误", 409);
  const p = outputCollections(JSON.parse(pub.manifest))[name].chunks[ordinal];
  if (
    !p ||
    p.ordinal !== ordinal ||
    p.byteLength !== raw.length ||
    (await hashBytes(raw)) !== p.sha256
  )
    fail("MARKET_PART_INTEGRITY", "市场片段哈希或长度不匹配", 409);
  const key = objectKey(job, root, name, p);
  await env.ARTIFACTS.put(key, raw, {
    httpMetadata: { contentType: "application/json" },
  });
  const now = NOW();
  const saved = await env.DB.prepare(
    "INSERT OR IGNORE INTO quant_market_parts(job_id,collection,ordinal,object_key,sha256,byte_length) SELECT ?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM quant_market_jobs WHERE id=? AND lease_token=? AND status='running' AND lease_until>=? AND deadline>=?)",
  )
    .bind(
      job.id,
      name,
      ordinal,
      key,
      p.sha256,
      p.byteLength,
      job.id,
      token,
      now,
      now,
    )
    .run();
  if (!saved.meta.changes) {
    const old = await env.DB.prepare(
      "SELECT object_key FROM quant_market_parts WHERE job_id=? AND collection=? AND ordinal=?",
    )
      .bind(job.id, name, ordinal)
      .first();
    if (old?.object_key !== key) {
      await env.ARTIFACTS.delete(key);
      fail("STALE_LEASE", "市场片段写入时任务已停止", 409);
    }
  }
  return {
    ok: true,
    collection: name,
    ordinal,
    sha256: p.sha256,
    byteLength: p.byteLength,
  };
}
export async function readPart(env, job, manifest, name, descriptor) {
  const key = objectKey(
      job,
      await hashBytes(new TextEncoder().encode(canonical(manifest))),
      name,
      descriptor,
    ),
    o = await env.ARTIFACTS.get(key);
  if (!o || o.size !== descriptor.byteLength)
    fail("MARKET_PART_INTEGRITY", "市场片段缺失", 409);
  const raw = new Uint8Array(await o.arrayBuffer());
  if ((await hashBytes(raw)) !== descriptor.sha256)
    fail("MARKET_PART_INTEGRITY", "市场片段损坏", 409);
  return raw;
}
async function* records(env, job, m, name) {
  for (const p of m.collections[name].chunks) {
    const raw = await readPart(env, job, m, name, p);
    let rows;
    try {
      rows = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
    } catch {
      fail("MARKET_PART_INTEGRITY", "市场片段不是完整JSON", 409);
    }
    if (!Array.isArray(rows) || rows.length !== p.rowCount)
      fail("MARKET_PART_INTEGRITY", "市场片段记录数不符", 409);
    for (const row of rows) yield row;
  }
}
async function verify(env, job, m, plan) {
  const symbols = new Set(plan.scope.symbols),
    calendar = new Set(m.calendar),
    counts = new Map();
  let previous = "",
    count = 0;
  const valueHasher = new crypto.DigestStream("SHA-256"),
    valueWriter = valueHasher.getWriter();
  for await (const row of records(env, job, m, "rows")) {
    object(row, ["ts_code", "trade_date", ...m.fields]);
    if (
      Object.keys(row).length !== m.fields.length + 2 ||
      !symbols.has(row.ts_code) ||
      !calendar.has(row.trade_date)
    )
      fail("MARKET_ROWS", "市场行字段或范围不符", 409);
    const key = row.trade_date + ":" + row.ts_code;
    if (key <= previous)
      fail("MARKET_ROWS", "市场行需日期/证券严格排序且不重复", 409);
    previous = key;
    for (const field of m.fields) {
      const x = row[field];
      if (x === null && DAILY_BASIC_FIELDS.includes(field)) continue;
      if (
        typeof x !== "number" ||
        !Number.isFinite(x) ||
        (MARKET_FIELDS.includes(field) &&
          (field === "vol" || field === "amount" ? x < 0 : x <= 0))
      )
        fail("MARKET_ROWS", "市场数值无效", 409);
    }
    if (
      row.low > Math.min(row.open, row.close) ||
      row.high < Math.max(row.open, row.close) ||
      row.low > row.high
    )
      fail("MARKET_ROWS", "OHLC区间不一致", 409);
    counts.set(row.ts_code, (counts.get(row.ts_code) || 0) + 1);
    count++;
    await valueWriter.write(new TextEncoder().encode(canonical(row) + "\n"));
  }
  if (count !== m.rowCount || counts.size !== symbols.size)
    fail("MARKET_INCOMPLETE", "必须保留完整集合且每只证券有真实观测", 409);
  await valueWriter.close();
  const rowValueRoot = Array.from(
    new Uint8Array(await valueHasher.digest),
    (v) => v.toString(16).padStart(2, "0"),
  ).join("");
  const receipts = [];
  for await (const r of records(env, job, m, "receipts")) receipts.push(r);
  const actual = (
    await env.DB.prepare(
      "SELECT c.* FROM quant_market_job_receipts j JOIN quant_market_receipts c ON c.id=j.receipt_id WHERE j.job_id=?",
    )
      .bind(job.id)
      .all()
  ).results;
  if (actual.length !== plan.requests.length)
    fail("MARKET_RECEIPTS_INCOMPLETE", "完整计划尚有未取得的原始回执", 409);
  const byKey = new Map(actual.map((r) => [r.request_key, r]));
  for (const [i, r] of receipts.entries()) {
    object(r, [
      "requestKey",
      "receiptId",
      "sha256",
      "byteLength",
      "httpStatus",
      "retrievedAt",
      "sourceKind",
      "rawLocation",
    ]);
    const expected = plan.requests[i],
      a = byKey.get(r.requestKey);
    if (
      !a ||
      r.requestKey !== expected.requestKey ||
      a.id !== r.receiptId ||
      a.sha256 !== r.sha256 ||
      a.byte_length !== r.byteLength ||
      a.http_status !== r.httpStatus ||
      a.retrieved_at !== r.retrievedAt ||
      a.source_kind !== r.sourceKind ||
      r.httpStatus !== 200 ||
      r.sourceKind !== m.sourceKind
    )
      fail("MARKET_RECEIPTS_INTEGRITY", "来源回执与完整计划不符", 409);
  }
  await verifyRawArchive(m, receipts, (p) => readPart(env, job, m, "raw", p));
  let p;
  for await (const row of records(env, job, m, "provenance")) p = row;
  object(p, [
    "source",
    "synthetic",
    "transport",
    "originalProviderWireAvailable",
    "tradingDates",
    "planRoot",
    "universeScopeRoot",
    "membershipPolicy",
    "historicalMembershipVerified",
    "adjustment",
    "volumeUnit",
    "amountUnit",
    "missingSessions",
    "observedColumns",
    "derivedColumns",
  ]);
  if (
    !p ||
    p.transport !== "one_attempt_authorized_endpoint" ||
    p.membershipPolicy !== "complete_filtered_set" ||
    p.historicalMembershipVerified !== false ||
    p.volumeUnit !== "hands" ||
    p.amountUnit !== "CNY_thousands" ||
    p.missingSessions !== "preserved_no_price_fill" ||
    p.adjustment !==
      "OHLC multiplied by adj_factor / first observed adj_factor per symbol" ||
    canonical(p.observedColumns) !==
      canonical([
        "raw_close",
        "vol",
        "amount",
        "adj_factor",
        ...m.fields.filter((x) => DAILY_BASIC_FIELDS.includes(x)).sort(),
      ]) ||
    canonical(p.derivedColumns) !==
      canonical(
        Object.fromEntries(
          ["open", "high", "low", "close"].map((c) => [
            c,
            {
              formula: "raw_" + c + " * adj_factor / first_adj_factor",
              classification: "CORPORATE_ACTION_ADJUSTED",
            },
          ]),
        ),
      ) ||
    p.source !==
      (m.sourceKind === "fixture"
        ? "SYNTHETIC_MARKET_FIXTURE"
        : "TUSHARE_PRO") ||
    p.synthetic !== (m.sourceKind === "fixture") ||
    canonical(p.tradingDates) !== canonical(m.calendar) ||
    p.planRoot !== m.planRoot ||
    p.universeScopeRoot !== m.universeScopeRef.scopeRoot ||
    p.originalProviderWireAvailable !== false
  )
    fail("MARKET_PROVENANCE", "市场来源声明不匹配", 409);
  // Verify both official calendars against retained responses, not a client label.
  for (const request of plan.requests.filter(
    (r) => r.apiName === "trade_cal",
  )) {
    const a = byKey.get(request.requestKey),
      o = await env.ARTIFACTS.get(a.object_key);
    if (!o || o.size !== a.byte_length)
      fail("MARKET_CALENDAR", "原始日历缺失", 409);
    const raw = new Uint8Array(await o.arrayBuffer());
    if ((await hashBytes(raw)) !== a.sha256)
      fail("MARKET_CALENDAR", "原始日历损坏", 409);
    let value;
    try {
      value = JSON.parse(new TextDecoder().decode(raw));
    } catch {
      fail("MARKET_CALENDAR", "日历响应不能解析", 409);
    }
    const f = value?.data?.fields,
      items = value?.data?.items;
    if (value.code !== 0 || !Array.isArray(f) || !Array.isArray(items))
      fail("MARKET_CALENDAR", "日历响应无效", 409);
    const di = f.indexOf("cal_date"),
      oi = f.indexOf("is_open"),
      ei = f.indexOf("exchange");
    if (Math.min(di, oi, ei) < 0) fail("MARKET_CALENDAR", "日历字段缺失", 409);
    const days = new Set(),
      sessions = [];
    for (const row of items) {
      if (
        row[ei] !== request.params.exchange ||
        typeof row[di] !== "string" ||
        !Number.isFinite(date(row[di])) ||
        row[di] < plan.scope.start ||
        row[di] > plan.scope.end ||
        days.has(row[di]) ||
        ![0, 1, "0", "1"].includes(row[oi])
      )
        fail("MARKET_CALENDAR", "日历范围或重复无效", 409);
      days.add(row[di]);
      if (Number(row[oi]) === 1) sessions.push(row[di]);
    }
    if (
      days.size !== plan.budget.calendarDays ||
      canonical(sessions.sort()) !== canonical(m.calendar)
    )
      fail("MARKET_CALENDAR", "全部交易所日历须完整且一致", 409);
  }
  return rowValueRoot;
}
export async function complete(env, jobId, v) {
  object(v, ["leaseToken", "manifestSha256"]);
  const job = await leased(env, jobId, v.leaseToken, true),
    p = await publication(env, jobId);
  if (
    p.manifest_hash !== hash(v.manifestSha256) ||
    p.lease_token !== v.leaseToken
  )
    fail("MARKET_PUBLICATION_CONFLICT", "完成版本不匹配", 409);
  if (job.status === "completed")
    return { job: jobView(job), result: parse(job.result) };
  requireEnabled(env, job);
  if (job.status !== "running") fail("CANCELLED", "市场准备已取消", 409);
  const status = await publicationStatus(env, p);
  if (Object.values(status.missing).some((x) => x.length))
    fail("MARKET_PARTS_MISSING", "市场输出尚有缺片", 409);
  const m = JSON.parse(p.manifest),
    { plan } = await loadPlan(env, job.owner, job.plan_id, job.plan_root);
  validateManifest(m, job, plan);
  const rowValueRoot = await verify(env, job, m, plan);
  const did = random(),
    now = NOW(),
    result = {
      marketDatasetRef: {
        datasetId: did,
        datasetRoot: p.manifest_hash,
        format: DATASET_FORMAT,
        version: 1,
      },
      universeScopeRef: plan.universeScopeRef,
      rowCount: m.rowCount,
      symbolCount: m.scope.symbolCount,
      profile: PROFILE,
    };
  await env.DB.batch([
    env.DB.prepare(
      "UPDATE quant_market_jobs SET status='completed',phase='ready',result=?,updated_at=? WHERE id=? AND lease_token=? AND status='running' AND lease_until>=? AND deadline>=?",
    ).bind(JSON.stringify(result), now, job.id, job.lease_token, now, now),
    env.DB.prepare(
      "INSERT INTO quant_market_datasets(id,owner,job_id,dataset_root,plan_root,scope_root,profile,manifest,row_value_root,status,created_at) SELECT ?,?,?,?,?,?,?,?,?,'ready',? WHERE EXISTS(SELECT 1 FROM quant_market_jobs WHERE id=? AND result=? AND status='completed') AND changes()=1",
    ).bind(
      did,
      job.owner,
      job.id,
      p.manifest_hash,
      job.plan_root,
      plan.universeScopeRef.scopeRoot,
      PROFILE,
      p.manifest,
      rowValueRoot,
      now,
      job.id,
      JSON.stringify(result),
    ),
    env.DB.prepare(
      "UPDATE quant_market_publications SET status='committed',updated_at=? WHERE job_id=? AND EXISTS(SELECT 1 FROM quant_market_datasets WHERE job_id=?)",
    ).bind(now, job.id, job.id),
  ]);
  const current = await ownedJob(env, job.owner, job.id);
  if (current.status !== "completed")
    fail("STALE_LEASE", "市场发布时任务已停止", 409);
  return { job: jobView(current), result: parse(current.result) };
}
