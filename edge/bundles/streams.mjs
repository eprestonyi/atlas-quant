import { ApiError } from '../errors.mjs';
import { BUNDLE_PROFILE } from './profile.mjs';

const encoder = new TextEncoder();
const bytes = (value) => encoder.encode(value);
const hex = (value) =>
  [...new Uint8Array(value)].map((part) => part.toString(16).padStart(2, '0')).join('');
const integrity = () => new ApiError('BUNDLE_INTEGRITY', '完整研究分片校验失败', 503);

export async function* withoutBraces(pieces) {
  let pending = null,
    first = true;
  for await (let piece of pieces) {
    if (!piece.byteLength) continue;
    if (first) {
      if (piece[0] !== 123) throw integrity();
      piece = piece.subarray(1);
      first = false;
    }
    if (pending !== null) yield pending;
    pending = piece;
  }
  if (first || !pending?.byteLength || pending[pending.byteLength - 1] !== 125) throw integrity();
  yield pending.subarray(0, pending.byteLength - 1);
}

/** Reconstruct canonical bytes, never JSON.stringify a decoded numeric value. */
export async function* documentPieces(parsed, name, readChunk) {
  const recipe = parsed.manifest.documents[name];
  if (!recipe) throw integrity();
  for (const part of recipe.parts) {
    if (Object.hasOwn(part, 'literal')) {
      yield bytes(part.literal);
    } else if (part.collection) {
      const collection = parsed.collections.get(part.collection);
      yield bytes('[');
      for (const descriptor of collection.chunks) {
        const raw = await readChunk(collection.id, descriptor);
        if (raw.byteLength !== descriptor.byteLength || raw[0] !== 91 || raw[raw.length - 1] !== 93)
          throw integrity();
        if (descriptor.ordinal) yield bytes(',');
        yield raw.subarray(1, raw.byteLength - 1);
      }
      yield bytes(']');
    } else {
      yield bytes('{"artifactId":"' + part.wrapArtifactId + '",');
      yield* withoutBraces(documentPieces(parsed, 'forecast', readChunk));
      yield bytes('}');
    }
  }
}

/** Cloudflare's native digest stream does not retain the complete document. */
export async function* verifiedDocumentPieces(parsed, name, readChunk) {
  const digest = new crypto.DigestStream('SHA-256');
  const writer = digest.getWriter();
  let length = 0,
    finished = false;
  try {
    for await (const piece of documentPieces(parsed, name, readChunk)) {
      length += piece.byteLength;
      if (length > BUNDLE_PROFILE.bytes) throw integrity();
      await writer.write(piece);
      yield piece;
    }
    await writer.close();
    finished = true;
    const expected = parsed.manifest.documents[name];
    if (length !== expected.byteLength || hex(await digest.digest) !== expected.sha256)
      throw integrity();
  } catch (error) {
    // Drain a rejected digest to avoid an unhandled promise after stream failure.
    digest.digest.catch(() => {});
    try {
      await writer.abort(error);
    } catch {
      /* Closed or already aborted. */
    }
    throw error;
  } finally {
    if (!finished) {
      digest.digest.catch(() => {});
      try {
        await writer.abort(new Error('Stream cancelled'));
      } catch {}
    }
    writer.releaseLock();
  }
}

export async function verifyDocuments(parsed, readChunk) {
  for (const name of Object.keys(parsed.manifest.documents)) {
    for await (const piece of verifiedDocumentPieces(parsed, name, readChunk)) {
      void piece;
    }
  }
}

export function documentStream(
  parsed,
  name,
  readChunk,
  { prefix = '', suffix = '', unwrap = false } = {}
) {
  async function* framed() {
    if (prefix) yield bytes(prefix);
    const verified = verifiedDocumentPieces(parsed, name, readChunk);
    yield* unwrap ? withoutBraces(verified) : verified;
    if (suffix) yield bytes(suffix);
  }
  const iterator = framed();
  return new ReadableStream({
    async pull(controller) {
      try {
        const next = await iterator.next();
        if (next.done) controller.close();
        else controller.enqueue(next.value);
      } catch (error) {
        controller.error(error);
      }
    },
    async cancel() {
      await iterator.return();
    }
  });
}
