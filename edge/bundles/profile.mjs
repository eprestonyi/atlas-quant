/** Phase A has bounded transport, not a larger numerical research universe. */
export const BUNDLE_PROFILE = Object.freeze({
  manifestBytes: 512 * 1024,
  targetChunkBytes: 4 * 1024 * 1024,
  chunkBytes: 8 * 1024 * 1024,
  chunkRows: 10000,
  chunks: 256,
  bytes: 256 * 1024 * 1024,
  rows: 1000000,
  pageRows: 100,
  pageChunks: 8,
  pageBytes: 8 * 1024 * 1024,
  indexBatchBytes: 1024 * 1024,
  summaryBytes: 256 * 1024,
  jsonDepth: 64
});

export const COLLECTION_PATHS = Object.freeze({
  forecasts: ['forecast', '/rows'],
  targets: ['forecast', '/targetDefinitions'],
  modelFits: ['forecast', '/modelFits'],
  factorFeatures: ['forecast', '/factorResearch/diagnostics/features'],
  factorJointDistributions: ['forecast', '/factorResearch/diagnostics/dependence/jointDistributions'],
  hedgeFits: ['forecast', '/hedgeFits'],
  perTarget: ['forecast', '/diagnostics/perTarget'],
  outerFolds: ['forecast', '/diagnostics/outerFolds'],
  finalTrials: ['forecast', '/diagnostics/finalTrials'],
  baselineRows: ['forecast', '/diagnostics/factorIncrement/baselineRows'],
  baselineModelFits: ['forecast', '/diagnostics/factorIncrement/baselineModelFits'],
  dailyLosses: ['forecast', '/diagnostics/factorIncrement/dailyLosses'],
  baselinePerTarget: ['forecast', '/diagnostics/factorIncrement/baselineValidation/perTarget'],
  baselineOuterFolds: ['forecast', '/diagnostics/factorIncrement/baselineValidation/outerFolds'],
  baselineFinalTrials: ['forecast', '/diagnostics/factorIncrement/baselineValidation/finalTrials'],
  equity: ['report', '/equity'],
  trades: ['report', '/trades'],
  riskLedger: ['report', '/execution/ledger'],
  decisions: ['report', '/execution/decisions'],
  snapshotRows: ['snapshot', '/rows'],
  plannedOrigins: ['coverage', '/origins']
});
export const OPTIONAL_CONTEXT_PATHS = Object.freeze({
  snapshotContextSources: ['snapshot', '/provenance/contextSources'],
  modelSearchCandidates: ['forecast', '/diagnostics/modelSearch/candidates'],
  researchPanel: ['forecast', '/factorResearch/panel/rows']
});
export const HASH = /^[a-f0-9]{64}$/;
export const DATE = /^\d{8}$/;
export const SENSITIVE_KEY = /^(?:token|serviceToken|api_key|password|authorization)$/i;
