"""Bounded descriptive factor evidence, evaluated only after model selection.

The diagnostic target is realized exit-state change / origin known gross. Daily
cross-sectional IC is never substituted by a pooled/time-series correlation.
No IID p-value is reported for overlapping and dependent financial observations.
"""
from __future__ import annotations
from itertools import combinations
import numpy as np
import pandas as pd

MAX_JOINT_PAIRS = 6
JOINT_BINS = 5
MAX_TIME_SERIES_TARGETS = 32


def _correlation(x, y, rank=False):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3:
        return None
    if rank:
        x, y = pd.Series(x).rank(method="average").to_numpy(), pd.Series(y).rank(method="average").to_numpy()
    # Scale first: no absolute threshold tied to price/factor units, and no
    # overflow from squaring large finite observations.
    x = x/max(float(np.max(np.abs(x))), np.finfo(float).tiny)
    y = y/max(float(np.max(np.abs(y))), np.finfo(float).tiny)
    x, y = x-x.mean(), y-y.mean()
    nx, ny = float(np.linalg.norm(x)), float(np.linalg.norm(y))
    if nx == 0 or ny == 0:
        return None
    return float(np.clip(np.dot(x/nx, y/ny), -1., 1.))


def _finite(value):
    return float(value) if np.isfinite(value) else None


def _distribution(values):
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if not len(x):
        return {"count": 0, **{key: None for key in ("mean", "variance", "std", "min", "q01", "q25", "median", "q75", "q99", "max")}}
    scale = max(float(np.max(np.abs(x))), np.finfo(float).tiny)
    scaled = x/scale
    with np.errstate(over="ignore", invalid="ignore"):
        qs = np.quantile(scaled, [0, .01, .25, .5, .75, .99, 1])*scale
        mean = float(scaled.mean()*scale)
        std = float(np.std(scaled, ddof=1)*scale) if len(x)>1 else np.nan
        variance = std*std
    return {"count": len(x), "mean": _finite(mean), "variance": _finite(variance),
            "std": _finite(std), "numericRangeUnavailable": bool(len(x)>1 and not np.isfinite(variance)),
            **dict(zip(("min", "q01", "q25", "median", "q75", "q99", "max"), (float(v) for v in qs)))}


def _descriptive_fit(x, y):
    xx, yy = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    valid = np.isfinite(xx) & np.isfinite(yy)
    xx, yy = xx[valid], yy[valid]
    corr = _correlation(xx, yy)
    if corr is None:
        return {"status": "unavailable", "n": len(xx), "reason": "insufficient_or_constant_pairs", "slope": None, "intercept": None, "rSquared": None}
    sx, sy = max(float(np.max(np.abs(xx))), np.finfo(float).tiny), max(float(np.max(np.abs(yy))), np.finfo(float).tiny)
    scaled_x, scaled_y = xx/sx, yy/sy
    centered = scaled_x-scaled_x.mean()
    with np.errstate(over="ignore", invalid="ignore"):
        scaled_slope = float(np.sum(centered*(scaled_y-scaled_y.mean()))/np.sum(centered**2))
        slope = scaled_slope*(sy/sx)
        intercept = float((scaled_y.mean()-scaled_slope*scaled_x.mean())*sy)
    finite = np.isfinite([slope, intercept]).all()
    return {"status": "available" if finite else "unavailable", "n": len(xx), "slope": _finite(slope), "intercept": _finite(intercept),
            "rSquared": corr**2, "fitSample": "reported_terminal_pairs", "outOfSampleFit": False,
            "unavailableReason": None if finite else "coefficient_outside_finite_range",
            "unit": "normalized_exit_change_per_feature_unit", "pValue": None, "coefficientStdError": None,
            "interpretation": "descriptive_pooled_univariate_OLS_not_incremental_or_causal_contribution"}


def _summary(values, all_dates):
    x = [value for value in values if value is not None]
    return {"status": "available" if x else "unavailable", "dates": len(x), "unavailableDates": all_dates-len(x),
            "mean": float(np.mean(x)) if x else None, "std": float(np.std(x, ddof=1)) if len(x)>1 else None,
            "weighting": "equal_valid_dates", "minimumTargetsPerDate": 3,
            "unavailableReason": None if x else "fewer_than_three_finite_targets_or_constant_cross_section", "pValue": None}


def _joint(x, y, x_train, y_train, names, dates):
    valid = np.isfinite(x) & np.isfinite(y)
    # Development-only quantiles freeze the display boundaries. +/-infinity are
    # represented as null exterior bounds; finite terminal outliers are retained.
    axes = []
    for source in (x_train, y_train):
        source = np.asarray(source, dtype=float)
        source = source[np.isfinite(source)]
        if not len(source):
            return {"x": names[0], "y": names[1], "status": "unavailable", "reason": "no_development_values_for_bins"}
        scale = max(float(np.max(np.abs(source))), np.finfo(float).tiny)
        interior = np.unique(np.quantile(source/scale, np.arange(1, JOINT_BINS)/JOINT_BINS)*scale)
        axes.append(np.r_[-np.inf, interior, np.inf])
    counts, _, _ = np.histogram2d(np.asarray(x)[valid], np.asarray(y)[valid], bins=axes)
    n = int(valid.sum())
    observed = np.asarray(dates)[valid]
    return {"x": names[0], "y": names[1], "status": "available" if n else "unavailable",
            "xEdges": [float(v) if np.isfinite(v) else None for v in axes[0]],
            "yEdges": [float(v) if np.isfinite(v) else None for v in axes[1]],
            "counts": counts.astype(int).tolist(), "probabilities": (counts/n).tolist() if n else None,
            "sampleCount": n, "missingPairCount": int(len(x)-n),
            "firstDate": str(observed.min()) if n else None, "lastDate": str(observed.max()) if n else None,
            "edgeSource": "pre_terminal_development_feature_quantiles", "requestedBins": JOINT_BINS,
            "intervalConvention": "[left,right); exterior null means -infinity/+infinity; final right closed",
            "interpretation": "empirical_pairwise_complete_frequency_not_assumed_joint_density"}


def factor_diagnostics(samples, holdout_start, factor_definitions=None):
    terminal = samples.meta.date >= holdout_start
    X = samples.X.loc[terminal].replace([np.inf, -np.inf], np.nan)
    meta = samples.meta.loc[terminal]
    # All terminal origins remain in missingness/distribution reporting; only
    # valid inputs and mature labels enter feature/target associations.
    mature = meta.inputValid & samples.y.loc[terminal].notna().all(axis=1) & meta.targetDate.notna()
    target = samples.y.loc[terminal, "exit"].where(mature)
    development = samples.X.loc[samples.meta.date < holdout_start]
    definitions = {"factor:"+f["id"]: f for f in (factor_definitions or [])}
    features = []
    dates = sorted(meta.date.unique())
    for name in X:
        daily_ic, daily_rank = [], []
        for date in dates:
            selected = meta.date == date
            daily_ic.append(_correlation(X.loc[selected, name], target.loc[selected]))
            daily_rank.append(_correlation(X.loc[selected, name], target.loc[selected], True))
        groups = []
        for group_i, (target_id, group) in enumerate(meta.groupby("targetId", sort=True)):
            if group_i >= MAX_TIME_SERIES_TARGETS:
                break
            xx, yy = X.loc[group.index, name], target.loc[group.index]
            ok = xx.notna() & yy.notna()
            groups.append({"targetId": str(target_id), "n": int(ok.sum()), "pearson": _correlation(xx, yy),
                           "spearman": _correlation(xx, yy, True), "pValue": None})
        definition = definitions.get(name)
        features.append({"name": name, "kind": "factor" if definition else "derived_state",
                         "definition": definition, "missing": {"count": int(X[name].isna().sum()), "total": len(X),
                             "fraction": float(X[name].isna().mean()) if len(X) else None},
                         "distribution": _distribution(X[name]), "ic": _summary(daily_ic, len(dates)),
                         "rankIc": _summary(daily_rank, len(dates)),
                         "timeSeriesCorrelation": {"status": "available" if any(g["pearson"] is not None for g in groups) else "unavailable",
                             "perTarget": groups, "totalTargets": int(meta.targetId.nunique()),
                             "omittedTargets": max(0, int(meta.targetId.nunique())-MAX_TIME_SERIES_TARGETS),
                             "targetSelection": "target_id_order_not_outcome_ranking", "interpretation": "within_target_temporal_association_not_cross_sectional_IC"},
                         "descriptiveFit": _descriptive_fit(X[name], target)})
    columns = list(X)
    pair_counts = [[int((X[a].notna() & X[b].notna()).sum()) for b in columns] for a in columns]
    correlation = [[_correlation(X[a], X[b]) for b in columns] for a in columns]
    covariance = X.cov(min_periods=2).to_numpy()
    cov = [[float(value) if np.isfinite(value) else None for value in row] for row in covariance]
    # Priority is fixed by feature declarations, never by strongest observed IC.
    priority = [c for c in columns if c.startswith("factor:")] + [c for c in columns if not c.startswith("factor:")]
    pairs = list(combinations(priority, 2))
    joints = [_joint(X[a].to_numpy(), X[b].to_numpy(), development[a], development[b], (a, b), meta.date.to_numpy())
              for a, b in pairs[:MAX_JOINT_PAIRS]]
    return {"period": "terminal_sequential_out_of_sample", "firstDate": str(meta.date.min()) if len(meta) else None,
            "lastDate": str(meta.date.max()) if len(meta) else None, "origins": len(meta), "maturedValidOrigins": int(mature.sum()),
            "target": "realized_exit_level_change_over_origin_known_gross", "targetDefinition": "(realizedFuture-currentState)/scale",
            "features": features, "dependence": {"featureNames": columns, "correlation": correlation, "covariance": cov,
                "pairCounts": pair_counts, "covarianceDof": 1, "missingness": "pairwise_complete",
                "positiveSemidefiniteGuaranteed": False, "jointDistributions": joints,
                "jointPairBudget": MAX_JOINT_PAIRS, "totalPossiblePairs": len(pairs), "omittedPairs": max(0, len(pairs)-MAX_JOINT_PAIRS),
                "jointPairSelection": "declared_factor_order_then_derived_states_no_outcome_ranking"},
            "selectionUse": False, "lookedAtAfterSelection": True,
            "significance": {"status": "not_available", "pValues": None,
                "reason": "overlapping_labels_and_cross_sectional_temporal_dependence_not_adjusted_for_factor_tests"},
            "interpretation": "descriptive_out_of_sample_association_not_causal_effect_or_validated_alpha"}
