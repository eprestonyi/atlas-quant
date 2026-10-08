import { ApiError } from '../errors.mjs';
import { HASH } from '../bundles/profile.mjs';
import { parseStrictJson, object } from '../bundles/json.mjs';
import { same } from './manifest.mjs';

const failure = () =>
  new ApiError('FINANCIAL_BUNDLE_SOURCE', '完整金融快照与原始数据集来源不一致', 409);
const encoder = new TextEncoder();

/** Extract only bounded metadata raw text. Strict JSON validation runs first;
 * no decoded numeric value is used to reconstruct source identity. */
export function topLevelRawMember(text, wanted) {
  const value = parseStrictJson(text, { codec: 'financial_json_v1' });
  if (!object(value) || !Object.hasOwn(value, wanted)) throw failure();
  const endString = (start) => {
    let escaped = false;
    for (let i = start + 1; i < text.length; i++) {
      if (escaped) escaped = false;
      else if (text[i] === '\\') escaped = true;
      else if (text[i] === '"') return i + 1;
    }
    throw failure();
  };
  let position = 1;
  while (position < text.length - 1) {
    const keyEnd = endString(position),
      key = JSON.parse(text.slice(position, keyEnd));
    const start = keyEnd + 1;
    let end = start,
      depth = 0;
    for (; end < text.length; end++) {
      const character = text[end];
      if (character === '"') {
        end = endString(end) - 1;
        continue;
      }
      if (character === '[' || character === '{') depth++;
      else if (character === ']' || character === '}') {
        if (depth === 0) break;
        depth--;
      } else if (character === ',' && depth === 0) break;
    }
    if (key === wanted) return text.slice(start, end);
    position = end + 1;
  }
  throw failure();
}

export function assertDatasetCommitment(current, parsed) {
  const manifest = current?.manifest,
    commitment = parsed.metadata.snapshot.financialSourceCommitment;
  const component = manifest?.components?.find((c) => c.componentId === 'researchRows');
  if (
    !object(manifest?.roots) ||
    !object(commitment) ||
    commitment.marketRoot !== manifest.roots.marketRoot ||
    commitment.financialDatasetRoot !== manifest.roots.financialDatasetRoot ||
    !component ||
    component.type !== 'research_rows' ||
    component.encoding !== 'raw_bytes' ||
    component.version !== 1 ||
    !HASH.test(component.payloadSha256) ||
    !Number.isSafeInteger(component.byteLength) ||
    component.byteLength < 2 ||
    component.byteLength > 24 * 1024 * 1024 ||
    !same(
      manifest.scope,
      Object.fromEntries(
        ['symbols', 'start', 'end'].map((key) => [
          key,
          parsed.metadata.forecast.sourceStrategy.universe[key]
        ])
      )
    )
  )
    throw failure();
  return component;
}

/** Hash the exact joined dataset payload from typed snapshot pieces. This is
 * byte equality to an admitted closure, not financial formula recomputation. */
export async function verifySnapshotDataset(current, parsed, readChunk) {
  const expected = assertDatasetCommitment(current, parsed);
  const text = parsed.manifest.documents.snapshot.parts
    .map(
      (part) =>
        part.literal ??
        (part.collection === 'snapshotRows'
          ? 'null'
          : (() => {
              throw failure();
            })())
    )
    .join('');
  const provenance = topLevelRawMember(text, 'provenance');
  const digest = new crypto.DigestStream('SHA-256'),
    writer = digest.getWriter();
  let total = 0;
  const write = async (raw) => {
    total += raw.byteLength;
    if (total > expected.byteLength) throw failure();
    await writer.write(raw);
  };
  try {
    await write(encoder.encode('{"provenance":' + provenance + ',"rows":['));
    for (const descriptor of parsed.collections.get('snapshotRows').chunks) {
      const raw = await readChunk('snapshotRows', descriptor);
      if (raw[0] !== 91 || raw[raw.length - 1] !== 93) throw failure();
      if (descriptor.ordinal) await write(encoder.encode(','));
      await write(raw.subarray(1, raw.length - 1));
    }
    await write(encoder.encode('],"schemaVersion":1}'));
    await writer.close();
    const hash = [...new Uint8Array(await digest.digest)]
      .map((x) => x.toString(16).padStart(2, '0'))
      .join('');
    if (total !== expected.byteLength || hash !== expected.payloadSha256) throw failure();
  } catch (error) {
    digest.digest.catch(() => {});
    try {
      await writer.abort(error);
    } catch {}
    throw error;
  } finally {
    writer.releaseLock();
  }
}
