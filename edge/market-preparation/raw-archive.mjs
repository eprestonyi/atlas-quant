/** Group original response bodies without changing one byte or issuing a provider read. */
import { object, integer, hashBytes } from "../financial/common.mjs";
import { hash, fail } from "./common.mjs";
export const RAW_ARCHIVE_LIMITS = Object.freeze({
  rawChunkBytes: 4 * 1024 * 1024,
  rawChunks: 256,
  rawBytes: 512 * 1024 * 1024,
});

export function validateRawArchive(value, receiptCount) {
  object(value, ["chunks", "byteLength", "receiptCount"]);
  if (
    value.receiptCount !== receiptCount ||
    !Array.isArray(value.chunks) ||
    !value.chunks.length ||
    value.chunks.length > RAW_ARCHIVE_LIMITS.rawChunks
  )
    fail("MARKET_RAW_ARCHIVE", "原始回执归档数量无效");
  integer(value.byteLength, 1, RAW_ARCHIVE_LIMITS.rawBytes);
  let bytes = 0;
  for (const [ordinal, p] of value.chunks.entries()) {
    object(p, ["ordinal", "sha256", "byteLength"]);
    hash(p.sha256);
    if (p.ordinal !== ordinal) fail("MARKET_RAW_ARCHIVE", "原始归档块必须连续");
    integer(p.byteLength, 1, RAW_ARCHIVE_LIMITS.rawChunkBytes);
    bytes += p.byteLength;
  }
  if (bytes !== value.byteLength)
    fail("MARKET_RAW_ARCHIVE", "原始归档累计长度不同");
}

/** All D1 receipts were loaded once by the caller; at most 256 bounded R2 reads. */
export async function verifyRawArchive(manifest, receipts, read) {
  const groups = manifest.rawArchive.chunks.map(() => []);
  let last = 0;
  for (const receipt of receipts) {
    const location = receipt.rawLocation;
    object(location, ["ordinal", "offset", "byteLength"]);
    integer(location.ordinal, 0, groups.length - 1);
    integer(location.offset, 0, RAW_ARCHIVE_LIMITS.rawChunkBytes);
    if (
      location.byteLength !== receipt.byteLength ||
      location.ordinal < last ||
      location.ordinal > last + 1
    )
      fail("MARKET_RAW_ARCHIVE", "原始回执位置或长度不同", 409);
    groups[location.ordinal].push(receipt);
    last = location.ordinal;
  }
  for (const part of manifest.rawArchive.chunks) {
    const raw = await read(part);
    let offset = 0;
    if (!groups[part.ordinal].length)
      fail("MARKET_RAW_ARCHIVE", "原始归档含无引用片段", 409);
    for (const receipt of groups[part.ordinal]) {
      const loc = receipt.rawLocation;
      if (
        loc.offset !== offset ||
        offset + loc.byteLength > raw.length ||
        (await hashBytes(raw.subarray(offset, offset + loc.byteLength))) !==
          receipt.sha256
      )
        fail("MARKET_RAW_ARCHIVE", "原始归档不能重现已保存的准确回执", 409);
      offset += loc.byteLength;
    }
    if (offset !== raw.length)
      fail("MARKET_RAW_ARCHIVE", "原始归档含未声明尾部字节", 409);
  }
}
