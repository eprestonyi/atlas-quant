import { LEGACY_DATASET, assertContext } from './context.mjs';
/** Resolve only same-owner immutable sources. No provider/upload fallback exists. */
import { ownedStageForRun, parsedStage } from '../bundles/storage.mjs';
import { inputRegistry, registryBytes } from '../financial/registry.mjs';
import { ownedPreparation } from '../financial/read-model.mjs';
import {
  LIMITS,
  object,
  id,
  hash,
  integer,
  date,
  fail,
  parse,
  canonical,
  sha,
  bytes
} from './common.mjs';
import { parseStrictJson } from '../bundles/json.mjs';

function sourceScope(value) {
  if (
    !value ||
    !Array.isArray(value.symbols) ||
    value.symbols.length < 1 ||
    value.symbols.length > LIMITS.symbols
  )
    fail('DATASET_SOURCE_NOT_ELIGIBLE', '冻结来源缺少完整研究范围', 409);
  return {
    symbols: [...value.symbols].sort(),
    start: value.start,
    end: value.end
  };
}
export function validateTransform(t, original) {
  object(t, ['kind', 'version', 'mode', 'symbols', 'start', 'end']);
  if (
    t.kind !== 'snapshot_scope_view' ||
    t.version !== 1 ||
    !['exact', 'explicit_subset'].includes(t.mode)
  )
    fail('DATASET_SCOPE', '请选择完整范围或明确子范围');
  if (
    !Array.isArray(t.symbols) ||
    t.symbols.length < 1 ||
    t.symbols.length > LIMITS.symbols ||
    new Set(t.symbols).size !== t.symbols.length ||
    t.symbols.some((x) => typeof x !== 'string' || !/^\d{6}\.(SH|SZ)$/.test(x)) ||
    JSON.stringify(t.symbols) !== JSON.stringify([...t.symbols].sort())
  )
    fail('DATASET_SCOPE', '股票范围须排序、唯一且为1–50只沪深股票');
  date(t.start);
  date(t.end);
  if (
    t.start > t.end ||
    t.start < original.start ||
    t.end > original.end ||
    t.symbols.some((x) => !original.symbols.includes(x))
  )
    fail('DATASET_SCOPE', '目标范围必须位于原冻结范围内');
  const exact =
    canonical({ symbols: t.symbols, start: t.start, end: t.end }) === canonical(original);
  if ((t.mode === 'exact') !== exact)
    fail(
      'DATASET_SCOPE',
      exact ? '完整范围请明确选择 exact' : '子范围必须明确选择 explicit_subset'
    );
  return { symbols: t.symbols, start: t.start, end: t.end };
}
export async function resolveMarket(env, owner, source) {
  object(source, ['kind', 'runId', 'expectedBundleId', 'expectedSnapshotSha256', 'transform']);
  if (source.kind !== 'forecast_snapshot_view')
    fail('DATASET_SOURCE_NOT_ELIGIBLE', '仅支持已有完整预测包的冻结行情');
  id(source.runId);
  hash(source.expectedBundleId);
  hash(source.expectedSnapshotSha256);
  const stage = await ownedStageForRun(env, owner, source.runId);
  if (!stage) fail('NOT_FOUND', '冻结来源不存在', 404);
  if (stage.bundle_id !== source.expectedBundleId)
    fail('DATASET_SOURCE_CHANGED', '冻结来源版本不匹配', 409);
  const parsed = await parsedStage(stage),
    snapshot = parsed.metadata.snapshot,
    collection = parsed.collections.get('snapshotRows');
  if (
    parsed.manifest.kind !== 'forecast' ||
    snapshot?.schemaVersion !== 1 ||
    snapshot.fingerprintVersion !== 'research_input_v1' ||
    !collection ||
    collection.rowCount < 1 ||
    collection.rowCount > LIMITS.marketRows ||
    snapshot.provenance?.financialDatasetRoot ||
    snapshot.provenance?.financialInputs
  )
    fail('DATASET_SOURCE_NOT_ELIGIBLE', '来源须为完整且不含财务组成的冻结预测包', 409);
  const descriptor = parsed.manifest.documents.snapshot;
  if (descriptor.sha256 !== source.expectedSnapshotSha256)
    fail('DATASET_SOURCE_CHANGED', '行情快照版本不匹配', 409);
  integer(descriptor.byteLength, 1, LIMITS.sourceSnapshotBytes);
  const originalScope = sourceScope(parsed.metadata.forecast.sourceStrategy?.universe),
    scope = validateTransform(source.transform, originalScope);
  return {
    scope,
    originalScope,
    stageId: stage.id,
    runId: source.runId,
    bundleId: stage.bundle_id,
    manifest: {
      sha256: stage.bundle_id,
      byteLength: bytes(stage.manifest_text).length
    },
    snapshot: {
      sha256: descriptor.sha256,
      byteLength: descriptor.byteLength,
      rowCount: collection.rowCount,
      parts: collection.chunks
    },
    sourceStrategyHash: await sha(canonical(parsed.metadata.forecast.sourceStrategy))
  };
}
export async function resolveFinancial(env, owner, ref, index, scope) {
  object(ref, [
    'inputId',
    'preparationId',
    'inputRoot',
    'packRoot',
    'preparedRoot',
    'calendarRoot'
  ]);
  id(ref.inputId);
  id(ref.preparationId);
  for (const k of ['inputRoot', 'packRoot', 'preparedRoot', 'calendarRoot']) hash(ref[k]);
  const preparation = await ownedPreparation(env, owner, ref.preparationId);
  if (preparation.input_id !== ref.inputId)
    fail('DATASET_SOURCE_CHANGED', '准备结果不属于该输入', 409);
  const roots = parse(preparation.roots),
    manifest = parse(preparation.manifest_text),
    selection = manifest.summary.input.selection;
  for (const k of ['inputRoot', 'packRoot', 'preparedRoot', 'calendarRoot'])
    if (roots[k] !== ref[k]) fail('DATASET_SOURCE_CHANGED', '财务来源根不匹配', 409);
  if (
    selection.start !== scope.start ||
    selection.end !== scope.end ||
    selection.symbols.some((x) => !scope.symbols.includes(x))
  )
    fail(
      'DATASET_SCOPE_MISMATCH',
      '财务准备的日期必须与目标一致，成员必须包含于目标；请先建立明确修订',
      409
    );
  const input = await env.DB.prepare('SELECT * FROM financial_inputs WHERE id=? AND owner=?')
    .bind(ref.inputId, owner)
    .first();
  if (!input) fail('NOT_FOUND', '财务输入不存在', 404);
  const registry = await inputRegistry(env, input);
  if ([registry.calendar, ...registry.proofs].some((x) => x.owner !== owner))
    fail('DATASET_REGISTRY_OWNER', '数据集仅支持当前工作区的精确日历与证明授权', 409);
  const c = manifest.collections.package;
  return {
    source: {
      sourceId: `financial${index}`,
      inputId: input.id,
      preparationId: preparation.id,
      publicationId: preparation.publication_id,
      roots,
      selection,
      unitPolicy: manifest.summary.input.unitPolicy,
      calendarRef: input.calendar_ref,
      proofRefs: parse(input.proof_refs),
      package: { sha256: c.sha256, byteLength: c.byteLength, parts: c.chunks }
    },
    registry: [registry.calendar, ...registry.proofs]
  };
}
export async function resolveSources(env, owner, value, context = LEGACY_DATASET) {
  assertContext(context);
  const market = await resolveMarket(env, owner, value.marketSource),
    financial = [],
    byRef = new Map(),
    claimed = new Set();
  let packageBytes = 0;
  for (let i = 0; i < value.financialInputs.length; i++) {
    const resolved = await resolveFinancial(env, owner, value.financialInputs[i], i, market.scope);
    for (const symbol of resolved.source.selection.symbols)
      for (const state of resolved.source.selection.selectedStateIds) {
        const key = symbol + '/' + state;
        if (claimed.has(key))
          fail('DATASET_STATE_COLLISION', '财务输入存在重复股票与状态，请建立显式合并输入', 409);
        claimed.add(key);
      }
    financial.push(resolved.source);
    packageBytes += resolved.source.package.byteLength;
    for (const r of resolved.registry) byRef.set(r.id, r);
  }
  if (packageBytes > LIMITS.packageBytes) fail('DATASET_BUDGET', '财务来源包总量超过24MiB', 413);
  if (context.version === 3) {
    const span =
      (Date.parse(market.scope.end.replace(/^(....)(..)(..)$/, '$1-$2-$3')) -
        Date.parse(market.scope.start.replace(/^(....)(..)(..)$/, '$1-$2-$3'))) /
        86400000 +
      1;
    if (span > 366) fail('DATASET_SCOPE', '图式来源最多366个自然日');
    if (new Set(financial.map((f) => f.roots.packRoot)).size !== financial.length)
      fail('DATASET_SOURCE_DUPLICATE', '同一冻结财务包不能重复组成', 409);
    if (financial.reduce((n, f) => n + f.selection.selectedStateIds.length, 0) > 16)
      fail('DATASET_BUDGET', '图数据集累计财务状态超过16项', 413);
    financial.sort((a, b) => a.roots.packRoot.localeCompare(b.roots.packRoot));
    financial.forEach((f, i) => {
      f.sourceId = 'financial' + i;
    });
  }
  const entries = [...byRef.values()].sort((a, b) => a.id.localeCompare(b.id)),
    registryBytesTotal = entries.reduce((n, r) => n + r.byte_length, 0);
  if (registryBytesTotal > LIMITS.registryTotalBytes)
    fail('DATASET_BUDGET', '授权证据总量超过32MiB', 413);
  let selectedDates = null;
  const calendarRefs = [...new Set(financial.map((x) => x.calendarRef))].sort();
  for (const ref of calendarRefs) {
    const r = byRef.get(ref),
      calendar = parseStrictJson(new TextDecoder().decode(await registryBytes(env, r))),
      p = calendar.payload,
      financialRoot = financial.find((x) => x.calendarRef === ref).roots.calendarRoot;
    if (
      !p ||
      calendar.kind !== 'calendar' ||
      calendar.scope?.calendarRoot !== financialRoot ||
      p.complete !== true ||
      p.coverage_start > market.scope.start ||
      p.coverage_end < market.scope.end ||
      !Array.isArray(p.sessions)
    )
      fail('DATASET_CALENDAR_MISMATCH', '财务日历未覆盖目标范围或根不匹配', 409);
    const dates = p.sessions.filter((d) => market.scope.start <= d && d <= market.scope.end);
    if (
      !dates.length ||
      dates.some((d) => typeof d !== 'string' || !/^\d{8}$/.test(d)) ||
      canonical(dates) !== canonical([...new Set(dates)].sort()) ||
      (selectedDates && canonical(dates) !== canonical(selectedDates))
    )
      fail('DATASET_CALENDAR_MISMATCH', '财务来源日历在目标范围内不一致', 409);
    selectedDates = dates;
  }
  return {
    market,
    financial,
    marketCalendarRef: calendarRefs[0],
    registry: entries.map(({ id, kind, sha256, byte_length }) => ({
      ref: id,
      kind,
      sha256,
      byteLength: byte_length
    })),
    knownSourceBytes:
      market.manifest.byteLength + market.snapshot.byteLength + packageBytes + registryBytesTotal
  };
}
export function sourceDependencies(resolved) {
  return [
    {
      kind: 'source_bundle',
      referenceId: resolved.market.stageId,
      contentHash: resolved.market.bundleId
    },
    ...resolved.financial.flatMap((x) => [
      {
        kind: 'financial_input',
        referenceId: x.inputId,
        contentHash: x.roots.packRoot
      },
      {
        kind: 'financial_preparation',
        referenceId: x.preparationId,
        contentHash: x.roots.preparedRoot
      },
      {
        kind: 'financial_publication',
        referenceId: x.publicationId,
        contentHash: x.package.sha256
      }
    ]),
    ...resolved.registry.map((x) => ({
      kind: 'registry',
      referenceId: x.ref,
      contentHash: x.sha256
    }))
  ];
}
