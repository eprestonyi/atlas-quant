import { fail, id, parse, hashBytes, LIMITS } from './common.mjs';
/** Operator registry writes have no public HTTP endpoint. */
export async function registryEntry(env, owner, ref, kind) {
  const row = await env.DB.prepare(
    "SELECT * FROM financial_registry_entries WHERE id=? AND (owner=? OR owner='*') AND status='active'"
  )
    .bind(id(ref), owner)
    .first();
  if (!row || (kind && row.kind !== kind)) fail('NOT_FOUND', '授权证据不存在', 404);
  return row;
}
export async function registryBytes(env, row) {
  const obj = await env.ARTIFACTS.get(row.object_key);
  if (!obj || obj.size !== row.byte_length || obj.size > LIMITS.registryEntryBytes)
    fail('REGISTRY_INTEGRITY', '证据内容不可读取', 409);
  const raw = new Uint8Array(await obj.arrayBuffer());
  if ((await hashBytes(raw)) !== row.sha256) fail('REGISTRY_INTEGRITY', '证据哈希不匹配', 409);
  return raw;
}
export function registryDTO(row) {
  return {
    ...parse(row.metadata, {}),
    ref: row.id,
    kind: row.kind,
    sha256: row.sha256,
    byteLength: row.byte_length,
  };
}
export async function inputRegistry(env, input) {
  const refs = parse(input.proof_refs);
  if (!Array.isArray(refs) || refs.length > 256 || new Set(refs).size !== refs.length)
    fail('INVALID_INPUT', '证明引用需要唯一且最多256项');
  const allRefs = [id(input.calendar_ref), ...refs.map(id)];
  const byId = new Map();
  // Stay below D1's bind limit, including the owner parameter. Resolve all
  // descriptors once, then reconstruct the original immutable proof order.
  for (let offset = 0; offset < allRefs.length; offset += 64) {
    const batch = allRefs.slice(offset, offset + 64);
    const rows = await env.DB.prepare(
      `SELECT * FROM financial_registry_entries WHERE id IN (${batch.map(() => '?').join(',')}) AND (owner=? OR owner='*') AND status='active'`
    )
      .bind(...batch, input.owner)
      .all();
    for (const row of rows.results) byId.set(row.id, row);
  }
  const entry = (ref, kind) => {
    const row = byId.get(ref);
    if (!row || row.kind !== kind) fail('NOT_FOUND', '授权证据不存在', 404);
    return row;
  };
  const calendar = entry(input.calendar_ref, 'calendar');
  const proofs = refs.map((ref) => entry(ref, 'unit_proof'));
  if (
    [calendar, ...proofs].some((r) => r.byte_length > LIMITS.registryEntryBytes) ||
    [calendar, ...proofs].reduce((n, r) => n + r.byte_length, 0) > LIMITS.registryTotalBytes
  )
    fail('REGISTRY_BYTE_BUDGET', '证据集合超过字节限制', 413);
  return { calendar, proofs };
}
