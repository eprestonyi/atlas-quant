/** Deterministic, private USTAR export of an already committed bundle. */
import { ApiError } from '../errors.mjs';
import { BUNDLE_PROFILE, COLLECTION_PATHS } from './profile.mjs';
import { readChunk } from './storage.mjs';
import { FINANCIAL_GRAPH_PROTOCOL } from '../financial-graph-bundles/manifest.mjs';
const archivePaths = (parsed) =>
  parsed.manifest.format === FINANCIAL_GRAPH_PROTOCOL.format && parsed.manifest.version === 2
    ? FINANCIAL_GRAPH_PROTOCOL.collectionPaths
    : COLLECTION_PATHS;

const encoder = new TextEncoder();
const block = 512;
const padding = (length) => (block - (length % block)) % block;
const invalid = () => new ApiError('BUNDLE_INTEGRITY', '复现包文件或字节不符合已提交清单', 503);

export function tarHeader(name, size, collectionPaths = COLLECTION_PATHS) {
  const match = /^chunks\/([A-Za-z][A-Za-z0-9]*)\/(0|[1-9][0-9]*)\.json$/.exec(name);
  if (name !== 'manifest.json' && (!match || !Object.hasOwn(collectionPaths, match[1])))
    throw invalid();
  if (name.length > 100 || !Number.isSafeInteger(size) || size < 0 || size > BUNDLE_PROFILE.bytes)
    throw invalid();
  const header = new Uint8Array(block);
  const text = (offset, value) => header.set(encoder.encode(value), offset);
  const octal = (offset, length, value) => {
    const digits = value.toString(8);
    if (digits.length >= length) throw invalid();
    text(offset, digits.padStart(length - 1, '0') + '\0');
  };
  text(0, name);
  octal(100, 8, 0o600);
  octal(108, 8, 0);
  octal(116, 8, 0);
  octal(124, 12, size);
  octal(136, 12, 0);
  header.fill(32, 148, 156);
  text(156, '0');
  text(257, 'ustar\0');
  text(263, '00');
  const checksum = header.reduce((total, value) => total + value, 0);
  text(148, checksum.toString(8).padStart(6, '0') + '\0 ');
  return header;
}

export function archiveEntries(stage, parsed) {
  const manifest = encoder.encode(stage.manifest_text);
  const entries = [{ name: 'manifest.json', size: manifest.byteLength, raw: manifest }];
  for (const collection of parsed.collections.values()) {
    for (const descriptor of collection.chunks) {
      entries.push({
        name: `chunks/${collection.id}/${descriptor.ordinal}.json`,
        size: descriptor.byteLength,
        collection: collection.id,
        descriptor
      });
    }
  }
  if (entries.length !== parsed.manifest.totals.chunkCount + 1) throw invalid();
  const names = new Set();
  for (const entry of entries) {
    if (names.has(entry.name)) throw invalid();
    names.add(entry.name);
    tarHeader(entry.name, entry.size, archivePaths(parsed));
  }
  return entries;
}

export function archiveLength(entries) {
  return entries.reduce((sum, entry) => sum + block + entry.size + padding(entry.size), block * 2);
}

/** Zero prefetch; at most the currently requested verified chunk is retained. */
export function archiveStream(entries, load, { collectionPaths = COLLECTION_PATHS } = {}) {
  let cancelled = false;
  async function* pieces() {
    for (const entry of entries) {
      if (cancelled) return;
      const raw = entry.raw ?? (await load(entry.collection, entry.descriptor));
      if (cancelled) return;
      if (!(raw instanceof Uint8Array) || raw.byteLength !== entry.size) throw invalid();
      yield tarHeader(entry.name, entry.size, collectionPaths);
      yield raw;
      if (padding(raw.byteLength)) yield new Uint8Array(padding(raw.byteLength));
    }
    if (!cancelled) yield new Uint8Array(block * 2);
  }
  const iterator = pieces();
  return new ReadableStream(
    {
      async pull(controller) {
        try {
          const next = await iterator.next();
          if (cancelled) return;
          if (next.done) controller.close();
          else controller.enqueue(next.value);
        } catch (error) {
          if (!cancelled) controller.error(error);
        }
      },
      async cancel() {
        cancelled = true;
        await iterator.return();
      }
    },
    { highWaterMark: 0 }
  );
}

export function bundleArchiveResponse(env, stage, parsed, runId) {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(runId) || stage.status !== 'committed') throw invalid();
  // parsedStage has already verified the exact raw manifest SHA and all paths.
  const entries = archiveEntries(stage, parsed);
  const length = archiveLength(entries);
  const source = archiveStream(
    entries,
    (collection, descriptor) => readChunk(env, stage, collection, descriptor),
    { collectionPaths: archivePaths(parsed) }
  );
  // Workers ignores a manually set Content-Length on an arbitrary stream.
  // A native fixed-length stream also makes truncation visible to HTTP clients.
  const fixed = new FixedLengthStream(length);
  source.pipeTo(fixed.writable).catch(() => {}); // The readable carries any failure.
  return new Response(fixed.readable, {
    headers: {
      'content-type': 'application/x-tar',
      'content-disposition': `attachment; filename="atlas-quant-${runId}-bundle.tar"`,
      'cache-control': 'private, no-store',
      'x-content-type-options': 'nosniff',
      'x-atlas-quant-bundle-id': stage.bundle_id
    }
  });
}
