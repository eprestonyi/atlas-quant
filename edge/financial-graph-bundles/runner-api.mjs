/** Independent result endpoint: no caller flag can upgrade a legacy stage. */
import { ApiError } from '../errors.mjs';
import { body, json } from '../runtime.mjs';
import { BUNDLE_PROFILE } from '../bundles/profile.mjs';
import { readBoundedText } from '../bundles/json.mjs';
import { beginBundle, uploadChunk } from '../bundles/storage.mjs';
import { finalizeBundle } from '../bundles/verify.mjs';
import { completeBundle } from '../bundles/publication.mjs';
import { same } from '../financial-bundles/manifest.mjs';
import {
  FINANCIAL_FORMAT,
  validateFinancialGraphManifest,
  validateGraphRows
} from './manifest.mjs';
import { assertGraphDatasetCommitment, verifyGraphSnapshotDataset } from './source.mjs';
import { verifyGraphCoverage } from './coverage.mjs';
import { assertRunDataset, FINANCIAL_GRAPH_PROFILE } from '../datasets/research.mjs';
const base = '/runner/financial-graph-bundles';
export async function financialGraphBundleRunnerApi(req, env, path) {
  if (!path.startsWith(base + '/')) return null;
  let commitment = null;
  const authorize = async (bindings, job, parsed) => {
    const current = await assertRunDataset(bindings, job);
    if (
      current.datasetRef.version !== 3 ||
      current.admissionProfile !== FINANCIAL_GRAPH_PROFILE ||
      !same(current.sourceEvidence, parsed.manifest.sourceEvidence)
    )
      throw new ApiError('FINANCIAL_GRAPH_BUNDLE_SOURCE', '图结果须对应已批准版本3数据集', 409);
    commitment = await assertGraphDatasetCommitment(bindings, current, parsed);
  };
  const options = {
    expectedFormat: FINANCIAL_FORMAT,
    expectedVersion: 2,
    authorize,
    validateRows: validateGraphRows,
    verifySource: async (parsed, read, { env, stage }) => {
      await verifyGraphSnapshotDataset(commitment, parsed, read);
      await verifyGraphCoverage(env, stage, parsed, commitment, read);
    }
  };
  if (path === base + '/begin' && req.method === 'POST')
    return json(
      await beginBundle(env, await body(req, BUNDLE_PROFILE.manifestBytes * 3), {
        validate: validateFinancialGraphManifest,
        authorize
      })
    );
  if (path === base + '/finalize' && req.method === 'POST')
    return json(await finalizeBundle(env, await body(req, 2000), options));
  if (path === base + '/complete' && req.method === 'POST') {
    const input = await body(req, 2000);
    if (
      input.result !== undefined ||
      input.error !== undefined ||
      !input.stageId ||
      !input.bundleId
    )
      throw new ApiError('INVALID_COMPLETION', '图结果完成须引用已验证分片');
    return json(await completeBundle(env, input, options));
  }
  const match =
    /^\/runner\/financial-graph-bundles\/([a-f0-9]{64})\/chunks\/([a-zA-Z]+)\/(0|[1-9][0-9]{0,3})$/.exec(
      path
    );
  if (match && req.method === 'PUT')
    return json(
      await uploadChunk(
        env,
        {
          id: req.headers.get('X-Quant-Job'),
          leaseToken: req.headers.get('X-Quant-Lease'),
          stageId: req.headers.get('X-Quant-Stage'),
          bundleId: match[1]
        },
        match[2],
        Number(match[3]),
        await readBoundedText(req, BUNDLE_PROFILE.chunkBytes),
        options
      )
    );
  throw new ApiError('NOT_FOUND', '图金融结果仅开放独立上传，不支持执行重放', 404);
}
