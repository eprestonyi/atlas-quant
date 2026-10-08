/** Publication closure over dates, coordinates and references. This is a
 * structural audit; Decimal formula evaluation remains the Python core's job. */
import { fail, parse, LIMITS } from './common.mjs';
import { registryEntry, registryBytes } from './registry.mjs';
import { parseStrictJson } from '../bundles/json.mjs';
export async function verifyPreparedClosure(env, job, pub, manifest) {
  const selection = manifest.summary.input.selection;
  const input = await env.DB.prepare(
    'SELECT calendar_ref FROM financial_inputs WHERE id=? AND owner=?'
  )
    .bind(job.input_id, job.owner)
    .first();
  const descriptor = await registryEntry(env, job.owner, input.calendar_ref, 'calendar');
  const calendar = parseStrictJson(new TextDecoder().decode(await registryBytes(env, descriptor)), {
    canonical: false,
  });
  const allDates = calendar.payload?.sessions;
  if (
    !Array.isArray(allDates) ||
    allDates.length > LIMITS.calendarSessions ||
    allDates.some(
      (d, n) => typeof d !== 'string' || !/^\d{8}$/.test(d) || (n > 0 && d <= allDates[n - 1])
    )
  )
    fail('CALENDAR_INTEGRITY', '登记日历不能用于核对面板完整性', 409);
  const dates = allDates.filter((date) => date >= selection.start && date <= selection.end),
    dateJson = JSON.stringify(dates);
  if (!dates.length || dates.length * selection.symbols.length > LIMITS.panelRows)
    fail('PANEL_INTEGRITY', '日历范围为空或面板超过预算', 409);
  const counts = await env.DB.prepare(
    'SELECT collection,COUNT(*) n FROM financial_records WHERE publication_id=? GROUP BY collection'
  )
    .bind(pub.id)
    .all();
  const actual = Object.fromEntries(counts.results.map((x) => [x.collection, x.n]));
  for (const [name, c] of Object.entries(manifest.collections))
    if (name !== 'package' && (actual[name] || 0) !== c.rowCount)
      fail('RECORD_INTEGRITY', '记录索引与分片清单不一致', 409);
  if (actual.panel !== dates.length * selection.symbols.length)
    fail('PANEL_INTEGRITY', '面板必须保留每个日历日期与标的，包括缺失行', 409);
  if (actual.coverage !== selection.symbols.length * selection.selectedStateIds.length)
    fail('COVERAGE_INTEGRITY', '覆盖摘要缺少标的与状态坐标', 409);
  const badDate = await env.DB.prepare(
    "SELECT 1 FROM financial_records WHERE publication_id=? AND collection='panel' AND date NOT IN (SELECT value FROM json_each(?)) LIMIT 1"
  )
    .bind(pub.id, dateJson)
    .first();
  if (badDate) fail('PANEL_INTEGRITY', '面板含登记日历之外的日期', 409);
  // Validity counts come from uploaded panel null masks, never from a claimed
  // coverage summary. Numeric state values are not copied into D1.
  const badCoverage = await env.DB.prepare(
    `WITH valid AS (
 SELECT p.symbol,j.value state_id,COUNT(*) n FROM financial_records p,json_each(p.audit,'$.available') j
 WHERE p.publication_id=? AND p.collection='panel' GROUP BY p.symbol,j.value)
 SELECT c.record_id FROM financial_records c LEFT JOIN valid v ON v.symbol=c.symbol AND v.state_id=c.state_id
 WHERE c.publication_id=? AND c.collection='coverage' AND
 (json_extract(c.audit,'$.okRows')!=COALESCE(v.n,0) OR json_extract(c.audit,'$.missingRows')!=?-COALESCE(v.n,0)) LIMIT 1`
  )
    .bind(pub.id, pub.id, dates.length)
    .first();
  if (badCoverage) fail('COVERAGE_INTEGRITY', '有效与缺失行数不匹配真实面板', 409);
  const anyUsable = await env.DB.prepare(
    "SELECT 1 FROM financial_records p,json_each(p.audit,'$.available') WHERE p.publication_id=? AND p.collection='panel' LIMIT 1"
  )
    .bind(pub.id)
    .first();
  if (manifest.summary.hasUsableStates !== Boolean(anyUsable))
    fail('COVERAGE_INTEGRITY', '整体可用性与真实面板不一致', 409);
  const invalidInterval = await env.DB.prepare(
    `WITH cal AS (SELECT CAST(key AS INTEGER) n,value date FROM json_each(?)),
 intervals AS (SELECT a.symbol,a.date,a.through_date,s.n start_n,e.n end_n,
 LAG(e.n) OVER(PARTITION BY a.symbol ORDER BY a.date) previous_end
 FROM financial_records a LEFT JOIN cal s ON s.date=a.date LEFT JOIN cal e ON e.date=a.through_date
 WHERE a.publication_id=? AND a.collection='assignments')
 SELECT symbol FROM intervals WHERE start_n IS NULL OR end_n IS NULL OR end_n<start_n OR start_n!=COALESCE(previous_end+1,0) LIMIT 1`
  )
    .bind(dateJson, pub.id)
    .first();
  const endpoints = await env.DB.prepare(
    "SELECT symbol,MAX(through_date) last_date FROM financial_records WHERE publication_id=? AND collection='assignments' GROUP BY symbol"
  )
    .bind(pub.id)
    .all();
  if (
    invalidInterval ||
    endpoints.results.length !== selection.symbols.length ||
    endpoints.results.some((x) => x.last_date !== dates.at(-1))
  )
    fail('ASSIGNMENT_INTEGRITY', '状态分配未完整覆盖日历或存在重叠', 409);
  const invalidReference = await env.DB.prepare(
    `SELECT a.record_id FROM financial_records a,json_each(a.audit,'$.states') j
 LEFT JOIN financial_records e ON e.publication_id=a.publication_id AND e.collection='events' AND e.record_id=j.value
 WHERE a.publication_id=? AND a.collection='assignments' AND
 (e.record_id IS NULL OR e.symbol!=a.symbol OR e.state_id!=j.key OR e.date>a.date) LIMIT 1`
  )
    .bind(pub.id)
    .first();
  if (invalidReference) fail('ASSIGNMENT_INTEGRITY', '分配引用不存在、错位或未来事件', 409);
  const inconsistentMask = await env.DB.prepare(
    `SELECT p.record_id FROM financial_records a,json_each(a.audit,'$.states') j
 JOIN financial_records e ON e.publication_id=a.publication_id AND e.collection='events' AND e.record_id=j.value
 JOIN financial_records p ON p.publication_id=a.publication_id AND p.collection='panel' AND p.symbol=a.symbol AND p.date>=a.date AND p.date<=a.through_date
 WHERE a.publication_id=? AND a.collection='assignments' AND
 ((e.status='ok')!=EXISTS(SELECT 1 FROM json_each(p.audit,'$.available') v WHERE v.value=j.key)) LIMIT 1`
  )
    .bind(pub.id)
    .first();
  if (inconsistentMask) fail('ASSIGNMENT_INTEGRITY', '面板缺失标记与分配事件状态不一致', 409);
  const orphan = await env.DB.prepare(
    `SELECT e.record_id FROM financial_records e WHERE e.publication_id=? AND e.collection='events' AND NOT EXISTS(
 SELECT 1 FROM financial_records a,json_each(a.audit,'$.states') j WHERE a.publication_id=e.publication_id AND a.collection='assignments' AND j.value=e.record_id) LIMIT 1`
  )
    .bind(pub.id)
    .first();
  if (orphan) fail('ASSIGNMENT_INTEGRITY', '产物存在未被分配的状态事件', 409);
}
