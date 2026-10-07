"""Date-clustered, dependence-aware *aggregate* forecast diagnostics.

These intervals describe mean historical OOS loss/calibration, not a prediction
interval for tomorrow's price. No confidence bound certifies a profitable trade.
"""
from __future__ import annotations

import math
from collections import defaultdict
import numpy as np


def _daily_statistics(rows):
    grouped = defaultdict(list)
    used = 0
    numeric_failures = 0
    fields = ("currentState", "scale", "expectedEntry", "expectedFuture", "realizedEntry", "realizedFuture")
    for row in rows:
        if row.get("status") != "valid" or row.get("labelMaturedAt") is None:
            continue
        values = [row.get(k) for k in fields]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            continue
        state, scale, ve, vx, entry, exit_ = values
        if scale <= 0:
            continue
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            actual = (np.array([entry, exit_])-state)/scale
            predicted = (np.array([ve, vx])-state)/scale
            errors = actual-predicted
            loss_improvement = float(np.mean(actual**2)-np.mean(errors**2))
            statistics = np.array([loss_improvement, *errors, errors[1]-errors[0]])
        if not np.isfinite(statistics).all():
            numeric_failures += 1
            continue
        grouped[row["date"]].append(statistics)
        used += 1
    dates = sorted(grouped)
    # All securities/baskets on a date stay together. More correlated rows on
    # one date must not turn into more independent temporal evidence.
    values = np.array([(np.asarray(grouped[d])/len(grouped[d])).sum(axis=0) for d in dates], dtype=float).reshape(-1, 4)
    return dates, values, used, numeric_failures


def _block_means(values, length, replications, seed):
    """Circular fixed-length blocks; every origin has equal sampling weight."""
    n = len(values)
    rng = np.random.default_rng(seed)
    blocks = math.ceil(n/length)
    means = np.empty((replications, values.shape[1]))
    offsets = np.arange(length)
    for start in range(0, replications, 128):
        size = min(128, replications-start)
        origins = rng.integers(0, n, size=(size, blocks))
        indices = ((origins[:, :, None]+offsets) % n).reshape(size, -1)[:, :n]
        means[start:start+size] = (values[indices]/n).sum(axis=1)
    return means


def evaluate_forecast_uncertainty(rows, horizon_sessions, observation_days, replications=1000, seed=17):
    """Deterministic percentile intervals with declared block-length sensitivity.

    Block length is a predeclared heuristic: max(label-span in observation
    periods, ceil(n**(1/3)), 2). It is not a fitted optimal dependence estimate.
    At least eight nominal blocks and forty observed dates are required.
    """
    for value, lower, upper, name in ((horizon_sessions, 1, 60, "horizon_sessions"),
                                     (observation_days, 1, 60, "observation_days"),
                                     (replications, 200, 5000, "replications"),
                                     (seed, 0, 2**32-1, "seed")):
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise ValueError(name + " outside supported integer range")
    dates, daily, used, numeric_failures = _daily_statistics(rows)
    n = len(dates)
    overlap = math.ceil((horizon_sessions+1)/observation_days)
    length = max(2, overlap, math.ceil(n**(1/3)))
    names = ("lossImprovement", "entryBias", "exitBias", "remainingChangeBias")
    result = {
        "method": "date_clustered_circular_block_percentile_bootstrap",
        "version": 1, "status": "unavailable", "observedDates": n,
        "forecastRows": used, "excludedRows": len(rows)-used,
        "numericFailureRows": numeric_failures,
        "crossSectionUnit": "equal_weight_daily_average",
        "independentSampleSize": None, "blockLengthObservations": length,
        "labelOverlapObservations": overlap, "replications": replications,
        "seed": seed, "confidenceLevel": .95, "intervals": None,
        "blockLengthRule": "max(2,ceil((horizonSessions+1)/observationDays),ceil(observedDates^(1/3)))",
        "biasSign": "realized_minus_predicted", "lossImprovementSign": "no_change_loss_minus_model_loss",
        "units": {"lossImprovement": "squared_change_over_origin_known_gross",
                  "bias": "change_over_origin_known_gross"},
        "individualPricePredictionInterval": False, "multipleExperimentsAdjusted": False,
        "modelSelectionUncertaintyIncluded": False, "profitabilityTested": False,
        "assumptions": ["weakly_stationary_date_aggregated_loss_and_error_sequence",
                        "serial_dependence_adequately_captured_by_declared_blocks",
                        "finite_moments_and_sufficient_effective_temporal_information",
                        "event_rows_condition_on_observed_event_origins"],
        "limitations": ["block_length_is_a_heuristic_not_optimal_or_a_dependence_test",
                        "circular_wrap_joins_end_to_start_under_stationarity_assumption",
                        "regime_changes_and_long_memory_can_invalidate_coverage",
                        "rolling_model_refit_is_not_reestimated_inside_bootstrap",
                        "repeated_research_and_selection_across_experiments_not_corrected"],
        "blockSensitivity": [],
    }
    if numeric_failures or not np.isfinite(daily).all():
        # Do not silently discard extreme valid observations and issue an
        # apparently successful confidence interval for a selected subset.
        result["unavailableReason"] = "nonfinite_derived_loss_or_error"
        return result
    if n < max(40, 8*length):
        result["unavailableReason"] = "insufficient_observed_dates_for_eight_nominal_blocks"
        return result
    observed = (daily/n).sum(axis=0)
    # Lower bound does not go below the horizon-overlap span. Alternative
    # lengths are diagnostic, never selected by the most favorable interval.
    lengths = sorted({max(2, overlap, length//2), length, 2*length})
    for candidate in lengths:
        if n < 8*candidate:
            result["blockSensitivity"].append({"blockLengthObservations": candidate,
                                               "status": "insufficient_dates", "interval": None})
            continue
        means = _block_means(daily, candidate, replications, seed)
        with np.errstate(over="ignore", invalid="ignore"):
            intervals = np.quantile(means, [.025, .975], axis=0)
        if not np.isfinite(intervals).all() or not np.isfinite(observed).all():
            result["intervals"] = None
            result["unavailableReason"] = "nonfinite_bootstrap_aggregate"
            return result
        result["blockSensitivity"].append({"blockLengthObservations": candidate, "status": "computed",
                                           "interval": intervals[:, 0].tolist()})
        if candidate == length:
            result["intervals"] = {name: {"estimate": float(observed[i]),
                                          "lower": float(intervals[0, i]), "upper": float(intervals[1, i])}
                                   for i, name in enumerate(names)}
    result["status"] = "computed_under_declared_assumptions"
    result["unavailableReason"] = None
    result["observationStart"], result["observationEnd"] = dates[0], dates[-1]
    return result
