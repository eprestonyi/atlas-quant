import { ApiError } from '../errors.mjs';
import { sha } from '../runtime.mjs';
import { BUNDLE_PROFILE, COLLECTION_PATHS, HASH } from './profile.mjs';
import { byteLength, keys, object, parseStrictJson } from './json.mjs';

const MARKER = '__atlas_quant_bundle_marker__';
const fail = (message, code = 'BUNDLE_MANIFEST', status = 400) => {
  throw new ApiError(code, message, status);
};
const integer = (value, maximum) => Number.isSafeInteger(value) && value >= 0 && value <= maximum;
const escapePointer = (value) => value.replaceAll('~', '~0').replaceAll('/', '~1');
function atPath(value, path) {
  for (const part of path.slice(1).split('/')) {
    if (!object(value) || !Object.hasOwn(value, part)) return undefined;
    value = value[part];
  }
  return value;
}

function skeleton(document, recipe, collections, artifactId) {
  const uses = new Set();
  let reference = false;
  const text = recipe.parts
    .map((part) => {
      if (!object(part)) fail('文档指令须为对象');
      if (Object.hasOwn(part, 'literal')) {
        keys(part, ['literal'], 'literal');
        if (typeof part.literal !== 'string') fail('literal 须为文本');
        return part.literal;
      }
      if (Object.hasOwn(part, 'collection')) {
        keys(part, ['collection'], 'collection');
        const collection = collections.get(part.collection);
        if (!collection || collection.document !== document || uses.has(collection.id)) {
          fail('集合引用重复或跨文档', 'BUNDLE_LAYOUT');
        }
        uses.add(collection.id);
        return JSON.stringify({ [MARKER]: collection.id });
      }
      keys(part, ['document', 'wrapArtifactId'], 'document reference');
      if (
        document !== 'report' ||
        reference ||
        part.document !== 'forecast' ||
        part.wrapArtifactId !== artifactId
      ) {
        fail('仅报告可引用同一预测文档一次', 'BUNDLE_LAYOUT');
      }
      reference = true;
      return JSON.stringify({ [MARKER]: '@forecast' });
    })
    .join('');
  if (byteLength(text) > BUNDLE_PROFILE.manifestBytes)
    fail('文档骨架超过预算', 'BUNDLE_BUDGET', 413);
  const root = parseStrictJson(text),
    found = new Set();
  if (!object(root)) fail('文档根节点须为对象');
  function visit(value, path = '') {
    if (object(value) && Object.hasOwn(value, MARKER)) {
      const id = value[MARKER];
      if (Object.keys(value).length !== 1 || found.has(id)) fail('骨架标记不唯一', 'BUNDLE_LAYOUT');
      found.add(id);
      if (id === '@forecast') {
        if (!reference || document !== 'report' || path !== '/forecasts')
          fail('预测引用位置不正确', 'BUNDLE_LAYOUT');
        return null;
      }
      const definition = collections.get(id);
      if (!uses.has(id) || !definition || definition.path !== path)
        fail('集合真实位置与声明不符', 'BUNDLE_LAYOUT');
      return [];
    }
    if (Array.isArray(value)) return value.map((item, index) => visit(item, path + '/' + index));
    if (object(value)) {
      for (const key of Object.keys(value))
        value[key] = visit(value[key], path + '/' + escapePointer(key));
    }
    return value;
  }
  const metadata = visit(root);
  for (const id of uses) if (!found.has(id)) fail('集合在实际文档中缺失', 'BUNDLE_LAYOUT');
  if (reference !== found.has('@forecast')) fail('报告缺少预测引用', 'BUNDLE_LAYOUT');
  if (document === 'report' && !reference) fail('报告必须引用完整预测文档', 'BUNDLE_LAYOUT');
  for (const [id, [owner, path]] of Object.entries(COLLECTION_PATHS)) {
    if (owner !== document) continue;
    const actual = atPath(metadata, path);
    if (actual !== undefined && (!Array.isArray(actual) || !uses.has(id))) {
      fail('已定义数组必须通过集合完整保存', 'BUNDLE_LAYOUT');
    }
  }
  return { metadata, uses };
}

/** Raw manifest identity is intentionally independent of JSON serialization. */
export async function validateManifest(manifestText, expectedId = null) {
  if (typeof manifestText !== 'string' || byteLength(manifestText) > BUNDLE_PROFILE.manifestBytes) {
    fail('manifest 超过 512 KiB', 'BUNDLE_BUDGET', 413);
  }
  const manifest = parseStrictJson(manifestText);
  keys(
    manifest,
    [
      'format',
      'version',
      'kind',
      'forecastArtifactId',
      'predictionConfigHash',
      'dataFingerprint',
      'documents',
      'collections',
      'totals'
    ],
    'manifest'
  );
  if (
    manifest.format !== 'atlas.quant.bundle' ||
    manifest.version !== 1 ||
    !['forecast', 'execution'].includes(manifest.kind)
  )
    fail('传输版本无效');
  for (const field of ['forecastArtifactId', 'predictionConfigHash', 'dataFingerprint']) {
    if (typeof manifest[field] !== 'string' || !HASH.test(manifest[field]))
      fail('产物身份须为 SHA256');
  }
  const bundleId = await sha(manifestText);
  if (expectedId !== null && bundleId !== expectedId)
    fail('manifest 原字节哈希不一致', 'BUNDLE_HASH');
  const documentNames =
    manifest.kind === 'forecast'
      ? ['forecast', 'report', 'snapshot', 'coverage']
      : ['forecast', 'report', 'coverage'];
  keys(manifest.documents, documentNames, 'documents');
  if (documentNames.some((name) => !Object.hasOwn(manifest.documents, name)))
    fail('完整文档集合缺失');
  if (
    !Array.isArray(manifest.collections) ||
    manifest.collections.length > Object.keys(COLLECTION_PATHS).length
  )
    fail('集合目录无效');
  const collections = new Map();
  let chunkCount = 0,
    chunkBytes = 0,
    rowCount = 0;
  for (const collection of manifest.collections) {
    keys(collection, ['id', 'document', 'path', 'rowCount', 'chunks'], 'collection');
    const definition = COLLECTION_PATHS[collection.id];
    if (
      !definition ||
      collections.has(collection.id) ||
      definition[0] !== collection.document ||
      definition[1] !== collection.path ||
      !documentNames.includes(collection.document)
    )
      fail('集合路径未定义或重复');
    if (!integer(collection.rowCount, BUNDLE_PROFILE.rows) || !Array.isArray(collection.chunks))
      fail('集合计数无效');
    let position = 0;
    for (const [ordinal, chunk] of collection.chunks.entries()) {
      keys(chunk, ['ordinal', 'start', 'count', 'sha256', 'byteLength'], 'chunk');
      if (
        chunk.ordinal !== ordinal ||
        chunk.start !== position ||
        !integer(chunk.count, BUNDLE_PROFILE.chunkRows) ||
        chunk.count < 1 ||
        !integer(chunk.byteLength, BUNDLE_PROFILE.chunkBytes) ||
        chunk.byteLength < 2 ||
        typeof chunk.sha256 !== 'string' ||
        !HASH.test(chunk.sha256)
      )
        fail('分片范围、哈希或大小无效');
      position += chunk.count;
      chunkCount++;
      chunkBytes += chunk.byteLength;
    }
    if (position !== collection.rowCount) fail('分片范围缺失或重叠');
    rowCount += collection.rowCount;
    collections.set(collection.id, collection);
  }
  keys(manifest.totals, ['chunkCount', 'chunkBytes'], 'totals');
  if (manifest.totals.chunkCount !== chunkCount || manifest.totals.chunkBytes !== chunkBytes)
    fail('父级总数不一致');
  if (
    chunkCount > BUNDLE_PROFILE.chunks ||
    chunkBytes + byteLength(manifestText) > BUNDLE_PROFILE.bytes ||
    rowCount > BUNDLE_PROFILE.rows
  )
    fail('研究分片超过父级预算', 'BUNDLE_BUDGET', 413);
  const metadata = {};
  for (const name of documentNames) {
    const document = manifest.documents[name];
    keys(document, ['parts', 'sha256', 'byteLength'], 'document');
    if (
      !Array.isArray(document.parts) ||
      document.parts.length > 2048 ||
      !document.parts.length ||
      !integer(document.byteLength, BUNDLE_PROFILE.bytes) ||
      document.byteLength < 2 ||
      typeof document.sha256 !== 'string' ||
      !HASH.test(document.sha256)
    )
      fail('文档指令或预算无效');
    const parsed = skeleton(name, document, collections, manifest.forecastArtifactId);
    for (const collection of collections.values())
      if (collection.document === name && !parsed.uses.has(collection.id))
        fail('不可达集合不能进入 manifest', 'BUNDLE_LAYOUT');
    metadata[name] = parsed.metadata;
  }
  if (manifest.documents.forecast.sha256 !== manifest.forecastArtifactId)
    fail('预测原 canonical 文档哈希必须等于 artifactId');
  for (const id of [
    'forecasts',
    'targets',
    'modelFits',
    'equity',
    'trades',
    'riskLedger',
    'plannedOrigins'
  ]) {
    if (!collections.has(id)) fail('完整研究缺少必需集合：' + id);
  }
  if (manifest.kind === 'forecast' && !collections.has('snapshotRows'))
    fail('预测研究缺少冻结数据');
  const forecast = metadata.forecast;
  if (
    forecast.schemaVersion !== 1 ||
    forecast.truncated !== false ||
    forecast.totalRows !== collections.get('forecasts').rowCount ||
    forecast.predictionConfigHash !== manifest.predictionConfigHash ||
    forecast.dataFingerprint !== manifest.dataFingerprint ||
    forecast.artifactId !== undefined
  )
    fail('预测身份或完整记录数不匹配');
  if (
    forecast.totalRows > 25000 ||
    collections.get('targets').rowCount > 110000 ||
    collections.get('modelFits').rowCount > 110000
  )
    fail('未扩容的数值维度超过上限', 'BUNDLE_BUDGET', 413);
  const report = metadata.report;
  if (
    report.schemaVersion !== 2 ||
    report.status !== 'completed' ||
    report.research?.mode !== 'statistical_quant' ||
    !object(report.strategy) ||
    !object(report.provenance) ||
    !object(report.selection)
  )
    fail('报告缺少版本化研究证据');
  const coverage = metadata.coverage;
  if (
    coverage.schemaVersion !== 1 ||
    !['samples_before_model_fitting', 'legacy_artifact_derived'].includes(coverage.source) ||
    typeof coverage.baselineRequired !== 'boolean' ||
    collections.get('plannedOrigins').rowCount !== forecast.totalRows
  )
    fail('独立覆盖计划无效');
  if (coverage.baselineRequired && collections.get('baselineRows')?.rowCount !== forecast.totalRows)
    fail('完整对照预测集合缺失');
  if (!coverage.baselineRequired && (collections.get('baselineRows')?.rowCount ?? 0) !== 0)
    fail('对照记录与计划不符');
  if (
    manifest.kind === 'forecast' &&
    (metadata.snapshot.schemaVersion !== 1 ||
      metadata.snapshot.dataFingerprint !== manifest.dataFingerprint ||
      !object(metadata.snapshot.provenance) ||
      collections.get('snapshotRows').rowCount > 110000 ||
      collections.get('snapshotRows').rowCount < 1)
  )
    fail('冻结数据格式或指纹无效');
  return { manifest, manifestText, bundleId, collections, metadata, rowCount };
}

export async function validateChunk(text, descriptor) {
  if (byteLength(text) !== descriptor.byteLength || (await sha(text)) !== descriptor.sha256) {
    fail('分片实际字节或哈希不一致', 'BUNDLE_HASH');
  }
  const rows = parseStrictJson(text);
  if (
    !Array.isArray(rows) ||
    rows.length !== descriptor.count ||
    rows.length > BUNDLE_PROFILE.chunkRows ||
    rows.some((row) => !object(row))
  )
    fail('分片须为完整记录数组，计数必须准确');
  return rows;
}
