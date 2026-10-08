/** Read-only local Miniflare export for independent audit. No public route. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
const sha = (raw) => createHash('sha256').update(raw).digest('hex');
export async function exportFinancialPreview(db, bucket, root, request) {
  if (
    !/^[a-f0-9-]{36}$/.test(request.requestId) ||
    (request.publicationId && !/^[a-f0-9-]{36}$/.test(request.publicationId))
  )
    throw Error('Explicit UUID request/publication required');
  const destination = path.join(root, 'private/financial-preview-exports', request.requestId);
  await fs.mkdir(destination, { recursive: true, mode: 0o700 });
  const query =
    "SELECT * FROM financial_publications WHERE status='committed'" +
    (request.publicationId ? ' AND id=?' : '');
  const publications = await (
    request.publicationId ? db.prepare(query).bind(request.publicationId) : db.prepare(query)
  ).all();
  const files = [];
  async function retain(relative, raw, expected) {
    if (expected && sha(raw) !== expected) throw Error('R2 byte hash mismatch: ' + relative);
    const target = path.join(destination, relative);
    await fs.mkdir(path.dirname(target), { recursive: true, mode: 0o700 });
    await fs.writeFile(target, raw, { mode: 0o600 });
    files.push({ path: relative, bytes: raw.length, sha256: sha(raw) });
  }
  for (const pub of publications.results) {
    await retain(pub.id + '/manifest.json', Buffer.from(pub.manifest_text), pub.manifest_hash);
    await retain(
      pub.id + '/publication.json',
      Buffer.from(
        JSON.stringify({
          id: pub.id,
          jobId: pub.job_id,
          manifestSha256: pub.manifest_hash,
          status: pub.status,
          totalBytes: pub.total_bytes,
        })
      )
    );
    const chunks = await db
      .prepare('SELECT * FROM financial_chunks WHERE publication_id=? ORDER BY collection,ordinal')
      .bind(pub.id)
      .all();
    for (const c of chunks.results) {
      const obj = await bucket.get(c.object_key);
      if (!obj || obj.size !== c.byte_length) throw Error('R2 chunk absent or changed');
      await retain(
        `${pub.id}/chunks/${c.collection}/${c.ordinal}.json`,
        Buffer.from(await obj.arrayBuffer()),
        c.sha256
      );
    }
  }
  const registry = await db.prepare('SELECT * FROM financial_registry_entries').all();
  for (const entry of registry.results) {
    const obj = await bucket.get(entry.object_key);
    if (!obj || obj.size !== entry.byte_length) throw Error('Registry absent or changed');
    await retain(
      'registry/' + entry.id + '.json',
      Buffer.from(await obj.arrayBuffer()),
      entry.sha256
    );
  }
  const receipt = {
    source: 'actual_local_Miniflare_R2_readback',
    requestId: request.requestId,
    destination,
    publications: publications.results.map((x) => x.id),
    files,
    createdAt: new Date().toISOString(),
  };
  await fs.writeFile(path.join(destination, 'receipt.json'), JSON.stringify(receipt, null, 2), {
    mode: 0o600,
  });
  return receipt;
}
