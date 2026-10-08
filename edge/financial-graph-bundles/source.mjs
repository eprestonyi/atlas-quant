/** Exact graph source-byte commitment; no financial formula or F recomputation. */
import { ApiError } from '../errors.mjs';
import { HASH } from '../bundles/profile.mjs';
import { parseStrictJson, object, byteLength } from '../bundles/json.mjs';
import { topLevelRawMember } from '../financial-bundles/source.mjs';
import { same } from '../financial-bundles/manifest.mjs';
import { readObject } from '../datasets/common.mjs';
const limit = 24 * 1024 * 1024;
const encoder = new TextEncoder();
const fail = () => {
  throw new ApiError('FINANCIAL_GRAPH_BUNDLE_SOURCE', '金融列快照与已批准完整数据集不一致', 409);
};

export async function assertGraphDatasetCommitment(env, current, parsed) {
  const m = current?.manifest,
    s = parsed.metadata.snapshot,
    c = s.financialSourceCommitment;
  const part = m?.components?.find((x) => x.componentId === 'researchColumns');
  if (
    m?.version !== 3 ||
    m.profile !== 'financial_snapshot_graph_50_v1' ||
    !object(c) ||
    c.marketRoot !== m.roots?.marketRoot ||
    c.financialDatasetRoot !== m.roots?.financialDatasetRoot ||
    !part ||
    part.type !== 'research_columns' ||
    part.encoding !== 'raw_bytes' ||
    part.version !== 1 ||
    !HASH.test(part.payloadSha256) ||
    !Number.isSafeInteger(part.byteLength) ||
    part.byteLength < 2 ||
    part.byteLength > limit ||
    !same(
      m.scope,
      Object.fromEntries(
        ['symbols', 'start', 'end'].map((k) => [
          k,
          parsed.metadata.forecast.sourceStrategy.universe[k]
        ])
      )
    )
  )
    fail();
  const d = part.parts[0],
    stored = await env.DB.prepare(
      "SELECT * FROM quant_dataset_parts WHERE stage_id=? AND component_id='researchColumns' AND ordinal=0"
    )
      .bind(current.stage.id)
      .first();
  if (!stored || stored.sha256 !== d.sha256 || stored.byte_length !== d.byteLength) fail();
  const raw = await readObject(env, stored.object_key, d.sha256, d.byteLength, 512 * 1024);
  // Canonical source order puts this bounded descriptor before numericInput.
  // Never parse or retain the complete potentially 24 MiB source component.
  const prefix = new TextDecoder('utf-8').decode(raw.subarray(0, 256));
  const end = prefix.indexOf('},"numericInput":');
  if (!prefix.startsWith('{"logicalJoined":') || end < 0) fail();
  const literal = prefix.slice('{"logicalJoined":'.length, end + 1);
  const logicalJoined = parseStrictJson(literal, {
    codec: 'financial_json_v1'
  });
  if (
    !same(Object.keys(logicalJoined).sort(), ['byteLength', 'sha256']) ||
    logicalJoined.sha256 !== part.semanticRoots.logicalJoinedSha256 ||
    !Number.isSafeInteger(logicalJoined.byteLength) ||
    logicalJoined.byteLength < 2 ||
    logicalJoined.byteLength > limit
  )
    fail();
  return { component: part, logicalJoinedLiteral: literal, scope: m.scope };
}

export async function verifyGraphSnapshotDataset(commitment, parsed, read) {
  if (!commitment) fail();
  const recipe = parsed.manifest.documents.snapshot;
  const skeleton = recipe.parts
    .map((p) => p.literal ?? (p.collection === 'snapshotColumns' ? 'null' : fail()))
    .join('');
  const provenance = topLevelRawMember(skeleton, 'provenance');
  const table = topLevelRawMember(skeleton, 'numericInput');
  if (!table.startsWith('{"columns":null,')) fail();
  // The independently admitted logical rows include exact type and signed-zero
  // spellings. Bound the full expanded snapshot envelope as well as physical bytes.
  const rawMeta = parseStrictJson(skeleton, {
    codec: 'financial_column_snapshot_v1'
  });
  const other = Object.keys(rawMeta).filter((k) => k !== 'numericInput');
  let expanded = 2 + other.length; // object braces and commas between N+1 members
  for (const k of other)
    expanded += byteLength(JSON.stringify(k)) + 1 + byteLength(topLevelRawMember(skeleton, k));
  expanded += byteLength('"rows":') + rawMeta.numericInput.logicalRows.byteLength;
  if (expanded > limit) fail();
  const digest = new crypto.DigestStream('SHA-256'),
    writer = digest.getWriter();
  let length = 0;
  const write = async (x) => {
    const raw = typeof x === 'string' ? encoder.encode(x) : x;
    length += raw.byteLength;
    if (length > commitment.component.byteLength) fail();
    await writer.write(raw);
  };
  try {
    await write(
      '{"logicalJoined":' + commitment.logicalJoinedLiteral + ',"numericInput":{"columns":['
    );
    for (const d of parsed.collections.get('snapshotColumns').chunks) {
      const raw = await read('snapshotColumns', d);
      if (raw[0] !== 91 || raw.at(-1) !== 93) fail();
      if (d.ordinal) await write(',');
      await write(raw.subarray(1, raw.length - 1));
    }
    await write(
      ']' +
        table.slice('{"columns":null'.length) +
        ',"provenance":' +
        provenance +
        ',"schemaVersion":1}'
    );
    await writer.close();
    const sha = [...new Uint8Array(await digest.digest)]
      .map((x) => x.toString(16).padStart(2, '0'))
      .join('');
    if (length !== commitment.component.byteLength || sha !== commitment.component.payloadSha256)
      fail();
  } catch (e) {
    digest.digest.catch(() => {});
    try {
      await writer.abort(e);
    } catch {}
    throw e;
  } finally {
    writer.releaseLock();
  }
}
