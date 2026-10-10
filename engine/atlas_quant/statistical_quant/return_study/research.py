"""Chronological asset-specific return research with complete immutable evidence."""
from __future__ import annotations
import copy
import time
from dataclasses import replace
import numpy as np
from .. import models
from ..schema import VERSION, digest, fail, prediction_config
from ..validation import mature_mask, folds, select, _train, forecast_origins
from .contract import SCHEMA, outputs
from .samples import build_samples, panel_document
from .diagnostics import metrics, factor_diagnostics

MAX_FIT_ATTEMPTS = 20000
MAX_RESEARCH_SECONDS = 1800


class Runtime:
    """Predeclared finite fit budget; the consumer separately enforces hard limits."""
    def __init__(self, maximum):
        if maximum > MAX_FIT_ATTEMPTS:
            fail('CAPACITY_FITS', '逐证券完整搜索超过拟合预算；请减少研究成员或候选集合')
        self.maximum, self.attempts, self.started = maximum, 0, time.monotonic()
    def before_fit(self, *args):
        if self.attempts >= self.maximum:
            fail('CAPACITY_FITS', '收益研究达到预先声明的拟合次数上限')
        self.attempts += 1
        self.after_fit()
    def after_fit(self):
        if time.monotonic()-self.started > MAX_RESEARCH_SECONDS:
            fail('CAPACITY_RESEARCH_TIMEOUT', '收益研究超过预先声明的计算时限')


def _candidate_functions(samples, strategy, trials, winner, development, cutoff, runtime):
    from ..model_function import export_function
    result = []
    mask = mature_mask(samples, cutoff, development, strategy['model']['trainWindow'])
    for trial in trials:
        item = {key: trial[key] for key in ('id', 'estimator', 'params', 'status', 'invalidReason')}
        item.update(validationScore=trial['score'], selected=trial['id'] == winner['id'],
            baseline=trial['estimator'] in {'no_change', 'historical_drift'},
            withinHeuristicTolerance=trial['withinHeuristicTolerance'], functionArtifact=None, trainingMetrics=None)
        if trial['status'] == 'valid':
            spec = {key: trial[key] for key in ('id', 'estimator', 'params')}
            phase = 'fit'
            try:
                fitted, audit = _train(samples, spec, development, cutoff, strategy, runtime)
                fit = {'id': 'candidate_fit_'+digest({'spec': spec, 'audit': audit})[:24],
                       'fitDate': cutoff, 'status': 'valid', 'sequentialMaturedLabelsOnly': True, **audit}
                prediction = fitted.predict(samples.X.loc[mask])
                truth = samples.y.loc[mask].to_numpy()
                phase = 'export'
                item.update(functionArtifact=export_function(fitted, fit, strategy), fit=fit,
                    trainingMetrics=metrics(prediction, truth, unit=outputs(strategy)[0], sample='training_in_sample'))
                chosen = np.unique(np.linspace(0, len(truth)-1, min(96, len(truth)), dtype=int))
                selected = samples.meta.loc[mask].iloc[chosen]
                item['trainingPlot'] = {'sample': 'training_in_sample', 'selection': 'uniform_row_index',
                    'totalRows': len(truth), 'points': [{'date': row.date, 'targetId': row.targetId,
                        'actualResponse': float(truth[i, 0]), 'fittedResponse': float(prediction[i, 0])}
                        for i, row in zip(chosen, selected.itertuples())]}
            except (ValueError, FloatingPointError) as exc:
                if phase != 'fit' or str(getattr(exc, 'code', '')).startswith('CAPACITY_'):
                    exc.candidate_functions = result
                    raise
                item.update(status='refit_invalid', invalidReason=getattr(exc, 'code', 'MODEL_FIT_FAILED'))
        result.append(item)
    research = [item for item in result if not item['baseline'] and item['functionArtifact'] is not None]
    best = min(research, key=lambda item: (item['validationScore'], item['id'])) if research else None
    return result, best['id'] if best else None


def _record(row, value, fit_id):
    finite = bool(np.isfinite(value))
    reason = row.invalidReason
    if reason is None and row.responseEndDate is None:
        reason = 'target_outside_available_calendar'
    if reason is None and not np.isfinite(row.observedResponse):
        reason = 'missing_response_price'
    if reason is None and not finite:
        reason = 'model_unavailable'
    prediction = float(value) if finite else None
    predicted_return = float(value*row.responseScale) if finite and np.isfinite(row.responseScale) else None
    conditional = float(row.originPrice*(1+predicted_return)) if predicted_return is not None and np.isfinite(row.originPrice) else None
    if predicted_return is not None and not np.isfinite([predicted_return, conditional]).all():
        fail('INVALID_FORECAST', '收益还原超出有限数值范围')
    return {'schema': 'asset-return-observation/1',
        'forecastId': 'forecast_'+digest({'date': row.date, 'targetId': row.targetId, 'fit': fit_id, 'response': prediction})[:32],
        'date': row.date, 'assetSymbol': row.assetSymbol, 'targetId': row.targetId, 'modelFitId': fit_id,
        'featureDate': row.featureDate, 'responseStartDate': row.responseStartDate, 'responseEndDate': row.responseEndDate,
        'informationCutoff': row.date+'_AFTER_CLOSE', 'status': 'valid' if reason is None else 'invalid',
        'invalidReason': reason, 'inputValid': bool(row.inputValid), 'originPrice': row.originPrice,
        'responseScale': row.responseScale, 'observedResponse': row.observedResponse, 'observedReturn': row.observedReturn,
        'predictedResponse': prediction, 'predictedReturn': predicted_return, 'conditionalPrice': conditional,
        'responseResidual': float(row.observedResponse-value) if finite and np.isfinite(row.observedResponse) else None,
        'labelMaturedAt': row.responseEndDate if np.isfinite(row.observedResponse) else None}


def _asset(samples, strategy, holdout, runtime, partial):
    from ..model_function import export_function
    from ...engine import ResearchError
    definition = next(iter(samples.definitions.values()))
    target_id, symbol = definition['id'], definition['symbols'][0]
    identity = {'targetId': target_id, 'targetSymbol': symbol}
    specs = models.candidates(strategy['model']['estimator'], strategy['model'].get('search'))
    development = sorted(samples.meta.loc[mature_mask(samples, holdout), 'date'].unique())
    h = strategy['target']['horizonSessions']
    outer = partial['outerFolds']
    for training, testing in folds(development, strategy['validation']['outerFolds'],
                                   strategy['validation']['minTrainDates']+2*(h+10), h):
        inner = sorted(samples.meta.loc[mature_mask(samples, testing[0], training), 'date'].unique())
        winner, trials = select(samples, specs, inner, strategy, runtime)
        fitted, audit = _train(samples, winner, inner, testing[0], strategy, runtime)
        eligible = samples.meta.date.isin(testing) & samples.meta.inputValid & samples.y.notna().all(axis=1)
        score = metrics(fitted.predict(samples.X.loc[eligible]), samples.y.loc[eligible], unit=outputs(strategy)[0])
        outer.append({**identity, 'testStart': testing[0], 'testEnd': testing[-1], 'selection': winner,
                      'trials': trials, 'fit': audit, 'metrics': score})
    # Only this final development selection may invoke the bounded reviewer.
    winner, trials = select(samples, specs, development, strategy, runtime, review=True)
    partial['finalTrials'] = trials
    candidates, research_id = _candidate_functions(samples, strategy, trials, winner, development, holdout, runtime)
    partial['candidates'] = candidates
    rows, fits = partial['rows'], partial['fits']
    model, fit_id, last_fit = None, None, -100000
    for row in samples.meta.loc[samples.meta.date >= holdout].itertuples():
        if model is None or row.dateIndex-last_fit >= strategy['model']['refitDays']:
            try:
                model, audit = _train(samples, winner, None, row.date, strategy, runtime)
                audit['status'] = 'valid'
            except ResearchError as exc:
                if exc.code not in {'MISSING_MODEL_DATA', 'INSUFFICIENT_FORECAST_DATA', 'MODEL_DID_NOT_CONVERGE', 'INVALID_FORECAST'}:
                    raise
                model = None
                audit = {**identity, 'status': 'invalid', 'invalidReason': exc.code, 'informationCutoff': row.date,
                    'labelEndMax': None, 'trainStart': None, 'trainEnd': None, 'estimator': winner['estimator'],
                    'params': winner['params'], 'featureNames': [], 'outputs': outputs(strategy)}
            fit_id = 'fit_'+digest({'date': row.date, 'winner': winner, 'audit': audit})[:24]
            fit = {'id': fit_id, 'fitDate': row.date, 'sequentialMaturedLabelsOnly': True, **audit}
            if model is not None:
                fit['functionArtifact'] = export_function(model, audit, strategy)
            fits.append(fit); last_fit = row.dateIndex
        value = model.predict(samples.X.loc[[row.Index]])[0, 0] if model is not None and row.inputValid else np.nan
        rows.append(_record(row, value, fit_id))
    scored = [r for r in rows if r['status'] == 'valid' and r['labelMaturedAt'] is not None]
    score = metrics([r['predictedResponse'] for r in scored], [r['observedResponse'] for r in scored], unit=outputs(strategy)[0])
    factor_stats = factor_diagnostics(samples, holdout, strategy)
    prefix = target_id+'::'
    for item in candidates:
        item.update(id=prefix+item['id'], **identity, symbols=[symbol])
    trials = [{**trial, 'id': prefix+trial['id'], **identity} for trial in trials]
    return {'rows': rows, 'fits': fits, 'candidates': candidates, 'outerFolds': outer, 'finalTrials': trials,
        'assetModel': {**identity, 'selectedCandidateId': prefix+winner['id'],
            'researchCandidateId': prefix+research_id if research_id else None,
            'modelFitId': fits[-1]['id'] if fits else None},
        'diagnostics': {**identity, 'metrics': score, 'selectedModel': winner, 'factorDiagnostics': factor_stats,
            'featureNames': list(samples.X), 'validForecasts': len(scored),
            'invalidForecasts': sum(r['status'] != 'valid' for r in rows),
            'zeroReturnBaseline': metrics(np.zeros(len(scored)), [r['observedResponse'] for r in scored], unit=outputs(strategy)[0])}}


def run(strategy, panel, dates, audit, provenance, *, plan_sink=None):
    from ...engine import _finite_json
    from ...context_sources import summarize_context_provenance
    from ..ai_review import attach_reviewer
    samples = build_samples(panel, dates, strategy)
    observation = samples.meta.isObservation
    sampled = replace(samples, X=samples.X.loc[observation], y=samples.y.loc[observation], meta=samples.meta.loc[observation])
    holdout, indices = forecast_origins(sampled, strategy)
    specs = models.candidates(strategy['model']['estimator'], strategy['model'].get('search'))
    groups = len(samples.definitions)
    terminal_dates = sampled.meta.loc[indices, 'date'].nunique()
    outer, inner = strategy['validation']['outerFolds'], strategy['validation']['innerFolds']
    maximum = groups*((outer+1)*inner*len(specs)+outer+len(specs)+terminal_dates)
    base_runtime = Runtime(maximum)
    runtime = attach_reviewer(strategy, base_runtime)
    budget = {'modelGroups': groups, 'candidateCountPerAsset': len(specs), 'maximumFitAttempts': maximum,
              'declaredBeforeFitting': True, 'maximumResearchSeconds': MAX_RESEARCH_SECONDS,
              'maximumFitAttemptsAdmission': MAX_FIT_ATTEMPTS, 'includesFactorFreeBaseline': False}
    if plan_sink is not None:
        plan_sink({'schemaVersion': 1, 'studyProtocol': SCHEMA, 'source': 'samples_before_model_fitting',
            'baselineRequired': False, 'holdoutStart': holdout,
            'origins': [{'date': row.date, 'targetId': row.targetId, 'responseStartDate': row.responseStartDate,
                         'responseEndDate': row.responseEndDate, 'inputValid': bool(row.inputValid)}
                        for row in sampled.meta.loc[indices].itertuples()]})
    parts = []
    for target_id, definition in sorted(samples.definitions.items()):
        idx = sampled.meta.index[sampled.meta.targetId == target_id]
        part_samples = replace(sampled, X=sampled.X.loc[idx], y=sampled.y.loc[idx], meta=sampled.meta.loc[idx],
                               definitions={target_id: definition})
        config = copy.deepcopy(strategy)
        config['universe']['symbols'] = definition['symbols']
        # This is an internal scope, not a silently resampled user collection.
        config['universe'].pop('selection', None)
        partial = {'rows': [], 'fits': [], 'outerFolds': [], 'finalTrials': [], 'candidates': []}
        try:
            parts.append(_asset(part_samples, config, holdout, runtime, partial))
        except Exception as exc:
            exc.forecast_partial = {'status': 'interrupted', 'complete': False, 'publishable': False,
                'failedTargetId': target_id, 'completedAssets': parts, 'currentAsset': partial, 'declaredBudget': budget}
            raise
    rows = sorted((r for part in parts for r in part['rows']), key=lambda r: (r['date'], r['assetSymbol']))
    fits = sorted((f for part in parts for f in part['fits']), key=lambda f: (f['fitDate'], f['targetId']))
    candidates = [c for part in parts for c in part['candidates']]
    asset_models = [part['assetModel'] for part in parts]
    factor_stats = [part['diagnostics']['factorDiagnostics'] for part in parts]
    search = {'schema': 'factor-model-search-report/1', 'parameterSharing': 'per_target',
        'selectedCandidateId': 'per_target', 'researchCandidateId': next((a['researchCandidateId'] for a in asset_models if a['researchCandidateId']), None),
        'selectedCandidateIds': [a['selectedCandidateId'] for a in asset_models],
        'researchCandidateIds': [a['researchCandidateId'] for a in asset_models if a['researchCandidateId']],
        'candidates': candidates, 'freezeCutoff': holdout, 'usesTerminalOutcomes': False,
        'trainingPlotIsOutOfSample': False, 'researchCandidateIsDeploymentQualified': False,
        'target': outputs(strategy)[0], 'assetReturnIdentity': 'conditionalPrice = originPrice * (1 + responseScale * F(R))',
        'selectionMeaning': 'researchCandidate_is_best_nonbaseline_inner_score; selectedCandidate_uses_complexity_rule'}
    diagnostics = {'period': 'terminal_sequential_out_of_sample', 'holdoutStart': holdout, 'holdoutEnd': dates[-1],
        'parameterSharing': 'per_target', 'modelGroups': groups, 'assetModels': asset_models,
        'perTarget': [part['diagnostics'] for part in parts], 'modelSearch': search,
        'metrics': {'observations': sum(p['diagnostics']['metrics']['observations'] for p in parts),
                    'aggregation': 'not_aggregated_independent_asset_equations', 'mse': None, 'mseImprovement': None},
        'selectedModel': {'id': 'per_target', 'estimator': 'per_target', 'params': {}},
        'validForecasts': sum(row['status'] == 'valid' for row in rows),
        'invalidForecasts': sum(row['status'] != 'valid' for row in rows),
        'outerFolds': [fold for part in parts for fold in part['outerFolds']],
        'finalTrials': [trial for part in parts for trial in part['finalTrials']],
        'selectionUsesHoldout': False, 'rollingRefitsUseMaturedPastHoldoutLabels': True,
        'overlappingLabelsIndependent': False, 'significanceTested': False,
        'inputCoverage': {'totalOrigins': len(samples.meta), 'validInputOrigins': int(samples.meta.inputValid.sum()),
                          'invalidReasons': samples.meta.invalidReason.dropna().value_counts().to_dict()},
        'selectionAudit': {'schemaVersion': 1, 'candidateSet': specs, 'candidateSetHash': digest(specs),
            'candidateCount': len(specs), 'parameterSharing': 'per_target', 'modelGroups': groups,
            'terminalSelectionCutoff': holdout, 'rule': 'one_standard_error_complexity_heuristic',
            'score': 'equal_date_single_asset_response_mse', 'outerEstimateUsedForSelection': False,
            'terminalMetricsUsedForSelection': False, 'candidateSetExpandedUsingOutcomes': False,
            'eliminatesBiasOrOverfitting': False, 'reinforcementLearningIncluded': False,
            'researchFitBudget': {**budget, 'actualFitAttempts': base_runtime.attempts}}}
    factor_research = {'schemaVersion': 1, 'studyProtocol': SCHEMA,
        'modelFunctions': [{'modelFitId': f['id'], 'targetId': f['targetId'], 'artifactId': f['functionArtifact']['artifactId'],
            'path': f'forecasts.modelFits[{i}].functionArtifact'} for i, f in enumerate(fits) if 'functionArtifact' in f],
        'candidateModelFunctions': [{'candidateId': c['id'], 'modelFitId': c['fit']['id'], 'targetId': c['targetId'],
            'artifactId': c['functionArtifact']['artifactId'], 'path': f'forecasts.diagnostics.modelSearch.candidates[{i}].functionArtifact'}
            for i, c in enumerate(candidates) if c.get('functionArtifact') is not None],
        'diagnostics': {'period': 'terminal_sequential_out_of_sample', 'selectionUse': False,
            'features': [f for d in factor_stats for f in d['features']],
            'dependence': {'jointDistributions': [j for d in factor_stats for j in d['jointDistributions']]}},
        'panel': panel_document(samples, strategy['universe']['symbols']),
        'editSemantics': 'derived_function_requires_new_validation_original_report_is_immutable'}
    artifact = _finite_json({'schemaVersion': 1, 'studyProtocol': SCHEMA,
        'predictionConfigHash': digest(prediction_config(strategy)), 'dataFingerprint': audit['dataSha256'],
        'sourceStrategy': copy.deepcopy(strategy), 'rows': rows, 'totalRows': len(rows), 'truncated': False,
        'targetDefinitions': list(samples.definitions.values()), 'modelFits': fits, 'hedgeFits': [],
        'diagnostics': diagnostics, 'factorResearch': factor_research})
    artifact['artifactId'] = digest(artifact)
    mode = strategy['research']['returnStudy']['mode']
    return _finite_json({'schemaVersion': 2, 'status': 'completed', 'engineVersion': VERSION, 'strategy': strategy,
        'research': {**strategy['research'], 'studyProtocol': SCHEMA, 'executionOnly': False, 'predictionRefitPerformed': True},
        'provenance': {**summarize_context_provenance(provenance), **audit}, 'forecasts': artifact,
        'validation': {'completeArtifactPath': 'forecasts.diagnostics', 'holdoutStart': holdout,
                       'selectionUsesHoldout': False, 'parameterSharing': 'per_target'},
        'selection': {'winner': 'per_target', 'winnerTrialId': 'per_target', 'params': {},
            'metric': 'equal_date_single_asset_response_mse', 'holdoutUsedForSelection': False,
            'qualified': False, 'deploymentQualified': False,
            'evidenceStatus': 'DESCRIPTIVE_ASSOCIATION_NOT_FORECAST' if mode == 'association' else 'RETURN_FORECAST_NOT_SIGNIFICANCE_TESTED'},
        'metrics': None, 'equity': [], 'trades': [], 'ledger': [], 'decisions': [],
        'execution': {'enabled': False, 'forecastArtifactId': artifact['artifactId'], 'ledger': [], 'decisions': []},
        'factors': strategy['factors'],
        'warnings': (['SYNTHETIC_DATA_NOT_MARKET_EVIDENCE'] if provenance.get('synthetic') else []) +
                    ['OVERLAPPING_LABELS_ARE_NOT_INDEPENDENT', 'CURRENT_UNIVERSE_NOT_HISTORICAL_CONSTITUENTS',
                     'ASSOCIATION_SCENARIO_IS_NOT_ORIGIN_KNOWN_FORECAST' if mode == 'association' else 'PREDICTIVE_IMPROVEMENT_IS_NOT_PROFITABILITY']})
