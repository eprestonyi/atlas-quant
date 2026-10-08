/** Source-derived asset coverage after the exact graph component was verified.
 * Planned/output records are never allowed to define their own required domain.
 */
import { ApiError } from '../errors.mjs';
import { validateChunk } from '../bundles/manifest.mjs';
import { marketOriginDomain, verifyMarketHedgeFits } from '../market-preparation/coverage.mjs';
import { same } from '../financial-bundles/manifest.mjs';
const reject = () => {
  throw new ApiError(
    'FINANCIAL_GRAPH_COVERAGE',
    '图研究未保留完整来源证券、预测时钟或尾部记录',
    409
  );
};

export async function verifyGraphCoverage(env, stage, parsed, commitment, read) {
  // The caller first hashes columns + this provenance against researchColumns.
  // Its calendar is therefore immutable input, not an output-only assertion.
  const strategy = parsed.metadata.forecast.sourceStrategy;
  const scope = commitment.scope;
  const calendar = parsed.metadata.snapshot.provenance.tradingDates;
  if (
    !Array.isArray(calendar) ||
    !calendar.length ||
    calendar.length > 366 ||
    calendar.some(
      (d, i) =>
        typeof d !== 'string' ||
        !/^\d{8}$/.test(d) ||
        d < scope.start ||
        d > scope.end ||
        (i && d <= calendar[i - 1])
    ) ||
    !same(strategy.universe.symbols, scope.symbols)
  )
    reject();
  const domain = await marketOriginDomain(strategy, calendar, scope.symbols);
  if (domain.expectedRows > 25000) reject();
  const { forecast, report, coverage } = parsed.metadata;
  if (
    coverage.source !== 'samples_before_model_fitting' ||
    coverage.holdoutStart !== domain.holdoutStart ||
    coverage.baselineRequired !== domain.baselineRequired
  )
    reject();
  const clocks = [forecast.diagnostics, report.validation];
  if (domain.baselineRequired)
    clocks.push(forecast.diagnostics?.factorIncrement?.baselineValidation);
  for (const clock of clocks) {
    if (
      clock?.holdoutStart !== domain.holdoutStart ||
      clock.holdoutEnd !== domain.holdoutEnd ||
      (clock.selectionAudit && clock.selectionAudit.terminalSelectionCutoff !== domain.holdoutStart)
    )
      reject();
  }
  const targets = parsed.collections.get('targets');
  if (targets.rowCount !== domain.targets.length) reject();
  const expected = new Map(domain.targets.map((t) => [t.id, t]));
  for (const descriptor of targets.chunks) {
    const raw = await read('targets', descriptor);
    const rows = await validateChunk(
      new TextDecoder('utf-8', { fatal: true }).decode(raw),
      descriptor
    );
    for (const row of rows) {
      if (!same(expected.get(row.id), row)) reject();
      expected.delete(row.id);
    }
  }
  if (expected.size) reject();
  const required = [
    'forecasts',
    'plannedOrigins',
    ...(domain.baselineRequired ? ['baselineRows'] : [])
  ];
  if (!domain.baselineRequired && (parsed.collections.get('baselineRows')?.rowCount ?? 0)) reject();
  const ids = JSON.stringify(domain.targets.map((t) => t.id));
  const origins = JSON.stringify(domain.origins);
  for (const name of required) {
    if (parsed.collections.get(name)?.rowCount !== domain.expectedRows) reject();
    const bad = await env.DB.prepare(
      `SELECT r.ordinal FROM json_each(?) d CROSS JOIN json_each(?) t
      LEFT JOIN quant_bundle_records r ON r.stage_id=? AND r.collection=?
      AND r.ordinal=CAST(d.key AS INTEGER)*?+CAST(t.key AS INTEGER)
      WHERE r.ordinal IS NULL OR r.date IS NOT json_extract(d.value,'$.date') OR r.target_id IS NOT t.value
      OR json_extract(r.metadata,'$.entryDate') IS NOT json_extract(d.value,'$.entryDate')
      OR json_extract(r.metadata,'$.targetDate') IS NOT json_extract(d.value,'$.targetDate') LIMIT 1`
    )
      .bind(origins, ids, stage.id, name, domain.targets.length)
      .first();
    if (bad) reject();
  }
  await verifyMarketHedgeFits(env, stage, parsed, domain, read);
  return domain;
}
