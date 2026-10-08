/** Private full source closure, at most 576 R2 reads; no per-request raw GET loop. */
import { canonical, fail, digest } from "./common.mjs";
import { hashBytes } from "../financial/common.mjs";
import {
  outputCollections,
  readPart,
  PUBLICATION_LIMITS,
} from "./publication.mjs";
const bytes = (text) => new TextEncoder().encode(text);
const padding = (n) => (512 - (n % 512)) % 512;

export function marketTarHeader(name, size) {
  if (
    !["manifest.json", "plan.json", "scope.json"].includes(name) &&
    !/^parts\/(rows|receipts|provenance|raw)\/(0|[1-9][0-9]{0,2})\.bin$/.test(
      name,
    )
  )
    fail("MARKET_ARCHIVE", "归档路径不合法");
  if (
    !Number.isSafeInteger(size) ||
    size < 1 ||
    size > PUBLICATION_LIMITS.rawChunkBytes
  )
    fail("MARKET_ARCHIVE", "归档文件大小不合法");
  const out = new Uint8Array(512),
    put = (i, v) => out.set(bytes(v), i),
    octal = (i, n, v) => put(i, v.toString(8).padStart(n - 1, "0") + "\0");
  put(0, name);
  octal(100, 8, 0o600);
  octal(108, 8, 0);
  octal(116, 8, 0);
  octal(124, 12, size);
  octal(136, 12, 0);
  out.fill(32, 148, 156);
  put(156, "0");
  put(257, "ustar\0");
  put(263, "00");
  put(
    148,
    out
      .reduce((a, b) => a + b, 0)
      .toString(8)
      .padStart(6, "0") + "\0 ",
  );
  return out;
}

export async function marketArchiveResponse(env, admitted, owner) {
  const { dataset, manifest, plan } = admitted;
  const [job, scope] = await Promise.all([
    env.DB.prepare(
      "SELECT * FROM quant_market_jobs WHERE id=? AND owner=? AND status='completed'",
    )
      .bind(dataset.job_id, owner)
      .first(),
    env.DB.prepare(
      "SELECT spec FROM quant_universe_scopes WHERE id=? AND owner=? AND scope_root=?",
    )
      .bind(
        manifest.universeScopeRef.scopeId,
        owner,
        manifest.universeScopeRef.scopeRoot,
      )
      .first(),
  ]);
  if (
    !job ||
    !scope ||
    (await hashBytes(bytes(scope.spec))) !== manifest.universeScopeRef.scopeRoot
  )
    fail("MARKET_ARCHIVE", "来源范围或任务缺失", 409);
  const entries = [
    { name: "manifest.json", raw: bytes(dataset.manifest) },
    { name: "plan.json", raw: bytes(canonical(plan)) },
    { name: "scope.json", raw: bytes(scope.spec) },
  ];
  for (const [collection, c] of Object.entries(outputCollections(manifest)))
    for (const p of c.chunks)
      entries.push({
        name: `parts/${collection}/${p.ordinal}.bin`,
        collection,
        descriptor: p,
        size: p.byteLength,
      });
  if (entries.length > 579) fail("MARKET_ARCHIVE", "完整归档超过块预算", 413);
  for (const e of entries) {
    e.size ??= e.raw.length;
    marketTarHeader(e.name, e.size);
  }
  let cancelled = false;
  async function* pieces() {
    for (const e of entries) {
      if (cancelled) return;
      const raw =
        e.raw ??
        (await readPart(env, job, manifest, e.collection, e.descriptor));
      if (cancelled) return;
      yield marketTarHeader(e.name, e.size);
      yield raw;
      if (padding(raw.length)) yield new Uint8Array(padding(raw.length));
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
          } catch (e) {
            if (!cancelled) controller.error(e);
          }
        },
        async cancel() {
          cancelled = true;
          await iterator.return();
        },
      },
      { highWaterMark: 0 },
    );
  const length = entries.reduce(
      (n, e) => n + 512 + e.size + padding(e.size),
      1024,
    ),
    fixed = new FixedLengthStream(length);
  source.pipeTo(fixed.writable).catch(() => {});
  return new Response(fixed.readable, {
    encodeBody: "manual",
    headers: {
      "content-type": "application/x-tar",
      "content-disposition": `attachment; filename="atlas-market-${dataset.id}.tar"`,
      "content-encoding": "identity",
      "cache-control": "private, no-store, no-transform",
      "x-content-type-options": "nosniff",
      "x-atlas-market-root": dataset.dataset_root,
    },
  });
}
