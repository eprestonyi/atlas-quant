"""Prefix-only DSL evaluation and fixed-q features; no learned transformation."""
from __future__ import annotations

import numpy as np

from ..factors import evaluate_expression
from ..statistical_quant.targets import _features
from .contract import digest
from .research_contract import STATE_COLUMNS


def _origin_features(source, spec, research, targets):
    """Internal generator; target rows are produced by the bound Stage 1 kernel."""
    if not targets["targets"]:
        return
    frame = source.frame()
    domain = source.domain
    dates = domain["calendar"]
    closes = frame.close.unstack("ts_code")
    definitions = {t["targetVersionId"]: t for t in targets["targets"]}
    rows_by_origin = {d: [] for d in spec["origins"]}
    for row in targets["rows"]:
        rows_by_origin[row["origin"]].append(row)
    observed = source.observed_rows()
    strategy = {"model": {"family": "pair_reversion"}, "factors": research["factors"]}
    feature_columns = [*STATE_COLUMNS, *("factor:" + f["id"] for f in research["factors"])]
    for origin in spec["origins"]:
        index = dates.index(origin)
        # The DSL evaluator never receives later rows, even for rank/rolling.
        prefix = frame.loc[:origin]
        factor_at_origin = {
            f["id"]: (evaluate_expression(f["expression"], prefix) * f["direction"]).loc[origin]
            for f in research["factors"]
        }
        prefix_root = digest({"symbols": domain["symbols"], "calendar": dates[:index + 1],
                              "fields": list(source.fields), "cutoff": origin,
                              "observedRows": [r for r in observed if r["trade_date"] <= origin]})
        for row in rows_by_origin[origin]:
            target = definitions[row["targetVersionId"]]
            symbols = [leg["symbol"] for leg in target["legs"]]
            quantities = np.asarray([leg["quantity"] for leg in target["legs"]])
            history = closes.loc[dates[index - 60:index + 1], symbols].to_numpy()
            history_missing = [symbol for col, symbol in enumerate(symbols)
                               if not np.isfinite(history[:, col]).all()]
            factor_values = {key: values.reindex(symbols).to_numpy() for key, values in factor_at_origin.items()}
            factor_missing = {key: [symbol for i, symbol in enumerate(symbols) if not np.isfinite(values[i])]
                              for key, values in factor_values.items() if not np.isfinite(values).all()}
            feat = {name: np.nan for name in feature_columns}
            reason = None
            if row["current"]["status"] != "complete":
                reason = "current_" + row["current"]["status"]
            elif history_missing:
                reason = "missing_close_history"
            else:
                with np.errstate(all="ignore"):
                    feat, _ = _features(history, quantities, row["current"]["grossScale"],
                                        factor_values, strategy)
                if not all(np.isfinite(feat[name]) for name in STATE_COLUMNS):
                    reason = "state_feature_arithmetic_unavailable"
                feat = {name: value if np.isfinite(value) else np.nan for name, value in feat.items()}
            yield row, feat, {
                "featureInputValid": reason is None, "invalidReason": reason,
                "missingHistoryLegs": history_missing, "missingFactorLegs": factor_missing,
                "featureCutoff": origin, "featurePrefixRoot": prefix_root,
            }
