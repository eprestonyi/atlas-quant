/** Integrity-checked reads for legacy single-object artifacts. */
import { ApiError } from './errors.mjs';
import { sha } from './runtime.mjs';
export async function readPrivateObject(env, key, expectedHash = null) {
  const object = await env.ARTIFACTS.get(key);
  if (!object) throw new ApiError('ARTIFACT_UNAVAILABLE', '私有研究产物暂不可读取', 503);
  const text = await object.text();
  const contentHash = expectedHash ?? /\/([a-f0-9]{64})\.json$/.exec(key)?.[1];
  if (contentHash && (!/^[a-f0-9]{64}$/.test(contentHash) || (await sha(text)) !== contentHash))
    throw new ApiError('ARTIFACT_INTEGRITY', '私有研究产物完整性校验失败', 503);
  try {
    return JSON.parse(text);
  } catch {
    throw new ApiError('ARTIFACT_INTEGRITY', '私有研究产物格式校验失败', 503);
  }
}
