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
    ref: row.id,
    kind: row.kind,
    sha256: row.sha256,
    byteLength: row.byte_length,
    ...parse(row.metadata, {}),
  };
}
export async function inputRegistry(env, input) {
  const refs = JSON.parse(input.proof_refs);
  const calendar = await registryEntry(env, input.owner, input.calendar_ref, 'calendar');
  const proofs = [];
  for (const ref of refs) proofs.push(await registryEntry(env, input.owner, ref, 'unit_proof'));
  if (
    [calendar, ...proofs].some((r) => r.byte_length > LIMITS.registryEntryBytes) ||
    [calendar, ...proofs].reduce((n, r) => n + r.byte_length, 0) > LIMITS.registryTotalBytes
  )
    fail('REGISTRY_BYTE_BUDGET', '证据集合超过字节限制', 413);
  return { calendar, proofs };
}
