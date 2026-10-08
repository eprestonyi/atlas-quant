import { ApiError } from '../errors.mjs';
import { body, json } from '../runtime.mjs';
import { BUNDLE_PROFILE } from '../bundles/profile.mjs';
import { readBoundedText } from '../bundles/json.mjs';
import { beginBundle, uploadChunk } from '../bundles/storage.mjs';
import { finalizeBundle } from '../bundles/verify.mjs';
import { completeBundle } from '../bundles/publication.mjs';
import { assertDatasetCommitment, verifySnapshotDataset } from './source.mjs';
import { FINANCIAL_FORMAT, validateFinancialManifest, same } from './manifest.mjs';
import {registeredFinancialProfile} from '../datasets/research-profile.mjs';

/** assertRunDataset is a fixed server module supplied by worker dispatch, never
 * a client claim. It rechecks ownership, immutable admission and current pins. */
export async function financialBundleRunnerApi(req, env, path, { assertRunDataset } = {}) {
  const prefixed = path.startsWith('/runner/financial-bundles/');
  if (!prefixed) return null;
  let admittedDataset = null;
  const authorize = async (bindings, job, parsed) => {
    if (typeof assertRunDataset !== 'function')
      throw new ApiError('FINANCIAL_DATASET_GATE', '金融数据集准入模块尚未连接', 503);
    const current = await assertRunDataset(bindings, job);
    if (
      !current ||
      !same(current.sourceEvidence, parsed.manifest.sourceEvidence) ||
      current.sourceEvidence?.datasetRef?.version !== 2 ||
      !registeredFinancialProfile(current.sourceEvidence?.admissionProfile, 2)
    )
      throw new ApiError('FINANCIAL_BUNDLE_SOURCE', '结果来源与原始数据集准入不一致', 409);
    assertDatasetCommitment(current, parsed);
    admittedDataset = current;
  };
  const options = {
    expectedFormat: FINANCIAL_FORMAT,
    authorize,
    verifySource: (parsed, readChunk) => verifySnapshotDataset(admittedDataset, parsed, readChunk)
  };
  if (path === '/runner/financial-bundles/complete' && req.method === 'POST') {
    const input = await body(req, 2000);
    if (
      input.result !== undefined ||
      input.error !== undefined ||
      !input.stageId ||
      !input.bundleId
    )
      throw new ApiError('INVALID_COMPLETION', '金融分片完成须引用既有 stage 与 bundle');
    return json(await completeBundle(env, input, options));
  }
  if (path === '/runner/financial-bundles/begin' && req.method === 'POST')
    return json(
      await beginBundle(env, await body(req, BUNDLE_PROFILE.manifestBytes * 3), {
        validate: validateFinancialManifest,
        authorize
      })
    );
  if (path === '/runner/financial-bundles/finalize' && req.method === 'POST')
    return json(await finalizeBundle(env, await body(req, 2000), options));
  const match =
    /^\/runner\/financial-bundles\/([a-f0-9]{64})\/chunks\/([a-zA-Z]+)\/(0|[1-9][0-9]{0,3})$/.exec(
      path
    );
  if (match && req.method === 'PUT') {
    const [, bundleId, collection, ordinal] = match;
    const input = {
      id: req.headers.get('X-Quant-Job'),
      leaseToken: req.headers.get('X-Quant-Lease'),
      stageId: req.headers.get('X-Quant-Stage'),
      bundleId
    };
    return json(
      await uploadChunk(
        env,
        input,
        collection,
        Number(ordinal),
        await readBoundedText(req, BUNDLE_PROFILE.chunkBytes),
        options
      )
    );
  }
  // No financial GET replay route exists. The dataset must be restored through
  // its own authorized reader, and execution is not part of this profile.
  if (prefixed) throw new ApiError('NOT_FOUND', '金融传输入口不存在', 404);
  return null;
}
