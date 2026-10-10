/** Indexes contain aggregates; complete numerical evidence stays in private R2. */
const object = (value) => value && typeof value === 'object' && !Array.isArray(value);
const pick = (value, names) =>
  object(value)
    ? Object.fromEntries(
        names.filter((key) => value[key] !== undefined).map((key) => [key, value[key]])
      )
    : {};
const count = (value) => (Array.isArray(value) ? value.length : 0);

export function diagnosticSummary(value) {
  if (!object(value)) return {};
  const summary = pick(value, [
    'period',
    'holdoutStart',
    'holdoutEnd',
    'metrics',
    'validForecasts',
    'invalidForecasts',
    'invalidModelFits',
    'selectedModel',
    'selectionUsesHoldout',
    'rollingRefitsUseMaturedPastHoldoutLabels',
    'purgeRule',
    'overlappingLabelsIndependent',
    'significanceTested',
    'familyHypothesis',
    'meanReversionProven',
    'aggregateUncertainty'
    ,'studyProtocol', 'studyMode', 'outputUnit', 'assetModels', 'parameterSharing'
  ]);
  summary.detailCounts = {
    targetDiagnostics: value.detailCounts?.targetDiagnostics ?? count(value.perTarget),
    outerFolds: value.detailCounts?.outerFolds ?? count(value.outerFolds),
    finalTrials: value.detailCounts?.finalTrials ?? count(value.finalTrials)
  };
  if (object(value.modelSearch)) {
    summary.modelSearch = pick(value.modelSearch, [
      'schema', 'parameterSharing', 'selectedCandidateId', 'researchCandidateId',
      'freezeCutoff', 'usesTerminalOutcomes', 'trainingPlotIsOutOfSample',
      'researchCandidateIsDeploymentQualified', 'target', 'assetReturnIdentity',
      'selectionMeaning'
    ]);
    summary.modelSearch.candidateCount = value.modelSearch.candidateCount ?? count(value.modelSearch.candidates);
  }
  if (object(value.inputCoverage))
    summary.inputCoverage = pick(value.inputCoverage, [
      'totalOrigins',
      'validInputOrigins',
      'invalidReasons',
      'features'
    ]);
  if (object(value.factorIncrement)) {
    const f = value.factorIncrement;
    summary.factorIncrement = {
      ...pick(f, [
        'status',
        'reason',
        'featuresRemoved',
        'method',
        'pairedDates',
        'pairedObservations',
        'withFactorsMse',
        'stateOnlyMse',
        'dateBalancedMseImprovement',
        'relativeMseImprovement',
        'hedgeFactorsAblated',
        'sameEventAndMissingInputMask',
        'significanceTested',
        'causalAttribution',
        'profitabilityEstablished',
        'bothModelValidOnly',
        'coverage',
        'outputValidityMasksIdentical'
      ]),
      detailCounts: {
        dailyLosses: f.detailCounts?.dailyLosses ?? count(f.dailyLosses),
        baselineRows: f.detailCounts?.baselineRows ?? f.baselineTotalRows ?? count(f.baselineRows),
        baselineModelFits:
          f.detailCounts?.baselineModelFits ?? f.baselineModelFitCount ?? count(f.baselineModelFits)
      },
      ...(object(f.baselineValidation)
        ? { baselineValidation: diagnosticSummary(f.baselineValidation) }
        : {})
    };
  }
  return { ...summary, detailAvailability: 'complete_artifact_download' };
}

export function universeSummary(value) {
  return pick(value, [
    'symbols',
    'start',
    'end',
    'presetId',
    'snapshotDate',
    'resolutionHash',
    'snapshotHash',
    'subsetPolicy',
    'catalogSnapshot'
  ]);
}

export function forecastMetadataSummary(value) {
  if (!object(value)) return {};
  return {
    ...pick(value, ['target', 'model', 'synthetic', 'independentlyValidatedAlpha']),
    diagnostics: diagnosticSummary(value.diagnostics),
    validation: diagnosticSummary(value.validation),
    universe: universeSummary(value.universe)
  };
}
