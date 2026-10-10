"""Asset-specific single-response statistics; never synthetic cross-sectional IC."""
from __future__ import annotations
from itertools import combinations
import numpy as np
from ..factor_diagnostics import _correlation, _distribution, _descriptive_fit, _joint_axis, _joint, MAX_JOINT_PAIRS
from ..schema import fail


def metrics(prediction, truth, *, unit, sample='out_of_sample'):
    prediction, truth = np.asarray(prediction, dtype=float).reshape(-1), np.asarray(truth, dtype=float).reshape(-1)
    if len(prediction) != len(truth):
        raise ValueError('single response metric dimension mismatch')
    finite = np.isfinite(prediction) & np.isfinite(truth)
    pred, actual = prediction[finite], truth[finite]
    result = {'observations': int(len(pred)), 'observedDates': int(len(pred)), 'weighting': 'equal_observed_dates',
              'unit': unit, 'sample': sample, 'significanceTested': False, 'confidenceInterval': None,
              'biasSign': 'predicted_minus_realized'}
    if not len(pred):
        return {**result, **dict.fromkeys(('mse', 'rmse', 'mae', 'bias', 'rSquared', 'zeroReturnMse', 'mseImprovement', 'relativeMseImprovement'))}
    with np.errstate(over='ignore', invalid='ignore'):
        error = pred-actual
        mse, zero = float(np.mean(error**2)), float(np.mean(actual**2))
        variance = float(np.mean((actual-actual.mean())**2))
    if not np.isfinite([mse, zero, variance]).all():
        fail('INVALID_FORECAST', '收益评分超出有限数值范围')
    return {**result, 'mse': mse, 'rmse': float(np.sqrt(mse)), 'mae': float(np.mean(abs(error))),
            'bias': float(error.mean()), 'rSquared': 1-mse/variance if variance > 1e-20 else None,
            'rSquaredBaseline': 'same_sample_response_mean', 'zeroReturnMse': zero,
            'mseImprovement': zero-mse, 'relativeMseImprovement': 1-mse/zero if zero > 1e-20 else None,
            'pearson': _correlation(pred, actual), 'spearman': _correlation(pred, actual, True)}


def factor_diagnostics(samples, holdout, strategy):
    mask = samples.meta.date >= holdout
    X, meta = samples.X.loc[mask], samples.meta.loc[mask]
    mature = meta.inputValid & samples.y.loc[mask].notna().all(axis=1)
    y = samples.y.loc[mask, 'response'].where(mature)
    dev = samples.X.loc[samples.meta.date < holdout]
    identity = {'targetId': str(meta.targetId.iloc[0]), 'targetSymbol': str(meta.assetSymbol.iloc[0])}
    desc = {item['feature']: item for item in samples.automatic_preprocessing['factors']}
    definitions = {'factor:'+f['id']: f for f in strategy['factors']}
    features = []
    for name in X:
        temporal = {'n': int((X[name].notna() & y.notna()).sum()), 'pearson': _correlation(X[name], y),
                    'spearman': _correlation(X[name], y, True), 'pValue': None, **identity}
        univariate = _descriptive_fit(X[name], y)
        univariate.update(unit='response_per_economic_factor_unit', interpretation='single_asset_descriptive_temporal_OLS_not_causal_or_incremental')
        unavailable_ic = {'status': 'unavailable', 'mean': None, 'dates': 0,
                          'unavailableReason': 'independent_single_asset_model_not_cross_sectional_IC'}
        features.append({**identity, 'name': name, 'kind': 'factor', 'definition': definitions[name],
            'inputConstruction': desc[name], 'distribution': _distribution(X[name]),
            'missing': {'count': int(X[name].isna().sum()), 'total': len(X), 'fraction': float(X[name].isna().mean())},
            'ic': unavailable_ic, 'rankIc': dict(unavailable_ic), 'temporalAssociation': temporal,
            'timeSeriesCorrelation': {'status': 'available' if temporal['pearson'] is not None else 'unavailable',
                'perTarget': [temporal], 'totalTargets': 1, 'omittedTargets': 0,
                'interpretation': 'within_target_temporal_association_not_cross_sectional_IC'},
            'descriptiveFit': univariate})
    columns = list(X)
    covariance = X.cov(min_periods=2).to_numpy()
    pairs = list(combinations(columns, 2))
    axes = {c: _joint_axis(X[c], dev[c]) for c in columns}
    joints = [{**identity, **_joint(axes[a], axes[b], (a, b), meta.date.to_numpy())}
              for a, b in pairs[:MAX_JOINT_PAIRS]]
    dependence = {'featureNames': columns,
        'correlation': [[_correlation(X[a], X[b]) for b in columns] for a in columns],
        'covariance': [[float(x) if np.isfinite(x) else None for x in row] for row in covariance],
        'pairCounts': [[int((X[a].notna() & X[b].notna()).sum()) for b in columns] for a in columns],
        'covarianceDof': 1, 'missingness': 'pairwise_complete', 'positiveSemidefiniteGuaranteed': False,
        'jointDistributions': joints, 'jointPairBudget': MAX_JOINT_PAIRS,
        'totalPossiblePairs': len(pairs), 'omittedPairs': max(0, len(pairs)-MAX_JOINT_PAIRS)}
    mode = strategy['research']['returnStudy']['mode']
    return {**identity, 'features': features, 'dependence': dependence, 'jointDistributions': joints,
        'firstDate': str(meta.date.min()), 'lastDate': str(meta.date.max()), 'origins': len(meta),
        'maturedValidOrigins': int(mature.sum()), 'period': 'terminal_sequential_out_of_sample',
        'target': 'future_asset_response' if mode == 'forecast' else 'matched_period_asset_response',
        'targetDefinition': '(close(t+h)/close(t)-1)/scale(t)' if mode == 'forecast' else '(close(t)/close(t-h)-1)/scale(t-h)',
        'selectionUse': False, 'lookedAtAfterSelection': True,
        'significance': {'status': 'not_available', 'pValues': None, 'reason': 'overlap_and_temporal_dependence_not_adjusted'}}
