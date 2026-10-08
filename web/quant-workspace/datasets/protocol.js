// Explicit source/compute/transport tuples. A newer number never implies compatibility.
export const FINANCIAL_AUTO = 'financial_fundamental_auto_50_v1';
export const GRAPH_AUTO = 'financial_fundamental_graph_auto_50_v1';
const profiles = {
  1: { ridge: 'financial_compose_50_v1' },
  2: { ridge: 'financial_snapshot_view_50_v1', auto: FINANCIAL_AUTO },
  3: { auto: GRAPH_AUTO },
};
export function validDatasetRef(ref) {
  return ref?.format === 'atlas.quant.research_dataset' && Object.hasOwn(profiles, ref.version) && Number.isInteger(ref.version)
    && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(ref.datasetId || '') && /^[a-f0-9]{64}$/.test(ref.datasetRoot || '');
}
export const financialProfile = (ref, estimator) => validDatasetRef(ref) ? profiles[ref.version][estimator] || null : null;
export const isFinancialAuto = profile => [FINANCIAL_AUTO, GRAPH_AUTO].includes(profile);
export function datasetLocation(ref) {
  if (!validDatasetRef(ref)) return null;
  const graph = ref.version === 3, root = encodeURIComponent(ref.datasetRoot), id = encodeURIComponent(ref.datasetId);
  return {
    detail: `${graph ? '/dataset-graphs' : '/datasets'}/${id}?datasetRoot=${root}`,
    archive: `/quant/api/${graph ? 'dataset-graphs' : 'datasets'}/${id}/${graph ? 'download' : 'archive'}?datasetRoot=${root}`,
    manifest: `/quant/api/${graph ? 'dataset-graphs' : 'datasets'}/${id}/manifest?datasetRoot=${root}`,
    page: `#quant/studio/datasets/${graph ? 'graph/' : ''}dataset/${id}?root=${root}`,
  };
}
export function financialTransportSource(transport) {
  const source = transport?.sourceEvidence, ref = source?.datasetRef;
  if (transport?.format !== 'atlas.quant.financial_bundle' || !validDatasetRef(ref)) return null;
  if (transport.version !== (ref.version === 3 ? 2 : 1)) return null;
  if (!Object.values(profiles[ref.version]).includes(source.admissionProfile)) return null;
  if (ref.version === 3 && transport.sourceEvidenceClosure !== 'separate_research_dataset_v3') return null;
  return ref;
}
