// Construct only private, exact-version links from saved source references.
export function marketDatasetDownload(ref) {
  return ref?.format === 'atlas.quant.market_dataset' && ref.version === 1 &&
    /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(ref.datasetId || '') &&
    /^[a-f0-9]{64}$/.test(ref.datasetRoot || '')
    ? `/quant/api/market-datasets/${ref.datasetId}/download?datasetRoot=${ref.datasetRoot}`
    : null;
}
