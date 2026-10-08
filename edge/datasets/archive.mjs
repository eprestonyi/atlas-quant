/** Deterministic dataset USTAR: exact core path/header layout and bounded reads. */
import { bytes, hashBytes, fail, LIMITS, readObject } from './common.mjs';
const padding = (n) => (512 - (n % 512)) % 512;
export function datasetTarHeader(name, size) {
  if (
    name !== 'manifest.json' &&
    !/^parts\/[a-z][A-Za-z0-9]{0,39}\/(0|[1-9][0-9]*)\.bin$/.test(name)
  )
    fail('DATASET_ARCHIVE', '归档路径无效');
  if (
    name.length >= 100 ||
    !Number.isSafeInteger(size) ||
    size < 1 ||
    size > LIMITS.partBytes
  )
    fail('DATASET_ARCHIVE', '归档成员大小无效');
  const out = new Uint8Array(512),
    put = (i, v) => out.set(bytes(v), i),
    octal = (i, n, v) => put(i, v.toString(8).padStart(n - 1, '0') + '\0');
  put(0, name);
  octal(100, 8, 0o600);
  octal(108, 8, 0);
  octal(116, 8, 0);
  octal(124, 12, size);
  octal(136, 12, 0);
  out.fill(32, 148, 156);
  put(156, '0');
  put(257, 'ustar\0');
  put(263, '00');
  put(
    148,
    out
      .reduce((n, x) => n + x, 0)
      .toString(8)
      .padStart(6, '0') + '\0 ',
  );
  return out;
}
export async function datasetArchiveResponse(env, dataset, stage) {
  const raw = bytes(stage.manifest_text);
  if (
    stage.status !== 'committed' ||
    (await hashBytes(raw)) !== dataset.dataset_root
  )
    fail('DATASET_INTEGRITY', '归档清单不匹配', 409);
  const manifest = JSON.parse(stage.manifest_text),
    rows = await env.DB.prepare(
      'SELECT * FROM quant_dataset_parts WHERE stage_id=?',
    )
      .bind(stage.id)
      .all(),
    map = new Map(
      rows.results.map((r) => [r.component_id + '/' + r.ordinal, r]),
    ),
    entries = [{ name: 'manifest.json', byteLength: raw.length, raw }];
  for (const c of manifest.components)
    for (const p of c.parts) {
      const row = map.get(c.componentId + '/' + p.ordinal);
      if (!row || row.sha256 !== p.sha256 || row.byte_length !== p.byteLength)
        fail('DATASET_INCOMPLETE', '归档缺少完整分片', 409);
      entries.push({
        name: `parts/${c.componentId}/${p.ordinal}.bin`,
        ...p,
        key: row.object_key,
      });
    }
  if (entries.length - 1 !== rows.results.length)
    fail('DATASET_INCOMPLETE', '归档含未声明分片', 409);
  let cancelled = false;
  async function* pieces() {
    for (const e of entries) {
      if (cancelled) return;
      const data =
        e.raw ??
        (await readObject(
          env,
          e.key,
          e.sha256,
          e.byteLength,
          LIMITS.partBytes,
        ));
      if (cancelled) return;
      yield datasetTarHeader(e.name, e.byteLength);
      yield data;
      if (padding(data.length)) yield new Uint8Array(padding(data.length));
    }
    if (!cancelled) yield new Uint8Array(1024);
  }
  const iterator = pieces(),
    source = new ReadableStream(
      {
        async pull(controller) {
          try {
            const r = await iterator.next();
            if (cancelled) return;
            if (r.done) controller.close();
            else controller.enqueue(r.value);
          } catch (error) {
            if (!cancelled) controller.error(error);
          }
        },
        async cancel() {
          cancelled = true;
          await iterator.return();
        },
      },
      { highWaterMark: 0 },
    ),
    length = entries.reduce(
      (n, e) => n + 512 + e.byteLength + padding(e.byteLength),
      1024,
    ),
    fixed = new FixedLengthStream(length);
  source.pipeTo(fixed.writable).catch(() => {});
  return new Response(fixed.readable, {
    encodeBody: 'manual',
    headers: {
      'content-type': 'application/x-tar',
      'content-disposition': `attachment; filename="atlas-dataset-${dataset.id}.tar"`,
      'content-encoding': 'identity',
      'cache-control': 'private, no-store, no-transform',
      'x-content-type-options': 'nosniff',
      'x-atlas-dataset-root': dataset.dataset_root,
    },
  });
}
