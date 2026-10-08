/** Bounded acquisition output transport. It creates an uploaded input only. */
import {
  LIMITS,
  integer,
  hash,
  hashBytes,
  readBytes,
  utf8,
  random,
  fixedStream,
} from "../financial/common.mjs";
import {
  object,
  id,
  fail,
  parse,
  NOW,
  leasedJob,
  jobDTO,
  requireJobAuthorization,
  ownedJob,
} from "./common.mjs";
import { digest, canonical, profile } from "./planner.mjs";
import { receiptFor, rawReceipt } from "./receipts.mjs";
const COLLECTIONS = ["package", "calendar"];
export async function getPublication(env, jobId) {
  const row = await env.DB.prepare(
    "SELECT * FROM financial_acquisition_publications WHERE job_id=?",
  )
    .bind(jobId)
    .first();
  if (!row) fail("NOT_FOUND", "获取输出尚未开始", 404);
  return row;
}
export async function publicationStatus(env, pub) {
  const manifest = parse(pub.manifest),
    rows = await env.DB.prepare(
      "SELECT collection,ordinal FROM financial_acquisition_chunks WHERE job_id=?",
    )
      .bind(pub.job_id)
      .all();
  const found = new Set(
    rows.results.map((x) => `${x.collection}:${x.ordinal}`),
  );
  return {
    manifestSha256: pub.manifest_hash,
    missing: Object.fromEntries(
      COLLECTIONS.map((name) => [
        name,
        manifest[name].chunks
          .filter((x) => !found.has(`${name}:${x.ordinal}`))
          .map((x) => x.ordinal),
      ]),
    ),
  };
}
function validateManifest(manifest, job) {
  object(manifest, [
    "format",
    "version",
    "jobId",
    "executionPlanRoot",
    "package",
    "calendar",
    "calendarRoot",
    "inputRoot",
    "packRoot",
    "sourceReceipts",
  ]);
  if (
    manifest.format !== "atlas.quant.financial_acquisition_output" ||
    manifest.version !== 1 ||
    manifest.jobId !== job.id ||
    manifest.executionPlanRoot !== parse(job.spec).executionPlanRoot
  )
    fail("OUTPUT_PROTOCOL", "获取输出身份不一致", 409);
  for (const key of ["calendarRoot", "inputRoot", "packRoot"])
    hash(manifest[key]);
  for (const name of COLLECTIONS) {
    const item = object(manifest[name], ["sha256", "byteLength", "chunks"]);
    hash(item.sha256);
    integer(
      item.byteLength,
      1,
      name === "calendar" ? LIMITS.registryEntryBytes : LIMITS.packageBytes,
    );
    if (
      !Array.isArray(item.chunks) ||
      !item.chunks.length ||
      item.chunks.length > 48
    )
      fail("OUTPUT_BUDGET", "输出分片数量无效");
    let total = 0;
    item.chunks.forEach((chunk, n) => {
      object(chunk, ["ordinal", "sha256", "byteLength"]);
      if (chunk.ordinal !== n) fail("OUTPUT_PROTOCOL", "输出分片顺序无效");
      hash(chunk.sha256);
      integer(chunk.byteLength, 1, LIMITS.chunkBytes);
      total += chunk.byteLength;
    });
    if (total !== item.byteLength) fail("OUTPUT_PROTOCOL", "输出总长度不一致");
  }
  const requests = parse(job.spec).requests;
  if (
    !Array.isArray(manifest.sourceReceipts) ||
    manifest.sourceReceipts.length !== requests.length
  )
    fail("SOURCE_CLOSURE", "全部来源回执必须保留");
  for (let i = 0; i < requests.length; i++) {
    const r = object(
      manifest.sourceReceipts[i],
      [
        "requestKey",
        "receiptId",
        "sha256",
        "byteLength",
        "normalizedSnapshotId",
      ],
      ["requestKey", "receiptId", "sha256", "byteLength"],
    );
    if (r.requestKey !== requests[i].requestKey)
      fail("SOURCE_CLOSURE", "来源回执顺序或身份无效");
    id(r.receiptId);
    hash(r.sha256);
    integer(r.byteLength, 1, profile.maxResponseBytes);
    if (r.normalizedSnapshotId !== undefined) hash(r.normalizedSnapshotId);
  }
}
export async function beginPublication(env, jobId, value) {
  object(value, ["leaseToken", "manifest"]);
  const job = await leasedJob(env, jobId, value.leaseToken);
  if (job.status !== "running") fail("CANCELLED", "任务正在取消", 409);
  requireJobAuthorization(env, job);
  validateManifest(value.manifest, job);
  const text = canonical(value.manifest);
  if (new TextEncoder().encode(text).length > 128 * 1024)
    fail("OUTPUT_BUDGET", "输出清单过大", 413);
  const manifestHash = await hashBytes(new TextEncoder().encode(text));
  for (const descriptor of value.manifest.sourceReceipts) {
    const receipt = await receiptFor(env, job, descriptor.requestKey);
    if (
      receipt.id !== descriptor.receiptId ||
      receipt.sha256 !== descriptor.sha256 ||
      receipt.byte_length !== descriptor.byteLength ||
      receipt.http_status !== 200
    )
      fail("SOURCE_CLOSURE", "来源回执未完成或不匹配", 409);
  }
  await env.DB.prepare(
    `INSERT OR IGNORE INTO financial_acquisition_publications(job_id,manifest,manifest_hash,input_id,calendar_ref,created_at)
    SELECT ?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM financial_acquisition_jobs WHERE id=? AND lease_token=? AND status='running' AND lease_until>=? AND deadline>=?)`,
  )
    .bind(
      jobId,
      text,
      manifestHash,
      random(),
      random(),
      NOW(),
      jobId,
      job.lease_token,
      NOW(),
      NOW(),
    )
    .run();
  const pub = await getPublication(env, jobId);
  if (pub.manifest_hash !== manifestHash)
    fail("OUTPUT_IMMUTABLE", "输出清单已经冻结", 409);
  return publicationStatus(env, pub);
}
export async function putPart(env, jobId, token, expected, name, n, raw) {
  const job = await leasedJob(env, jobId, token),
    pub = await getPublication(env, jobId);
  if (job.status !== "running") fail("CANCELLED", "任务正在取消", 409);
  if (hash(expected) !== pub.manifest_hash || !COLLECTIONS.includes(name))
    fail("ROOT_MISMATCH", "输出版本不一致", 409);
  requireJobAuthorization(env, job);
  const item = parse(pub.manifest)[name].chunks[n];
  if (
    !item ||
    item.ordinal !== n ||
    item.byteLength !== raw.length ||
    (await hashBytes(raw)) !== item.sha256
  )
    fail("CHUNK_INTEGRITY", "输出分片不匹配", 409);
  const key = `financial-acquisition/output/${jobId}/${pub.manifest_hash}/${name}/${n}/${item.sha256}`;
  await env.ARTIFACTS.put(key, raw, {
    httpMetadata: { contentType: "application/octet-stream" },
  });
  const now = NOW();
  await env.DB.prepare(
    `INSERT OR IGNORE INTO financial_acquisition_chunks(job_id,collection,ordinal,sha256,byte_length,object_key)
    SELECT ?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM financial_acquisition_jobs WHERE id=? AND lease_token=? AND status='running' AND lease_until>=? AND deadline>=?)`,
  )
    .bind(jobId, name, n, item.sha256, raw.length, key, jobId, token, now, now)
    .run();
  const saved = await env.DB.prepare(
    "SELECT sha256 FROM financial_acquisition_chunks WHERE job_id=? AND collection=? AND ordinal=?",
  )
    .bind(jobId, name, n)
    .first();
  if (saved?.sha256 !== item.sha256)
    fail("STALE_LEASE", "输出未提交，任务已停止", 409);
  return { ok: true };
}
async function partRows(env, pub, name) {
  const rows = (
    await env.DB.prepare(
      "SELECT * FROM financial_acquisition_chunks WHERE job_id=? AND collection=? ORDER BY ordinal",
    )
      .bind(pub.job_id, name)
      .all()
  ).results;
  if (rows.length !== parse(pub.manifest)[name].chunks.length)
    fail("OUTPUT_INCOMPLETE", "输出仍缺分片", 409);
  return rows;
}
function collection(env, rows) {
  let index = 0;
  return new ReadableStream({
    async pull(controller) {
      try {
        if (index === rows.length) {
          controller.close();
          return;
        }
        const row = rows[index++],
          obj = await env.ARTIFACTS.get(row.object_key);
        if (!obj || obj.size !== row.byte_length)
          throw Error("OUTPUT_CHUNK_MISSING");
        const raw = new Uint8Array(await obj.arrayBuffer());
        if ((await hashBytes(raw)) !== row.sha256)
          throw Error("OUTPUT_CHUNK_HASH");
        controller.enqueue(raw);
      } catch (error) {
        controller.error(error);
      }
    },
  });
}
async function verifySources(env, job, manifest) {
  const plan = parse(job.spec),
    receipts = [];
  let total = 0;
  for (const request of plan.requests) {
    const row = await receiptFor(env, job, request.requestKey);
    total += row.byte_length;
    if (total > profile.maxTotalBytes || row.http_status !== 200)
      fail("SOURCE_CLOSURE", "来源字节预算或状态无效", 409);
    let data;
    try {
      data = JSON.parse(utf8(await rawReceipt(env, row)));
    } catch {
      fail("PROVIDER_RESPONSE", "来源响应不是完整JSON", 409);
    }
    if (
      data.code !== 0 ||
      !Array.isArray(data.data?.fields) ||
      !Array.isArray(data.data?.items)
    )
      fail("PROVIDER_RESPONSE", "来源响应未成功", 409);
    const fields = data.data.fields,
      items = data.data.items;
    if (
      new Set(fields).size !== fields.length ||
      request.fields.some((x) => !fields.includes(x)) ||
      items.some((x) => !Array.isArray(x) || x.length !== fields.length)
    )
      fail("PROVIDER_RESPONSE", "来源列或记录形状不一致", 409);
    if (
      !items.length ||
      items.length >
        (request.endpoint === "trade_cal" ? 366 : profile.maxStatementRows)
    )
      fail("PROVIDER_SCOPE", "所选公司、报告期或完整日历没有可用记录", 409);
    // Retain only the bounded calendar rows; statement values are inspected
    // one response at a time and are not accumulated across the seven reads.
    const records =
      request.endpoint === "trade_cal"
        ? items.map((values) =>
            Object.fromEntries(fields.map((field, i) => [field, values[i]])),
          )
        : undefined;
    const at = (name) => fields.indexOf(name);
    if (
      request.endpoint !== "trade_cal" &&
      items.some(
        (values) =>
          values[at("ts_code")] !== request.params.ts_code ||
          values[at("end_date")] !== request.params.period ||
          String(values[at("report_type")]) !== "1" ||
          String(values[at("comp_type")]) !== "1",
      )
    )
      fail("PROVIDER_SCOPE", "返回记录不属于计划的公司或报表口径", 409);
    const expected = manifest.sourceReceipts.find(
      (x) => x.requestKey === request.requestKey,
    );
    if (
      expected.receiptId !== row.id ||
      expected.sha256 !== row.sha256 ||
      expected.byteLength !== row.byte_length
    )
      fail("SOURCE_CLOSURE", "来源回执发生变化", 409);
    receipts.push({ row, request, records });
  }
  return receipts;
}
async function verifyCalendar(env, job, pub, receipts) {
  const manifest = parse(pub.manifest),
    raw = new Uint8Array(
      await new Response(
        collection(env, await partRows(env, pub, "calendar")),
      ).arrayBuffer(),
    );
  if (
    raw.length !== manifest.calendar.byteLength ||
    (await hashBytes(raw)) !== manifest.calendar.sha256
  )
    fail("CALENDAR_INTEGRITY", "日历字节身份不一致", 409);
  const envelope = object(JSON.parse(utf8(raw)), [
    "kind",
    "registryVersion",
    "payload",
    "scope",
    "evidenceLevel",
  ]);
  const p = object(envelope.payload, [
    "sessions",
    "coverage_start",
    "coverage_end",
    "complete",
    "evidence_reference",
    "kind",
  ]);
  const { request, records, row } = receipts[0],
    spec = parse(job.spec),
    s = spec.selection;
  const fixture = row.source_kind === "fixture",
    kind = fixture ? "fixture" : "official";
  if (receipts.some((x) => x.row.source_kind !== row.source_kind))
    fail("SOURCE_KIND", "不可混合合成与实际来源", 409);
  const reference = `${fixture ? "SYNTHETIC_FIXTURE:" : ""}tushare:trade_cal:${s.exchange}:${s.announcementStart}:${s.end}:receipt-sha256:${row.sha256}`;
  if (
    envelope.kind !== "calendar" ||
    envelope.registryVersion !== 1 ||
    p.kind !== kind ||
    p.complete !== true ||
    p.coverage_start !== s.announcementStart ||
    p.coverage_end !== s.end ||
    p.evidence_reference !== reference ||
    envelope.evidenceLevel !==
      (fixture
        ? "EXPLICIT_SYNTHETIC_CALENDAR_NOT_MARKET_EVIDENCE"
        : "provider_reported_calendar")
  )
    fail("CALENDAR_INTEGRITY", "日历来源和覆盖声明不一致", 409);
  const start = Date.UTC(
      +s.announcementStart.slice(0, 4),
      +s.announcementStart.slice(4, 6) - 1,
      +s.announcementStart.slice(6),
    ),
    end = Date.UTC(+s.end.slice(0, 4), +s.end.slice(4, 6) - 1, +s.end.slice(6));
  const byDate = new Map();
  for (const record of records) {
    if (
      record.exchange !== s.exchange ||
      !["0", "1"].includes(String(record.is_open)) ||
      byDate.has(record.cal_date)
    )
      fail("CALENDAR_INTEGRITY", "日历交易所、开市标记或日期重复", 409);
    byDate.set(record.cal_date, record);
  }
  const sessions = [];
  let days = 0;
  for (let t = start; t <= end; t += 86400000) {
    days++;
    const day = new Date(t).toISOString().slice(0, 10).replaceAll("-", ""),
      r = byDate.get(day);
    if (!r) fail("CALENDAR_COVERAGE", "来源日历存在缺日", 409);
    if (String(r.is_open) === "1") sessions.push(day);
  }
  if (
    !sessions.length ||
    byDate.size !== days ||
    canonical(p.sessions) !== canonical(sessions)
  )
    fail("CALENDAR_INTEGRITY", "日历会话与原始回执不一致", 409);
  const evidence = {
    sessions_hash: await digest(p.sessions),
    coverage_start: p.coverage_start,
    coverage_end: p.coverage_end,
    complete: p.complete,
    kind: p.kind,
    evidence_reference: p.evidence_reference,
  };
  const root = await digest(evidence);
  object(envelope.scope, ["calendarRoot"]);
  if (root !== manifest.calendarRoot || root !== envelope.scope.calendarRoot)
    fail("CALENDAR_INTEGRITY", "日历证据根不一致", 409);
  return { raw, payload: p, root, sourceKind: row.source_kind };
}
export async function complete(env, jobId, value) {
  object(value, ["leaseToken", "manifestSha256"]);
  hash(value.manifestSha256);
  const job = await leasedJob(env, jobId, value.leaseToken, { terminal: true }),
    pub = await getPublication(env, jobId),
    manifest = parse(pub.manifest);
  if (pub.manifest_hash !== value.manifestSha256)
    fail("ROOT_MISMATCH", "输出版本不一致", 409);
  if (job.status === "completed")
    return { job: jobDTO(job), result: parse(job.result), idempotent: true };
  if (job.status !== "running") fail("CANCELLED", "任务已停止", 409);
  requireJobAuthorization(env, job);
  const pending = await publicationStatus(env, pub);
  if (Object.values(pending.missing).some((x) => x.length))
    fail("OUTPUT_INCOMPLETE", "输出仍缺分片", 409);
  const receipts = await verifySources(env, job, manifest),
    calendar = await verifyCalendar(env, job, pub, receipts);
  const sourceKey = `financial/${job.owner}/sources/${pub.input_id}/${manifest.package.sha256}.json`,
    calendarKey = `financial-acquisition/calendars/${manifest.calendar.sha256}.json`;
  // Hash inline with R2 backpressure. tee() would let a fast hash consumer
  // retain the entire package while the network branch is still uploading.
  const digester = new crypto.DigestStream("SHA-256"),
    writer = digester.getWriter();
  const hashed = collection(
    env,
    await partRows(env, pub, "package"),
  ).pipeThrough(
    new TransformStream({
      async transform(chunk, controller) {
        await writer.write(chunk);
        controller.enqueue(chunk);
      },
      async flush() {
        await writer.close();
      },
    }),
  );
  await env.ARTIFACTS.put(
    sourceKey,
    fixedStream(hashed, manifest.package.byteLength),
    {
      httpMetadata: { contentType: "application/json" },
    },
  );
  const actual = [...new Uint8Array(await digester.digest)]
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
  if (actual !== manifest.package.sha256)
    fail("OUTPUT_INTEGRITY", "完整输入包哈希不一致", 409);
  await env.ARTIFACTS.put(calendarKey, calendar.raw, {
    httpMetadata: { contentType: "application/json" },
  });
  const now = NOW(),
    result = {
      inputId: pub.input_id,
      calendarRef: pub.calendar_ref,
      inputRoot: manifest.inputRoot,
      packRoot: manifest.packRoot,
      researchBinding: false,
    };
  const metadata = {
    acquisition: {
      jobId,
      planId: job.plan_id,
      executionPlanRoot: manifest.executionPlanRoot,
      sourceReceipts: manifest.sourceReceipts,
      sourceKind: calendar.sourceKind,
      calendarReceiptSha256: receipts[0].row.sha256,
    },
    originalAsPublishedVerified: false,
    revisionTimeVerified: false,
    unitVerified: false,
    researchBinding: false,
  };
  const guard =
    "EXISTS(SELECT 1 FROM financial_acquisition_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>=? AND deadline>=?)";
  await env.DB.batch([
    env.DB.prepare(
      `INSERT OR IGNORE INTO financial_registry_entries(id,kind,owner,object_key,sha256,byte_length,metadata,created_at) SELECT ?,'calendar',?,?,?,?,?,? WHERE ${guard}`,
    ).bind(
      pub.calendar_ref,
      job.owner,
      calendarKey,
      manifest.calendar.sha256,
      calendar.raw.length,
      JSON.stringify({
        label:
          calendar.sourceKind === "fixture"
            ? "合成验收日历 · 非交易所证据"
            : `${parse(job.spec).selection.exchange} 冻结供应商日历`,
        calendarRoot: manifest.calendarRoot,
        coverageStart: calendar.payload.coverage_start,
        coverageEnd: calendar.payload.coverage_end,
        complete: true,
        sourceKind: calendar.sourceKind,
      }),
      now,
      jobId,
      job.lease_token,
      now,
      now,
    ),
    env.DB.prepare(
      `INSERT OR IGNORE INTO financial_inputs(id,owner,name,status,calendar_ref,proof_refs,declared_bytes,source_key,source_hash,source_bytes,metadata,request_id,request_hash,created_at,updated_at)
      SELECT ?,?,?,'uploaded',?,'[]',?,?,?,?,?,?,?,?,? WHERE ${guard} AND COALESCE((SELECT SUM(declared_bytes) FROM financial_inputs WHERE owner=? AND parent_id IS NULL),0)+COALESCE((SELECT SUM(total_bytes) FROM financial_publications WHERE owner=?),0)+?<=?`,
    ).bind(
      pub.input_id,
      job.owner,
      parse(job.spec).name,
      pub.calendar_ref,
      manifest.package.byteLength,
      sourceKey,
      manifest.package.sha256,
      manifest.package.byteLength,
      JSON.stringify(metadata),
      job.id,
      pub.manifest_hash,
      now,
      now,
      jobId,
      job.lease_token,
      now,
      now,
      job.owner,
      job.owner,
      manifest.package.byteLength,
      LIMITS.workspaceRetainedFinancialBytes,
    ),
    env.DB.prepare(
      `UPDATE financial_acquisition_jobs SET status='completed',result=?,phase='writing_evidence',updated_at=? WHERE id=? AND lease_token=? AND status='running' AND lease_until>=? AND deadline>=? AND EXISTS(SELECT 1 FROM financial_inputs WHERE id=? AND source_hash=?)`,
    ).bind(
      JSON.stringify(result),
      now,
      jobId,
      job.lease_token,
      now,
      now,
      pub.input_id,
      manifest.package.sha256,
    ),
  ]);
  const current = await ownedJob(env, job.owner, jobId);
  if (current.status !== "completed")
    fail("OUTPUT_NOT_COMMITTED", "输入未提交：任务已停止或工作区空间不足", 409);
  return { job: jobDTO(current), result };
}
