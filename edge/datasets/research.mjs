/** Forecast admission binds a ready closure, not caller-supplied data or trust. */
import { validateExpression } from '../validation.mjs';
import definitions from '../financial/definitions.json' with { type: 'json' };
import {
  PROFILE,
  LIMITS,
  protocol,
  object,
  id,
  hash,
  fail,
  parse,
  bytes,
  hashBytes,
  datasetRef,
  canonical,
  requireEnabled
} from './common.mjs';
import { assertRegistry } from './transport.mjs';
import { datasetContext, assertPlanContext } from './context.mjs';
import {
  FINANCIAL_AUTO_PROFILE,
  FINANCIAL_GRAPH_PROFILE,
  financialProfileVersion,
  registeredFinancialProfile,
  assertFinancialResearchConfig
} from './research-profile.mjs';
export {
  FINANCIAL_AUTO_PROFILE,
  FINANCIAL_GRAPH_PROFILE,
  financialProfileVersion
} from './research-profile.mjs';

const stateIds = new Set(definitions.items.map((x) => x.id));
export const researchEnabled = (env, version = 2) =>
  version === 3
    ? env.FINANCIAL_GRAPH_RESEARCH_ENABLED === 'true' &&
      env.RESEARCH_DATASET_GRAPHS_ENABLED === 'true'
    : version === 2 &&
      env.FINANCIAL_DATASET_RESEARCH_ENABLED === 'true' &&
      env.RESEARCH_DATASETS_ENABLED === 'true';
export function requireResearchEnabled(env, version = 2) {
  requireEnabled(env, datasetContext(version));
  if (!researchEnabled(env, version))
    fail('DATASET_RESEARCH_UNAVAILABLE', '财务数据集模型研究尚未开放', 503);
}
export function financialFields(strategy) {
  return [
    ...new Set(
      (strategy.factors || [])
        .flatMap((f) => validateExpression(f.expression).fields)
        .filter((f) => f.startsWith('model_fin_'))
    )
  ];
}
export function supportsFinancialDatasets(input) {
  if (![input.datasetFormats, input.snapshotFormats, input.transportFormats].every(Array.isArray))
    return false;
  return (
    ['atlas.quant.research_dataset/2'].every((x) => input.datasetFormats?.includes(x)) &&
    ['financial_json_v1'].every((x) => input.snapshotFormats?.includes(x)) &&
    ['atlas.quant.financial_bundle/1'].every((x) => input.transportFormats?.includes(x))
  );
}
export function supportsFinancialResearchProfile(input, profile) {
  if (profile === FINANCIAL_GRAPH_PROFILE)
    return [
      ['datasetFormats', 'atlas.quant.research_dataset/3'],
      ['snapshotFormats', 'financial_column_snapshot_v1'],
      ['transportFormats', 'atlas.quant.financial_bundle/2'],
      ['financialResearchProfiles', FINANCIAL_GRAPH_PROFILE]
    ].every(([field, value]) => Array.isArray(input?.[field]) && input[field].includes(value));
  return (
    supportsFinancialDatasets(input) &&
    (profile === PROFILE ||
      (profile === FINANCIAL_AUTO_PROFILE &&
        Array.isArray(input.financialResearchProfiles) &&
        input.financialResearchProfiles.includes(profile)))
  );
}

export function acceptedFinancialProfiles(env, input) {
  return [PROFILE, FINANCIAL_AUTO_PROFILE, FINANCIAL_GRAPH_PROFILE].filter(
    (profile) =>
      researchEnabled(env, financialProfileVersion(profile)) &&
      supportsFinancialResearchProfile(input, profile)
  );
}

export function validateDatasetRef(value) {
  object(value, ['datasetId', 'datasetRoot', 'format', 'version']);
  id(value.datasetId);
  hash(value.datasetRoot);
  if (value.format !== protocol.datasetFormat || ![2, 3].includes(value.version))
    fail('DATASET_FORMAT', '需要明确的研究数据集版本2或3');
  return value;
}
export async function readyDataset(env, owner, ref) {
  validateDatasetRef(ref);
  const context = datasetContext(ref.version);
  const row = await env.DB.prepare(
    `SELECT d.*,s.manifest_text,s.total_bytes,s.job_id composition_job_id,s.status stage_status,p.spec plan_spec
    FROM quant_research_datasets d JOIN quant_dataset_stages s ON s.id=d.stage_id AND s.owner=d.owner
    JOIN quant_dataset_jobs j ON j.id=s.job_id AND j.owner=d.owner
    JOIN quant_dataset_plans p ON p.id=j.plan_id AND p.owner=d.owner
    WHERE d.id=? AND d.owner=? AND d.dataset_root=?`
  )
    .bind(ref.datasetId, owner, ref.datasetRoot)
    .first();
  if (!row) fail('NOT_FOUND', '研究数据集不存在或身份不匹配', 404);
  if (row.status !== 'ready' || row.stage_status !== 'committed')
    fail('DATASET_NOT_READY', '数据集尚未完成来源闭包核验', 409);
  const raw = bytes(row.manifest_text),
    manifest = parse(row.manifest_text),
    plan = parse(row.plan_spec);
  if (
    raw.length > LIMITS.manifestBytes ||
    (await hashBytes(raw)) !== ref.datasetRoot ||
    manifest?.format !== ref.format ||
    manifest?.version !== ref.version ||
    manifest?.profile !== context.profile
  )
    fail('DATASET_SOURCE_INTEGRITY', '数据集清单身份不匹配', 409);
  assertPlanContext(plan, context);
  await assertRegistry(env, owner, plan.sources.registry);
  return {
    dataset: row,
    stage: {
      id: row.stage_id,
      job_id: row.composition_job_id,
      manifest_text: row.manifest_text
    },
    manifestText: row.manifest_text,
    manifest,
    plan
  };
}
function assertProfile(strategy, closure, admissionProfile) {
  const scope = parse(closure.dataset.scope),
    u = strategy.universe;
  assertFinancialResearchConfig(strategy, admissionProfile, closure.manifest.version);
  if (
    u.selection ||
    canonical({ symbols: u.symbols, start: u.start, end: u.end }) !== canonical(scope)
  )
    fail(
      'DATASET_SCOPE_MISMATCH',
      '研究范围必须与已冻结数据集完全一致；需要改变范围时先准备新数据集'
    );
  const availableStates = new Set(
    closure.plan.sources.financial.flatMap((f) => f.selection.selectedStateIds)
  );
  const fields = financialFields(strategy);
  if (!fields.length || fields.some((f) => !stateIds.has(f) || !availableStates.has(f)))
    fail('DATASET_STATE_MISMATCH', '预测因子必须引用数据集中实际登记的财务状态');
  if (
    strategy.factors.some((f) => f.role !== 'predictor') ||
    Object.values(strategy.dataBindings || {}).some((x) => Object.keys(x || {}).length)
  )
    fail('DATASET_RESEARCH_PROFILE', '该财务预测入口仅使用预测状态，不接受外部临时绑定或对冲用途');
}
export async function admitDatasetResearch(env, owner, strategy, input) {
  const ref = validateDatasetRef(input.datasetRef);
  requireResearchEnabled(env, ref.version);
  if (
    input.dataset !== undefined ||
    !registeredFinancialProfile(input.admissionProfile, ref.version)
  )
    fail('DATASET_RESEARCH_PROFILE', '请使用冻结数据集引用和明确研究口径');
  const closure = await readyDataset(env, owner, input.datasetRef);
  assertProfile(strategy, closure, input.admissionProfile);
  const sourceEvidence = {
    datasetRef: datasetRef(closure.dataset, datasetContext(ref.version)),
    admissionProfile: input.admissionProfile
  };
  return {
    ...closure,
    ...sourceEvidence,
    sourceEvidence,
    strategyHash: await hashBytes(bytes(canonical(strategy)))
  };
}
/** Server-only helper injected into financial_bundle begin/first completion. */
export async function assertRunDataset(env, job) {
  const relation = await env.DB.prepare(
    'SELECT * FROM quant_run_datasets WHERE job_id=? AND owner=?'
  )
    .bind(job.id, job.owner)
    .first();
  if (
    !relation ||
    job.data_source !== 'ready_dataset' ||
    !financialProfileVersion(relation.profile)
  )
    fail('DATASET_RUN_BINDING', '实验没有已冻结的数据集关系', 409);
  const version = financialProfileVersion(relation.profile);
  requireResearchEnabled(env, version);
  const ref = {
      datasetId: relation.dataset_id,
      datasetRoot: relation.dataset_root,
      format: protocol.datasetFormat,
      version
    },
    closure = await readyDataset(env, job.owner, ref),
    strategy = parse(job.spec),
    admission = parse(relation.admission);
  assertProfile(strategy, closure, relation.profile);
  if (admission?.strategyHash !== (await hashBytes(bytes(canonical(strategy)))))
    fail('DATASET_RUN_BINDING', '实验配置与冻结准入不一致', 409);
  return {
    ...closure,
    datasetRef: ref,
    admissionProfile: relation.profile,
    sourceEvidence: { datasetRef: ref, admissionProfile: relation.profile }
  };
}

export async function experimentDatasetBinding(env, owner, experimentId, version) {
  const row = await env.DB.prepare(
    'SELECT r.*,d.scope,d.summary FROM quant_experiment_datasets r JOIN quant_research_datasets d ON d.id=r.dataset_id AND d.owner=r.owner WHERE r.experiment_id=? AND r.version=? AND r.owner=?'
  )
    .bind(experimentId, version, owner)
    .first();
  return row
    ? {
        datasetRef: {
          datasetId: row.dataset_id,
          datasetRoot: row.dataset_root,
          format: protocol.datasetFormat,
          version: financialProfileVersion(row.profile)
        },
        admissionProfile: row.profile,
        scope: parse(row.scope),
        selectedStateIds: parse(row.summary, {}).selectedStateIds || [],
        stateDefinitions: definitions.items
          .filter((x) => (parse(row.summary, {}).selectedStateIds || []).includes(x.id))
          .map((x) => ({ id: x.id, name: x.name }))
      }
    : null;
}
export function experimentBindingStatement(env, owner, experimentId, version, admission, time) {
  return env.DB.prepare(
    'INSERT INTO quant_experiment_datasets(experiment_id,version,owner,dataset_id,dataset_root,profile,created_at) SELECT ?,?,?,?,?,?,? WHERE changes()=1 AND EXISTS(SELECT 1 FROM quant_experiment_versions WHERE experiment_id=? AND version=?)'
  ).bind(
    experimentId,
    version,
    owner,
    admission.datasetRef.datasetId,
    admission.datasetRef.datasetRoot,
    admission.admissionProfile,
    time,
    experimentId,
    version
  );
}
