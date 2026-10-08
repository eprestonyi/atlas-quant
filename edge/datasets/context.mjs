/** Server-selected protocols. A request field can never supply this context. */
import legacy from '../../contracts/hosted-datasets-v1.json' with { type: 'json' };
import graph from '../../contracts/hosted-dataset-graphs-v1.json' with { type: 'json' };
import { ApiError } from '../errors.mjs';
export const LEGACY_DATASET = Object.freeze({
  protocol: legacy,
  profile: legacy.profile,
  version: 2,
  capability: legacy.protocol,
  runnerBase: '/runner/datasets',
  publicBase: '/datasets',
  jobKind: 'dataset_compose',
  metaKey: 'dataset_runner',
  maintenanceKey: 'dataset_maintenance',
  flag: legacy.featureFlag
});
export const GRAPH_DATASET = Object.freeze({
  protocol: graph,
  profile: graph.profile,
  version: 3,
  capability: graph.protocol,
  runnerBase: '/runner/dataset-graphs',
  publicBase: '/dataset-graphs',
  jobKind: 'dataset_graph_compose',
  metaKey: 'dataset_graph_runner',
  maintenanceKey: 'dataset_graph_maintenance',
  flag: graph.featureFlag
});
export function datasetContext(version) {
  if (version === 2) return LEGACY_DATASET;
  if (version === 3) return GRAPH_DATASET;
  throw new ApiError('DATASET_FORMAT', '数据集版本未登记');
}
export function assertContext(context) {
  if (context !== LEGACY_DATASET && context !== GRAPH_DATASET)
    throw Error('Unregistered server dataset context');
  return context;
}
export function assertPlanContext(spec, context = LEGACY_DATASET) {
  assertContext(context);
  if (spec?.profile !== context.profile || spec.request?.profile !== context.profile)
    throw new ApiError('DATASET_PROFILE', '数据集记录不属于该版本入口', 409);
}
