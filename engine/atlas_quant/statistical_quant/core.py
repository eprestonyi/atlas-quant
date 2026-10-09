"""Forecast artifacts are immutable research products, separate from execution."""
from __future__ import annotations
import copy
from dataclasses import replace
import numpy as np
from threadpoolctl import threadpool_limits
from .schema import VERSION, digest, fail, prediction_config, validate
from .targets import build_samples
from .validation import forecast
from .execution import execute
from .reporting import diagnostics_summary, trial_summary


def _envelope(strategy, provenance, audit, artifact, panel, dates, execution_only=False):
    from ..engine import _finite_json
    from ..context_sources import summarize_context_provenance
    metrics, equity, trades, execution = execute(panel, dates, strategy, artifact)
    diagnostics = artifact["diagnostics"]
    positive = diagnostics["metrics"].get("mseImprovement")
    warnings = ["THEORETICAL_SHORT_INVENTORY_NOT_VERIFIED", "FRACTIONAL_ADJUSTED_UNITS_NOT_EXCHANGE_LOTS",
                "FIXED_COSTS_NOT_HISTORICAL_FEE_SCHEDULE", "NO_INTRADAY_LIMIT_QUEUE_OR_CAPACITY_MODEL",
                "OVERLAPPING_LABELS_ARE_NOT_INDEPENDENT", "PREDICTIVE_IMPROVEMENT_IS_NOT_PROFITABILITY",
                "CURRENT_UNIVERSE_NOT_HISTORICAL_CONSTITUENTS"]
    if provenance.get("synthetic"):
        warnings.insert(0, "SYNTHETIC_DATA_NOT_MARKET_EVIDENCE")
    state_effects = [x for f in artifact["modelFits"] for x in f.get("stateEffects", [])]
    if strategy["model"]["family"] in ("mean_reversion", "pair_reversion") and not any(x["negativeEffectObserved"] for x in state_effects):
        warnings.append("NEGATIVE_STATE_EFFECT_NOT_ESTABLISHED_BY_SELECTED_MODEL")
    result = {"schemaVersion": 2, "status": "completed", "engineVersion": VERSION,
              "strategy": strategy, "research": {"mode": "statistical_quant", "forecastFirst": True,
                  "executionOnly": execution_only, "predictionRefitPerformed": not execution_only,
                  "observationDays": strategy["research"]["observationDays"]},
              "provenance": {**summarize_context_provenance(provenance), **audit}, "forecasts": artifact,
              "validation": diagnostics_summary(diagnostics), "selection": {"winner": diagnostics["selectedModel"]["estimator"],
                  "winnerTrialId": diagnostics["selectedModel"]["id"], "params": diagnostics["selectedModel"]["params"],
                  "metric": "date_balanced_joint_entry_exit_normalized_mse", "trials": trial_summary(diagnostics["finalTrials"]),
                  "holdoutUsedForSelection": False, "qualified": False, "deploymentQualified": False,
                  "evidenceStatus": "OOS_FORECAST_IMPROVEMENT_NOT_SIGNIFICANCE_TESTED" if positive is not None and positive>0 else "NO_VALIDATED_FORECAST_EDGE"},
              "metrics": metrics, "equity": equity, "trades": trades, "execution": execution,
              "factors": [{"id": f["id"], "role": f["role"], "expression": f["expression"]} for f in strategy["factors"]],
              "warnings": warnings}
    return _finite_json(result)


def run_statistical_quant(strategy, data, provenance=None, *, plan_sink=None):
    from ..engine import _prepare_data, _finite_json
    s = validate(strategy)
    if provenance is not None and not isinstance(provenance, dict):
        fail("INVALID_PROVENANCE", "来源记录须为对象")
    p = copy.deepcopy(provenance or {})
    panel, dates, audit = _prepare_data(data, s, p)
    with threadpool_limits(limits=1):
        samples = build_samples(panel, dates, s)
        return _research_from_samples(s, panel, dates, audit, p, samples, plan_sink=plan_sink)


def declared_fit_budget(s, samples, *, max_forecasts=None):
    """The existing complete branch/candidate/origin upper bound, before fitting."""
    # Declare the complete selection budget before any model is fitted. Failed
    # rolling fits may retry at the next origin, so cap them by all origin dates.
    from .validation import forecast_origins
    from .models import candidates
    factor_columns = [name for name in samples.X if name.startswith("factor:")]
    branches = 2 if factor_columns else 1
    _, terminal_origins = forecast_origins(samples, s, max_forecasts=max_forecasts)
    terminal_dates = samples.meta.loc[terminal_origins, "date"].nunique()
    candidate_count = len(candidates(s["model"]["estimator"], s["model"].get("search")))
    outer_count, inner_count = s["validation"]["outerFolds"], s["validation"]["innerFolds"]
    groups = samples.meta.targetId.nunique() if s["model"].get("parameterSharing") == "per_target" else 1
    selection_fit_cap = branches*groups*((outer_count+1)*inner_count*candidate_count+outer_count)
    candidate_export_cap = groups*candidate_count if s["model"].get("search") else 0
    result = {"branches": branches, "includesFactorFreeBaseline": bool(factor_columns),
        "nestedSelectionAndOuterFitCap": selection_fit_cap,
        "sequentialFitAttemptCap": int(branches*groups*terminal_dates),
        "maximumFitAttempts": int(selection_fit_cap+candidate_export_cap+branches*groups*terminal_dates),
        "declaredBeforeFitting": True, "actualFitsMayBeLower": True}
    if s["model"].get("search"):
        result.update(candidateFunctionFitCap=int(candidate_export_cap), modelGroups=int(groups),
                      internalBasisSubfitCapPerCandidate=1+len(samples.X.columns))
    return result


def forecast_branches(s, samples, *, plan_sink=None, max_forecasts=None, runtime=None,
                      export_functions=True, branch_sink=None):
    """Common Samples-only research, without an artifact or execution envelope.

    Default export/callback behavior preserves every existing route. Callbacks
    are process-local diagnostics, never a callable read from a user contract.
    """
    from .ai_review import attach_reviewer
    runtime = attach_reviewer(s, runtime)
    limits = {} if max_forecasts is None else {"max_forecasts": max_forecasts}
    if runtime is not None:
        limits["runtime"] = runtime
    factor_columns = [name for name in samples.X if name.startswith("factor:")]
    declared_budget = declared_fit_budget(s, samples, max_forecasts=max_forecasts)
    baseline_rows, baseline_fits, baseline_diagnostics = None, None, None
    with threadpool_limits(limits=1):
        if plan_sink is not None:
            from .validation import forecast_origins
            holdout, origins = forecast_origins(samples, s, max_forecasts=max_forecasts)
            plan_sink({"schemaVersion": 1, "source": "samples_before_model_fitting",
                "baselineRequired": any(name.startswith("factor:") for name in samples.X),
                "holdoutStart": holdout,
                "origins": [{"date": row.date, "targetId": row.targetId,
                             "entryDate": row.entryDate, "targetDate": row.targetDate,
                             "inputValid": bool(row.inputValid)}
                            for row in samples.meta.loc[origins].itertuples()]})
        if s["model"]["family"] in ("event", "fundamental"):
            valid_dates = samples.meta.loc[samples.meta.inputValid, "date"].nunique()
            if valid_dates < s["validation"]["minTrainDates"]+40:
                fail("MISSING_MODEL_DATA", "条件模型缺少足够实际可观测输入日期用于嵌套验证")
        if branch_sink is not None:
            branch_sink("main_started", None)
        main_limits = limits if export_functions else {**limits, "export_functions": False}
        rows, fits, diagnostics = forecast(samples, s, **main_limits)
        diagnostics["inputCoverage"] = {"totalOrigins": len(samples.meta),
            "validInputOrigins": int(samples.meta.inputValid.sum()),
            "invalidReasons": {str(k): int(v) for k, v in samples.meta.invalidReason.dropna().value_counts().items()},
            "features": [{"name": name, "finiteOrigins": int(np.isfinite(samples.X[name]).sum())} for name in samples.X]}
        if branch_sink is not None:
            branch_sink("main_completed", copy.deepcopy({"rows": rows, "fits": fits, "diagnostics": diagnostics}))
        if factor_columns:
            # Hold q, labels, coverage mask, maturity and candidate budget fixed.
            # Re-select/re-fit the state-only baseline in its own train folds.
            baseline_samples = replace(samples, X=samples.X.drop(columns=factor_columns))
            if branch_sink is not None:
                branch_sink("baseline_started", None)
            baseline_rows, baseline_fits, baseline_diagnostics = forecast(baseline_samples, s, export_functions=False, **limits)
            if branch_sink is not None:
                branch_sink("baseline_completed", copy.deepcopy({"rows": baseline_rows, "fits": baseline_fits,
                                                                 "diagnostics": baseline_diagnostics}))
            from .comparison import compare_factor_increment
            diagnostics["factorIncrement"] = compare_factor_increment(
                rows, baseline_rows, factor_columns, baseline_fits, baseline_diagnostics)
        else:
            diagnostics["factorIncrement"] = {"status": "not_applicable", "reason": "no_predictor_or_event_factor_columns",
                "hedgeFactorsAblated": False}
    if branch_sink is not None:
        branch_sink("diagnostics_started", None)
    from .factor_diagnostics import factor_diagnostics
    factor_research = {"schemaVersion": 1, "modelFunctions": [
        {"modelFitId": f["id"], "artifactId": f["functionArtifact"]["artifactId"],
         "path": f"forecasts.modelFits[{i}].functionArtifact"}
        for i, f in enumerate(fits) if "functionArtifact" in f],
        "diagnostics": factor_diagnostics(samples, diagnostics["holdoutStart"], s["factors"]),
        "editSemantics": "derived_function_requires_new_validation_original_report_is_immutable"}
    if "modelSearch" in diagnostics:
        factor_research["candidateModelFunctions"] = [
            {"candidateId": candidate["id"], "modelFitId": candidate["fit"]["id"],
             "artifactId": candidate["functionArtifact"]["artifactId"],
             "path": f"forecasts.diagnostics.modelSearch.candidates[{i}].functionArtifact"}
            for i, candidate in enumerate(diagnostics["modelSearch"]["candidates"])
            if candidate.get("functionArtifact") is not None]
    diagnostics["selectionAudit"]["researchFitBudget"] = {
        **declared_budget, "sequentialFitAttempts": len(fits)+(len(baseline_fits) if factor_columns else 0)}
    return {"rows": rows, "fits": fits, "diagnostics": diagnostics, "factorResearch": factor_research,
            "baseline": {"rows": baseline_rows, "fits": baseline_fits, "diagnostics": baseline_diagnostics}
                        if factor_columns else None,
            "declaredBudget": declared_budget}


def _research_from_samples(s, panel, dates, audit, p, samples, *, plan_sink=None, max_forecasts=None, runtime=None):
    """Legacy artifact/execute envelope around the unchanged common research."""
    from ..engine import _finite_json
    result = forecast_branches(s, samples, plan_sink=plan_sink, max_forecasts=max_forecasts, runtime=runtime)
    rows, fits, diagnostics = result["rows"], result["fits"], result["diagnostics"]
    factor_research = result["factorResearch"]
    artifact = _finite_json({"schemaVersion": 1, "predictionConfigHash": digest(prediction_config(s)),
                            "dataFingerprint": audit["dataSha256"], "sourceStrategy": copy.deepcopy(s),
                            "rows": rows, "totalRows": len(rows), "truncated": False,
                            "targetDefinitions": list(samples.definitions.values()), "modelFits": fits,
                            "hedgeFits": samples.hedge_fits, "diagnostics": diagnostics, "factorResearch": factor_research})
    artifact["artifactId"] = digest(artifact)
    return _envelope(s, p, audit, artifact, panel, dates)


def execute_forecasts(strategy, data, artifact, provenance=None):
    """No fitting or target construction. Only execute a verified frozen artifact.

    Callers may change execution/portfolio/costs/name, never prediction
    inputs. A bundle's original full-precision data and calendar must accompany it.
    """
    from ..engine import _prepare_data
    s = validate(strategy)
    from ..factors import validate_expression
    financial_factors = any(
        field.startswith("model_fin_")
        for factor in s["factors"]
        for field in validate_expression(factor["expression"])["fields"]
    )
    financial_rows = any(
        str(column).startswith("model_fin_")
        for column in getattr(data, "columns", ())
    )
    financial_roots = isinstance(provenance, dict) and any(
        key in provenance for key in (
            "financialInputs", "financialDatasetRoot", "financialCompositionVersion",
        )
    )
    if financial_factors or financial_rows or financial_roots:
        fail(
            "FINANCIAL_REPLAY_NOT_AVAILABLE",
            "财务预测的执行重放尚未接入完整输入证据闭包；进程内来源登记不能替代归档复现。",
        )
    if not isinstance(artifact, dict) or artifact.get("schemaVersion") != 1 or artifact.get("truncated") is not False:
        fail("FORECAST_ARTIFACT_MISMATCH", "需要完整版本化预测产物")
    a = copy.deepcopy(artifact)
    ident = a.pop("artifactId", None)
    try:
        actual_id = digest(a)
    except (ValueError, TypeError):
        fail("FORECAST_ARTIFACT_MISMATCH", "预测产物包含无效数据")
    if ident != actual_id or not isinstance(a.get("rows"), list) or a.get("totalRows") != len(a["rows"]):
        fail("FORECAST_ARTIFACT_MISMATCH", "预测产物指纹或完整记录数不匹配")
    if a.get("predictionConfigHash") != digest(prediction_config(s)):
        fail("FORECAST_ARTIFACT_MISMATCH", "重放只能改变执行、组合与费用，不能替换预测研究配置")
    if digest(prediction_config(validate(a.get("sourceStrategy")))) != a["predictionConfigHash"]:
        fail("FORECAST_ARTIFACT_MISMATCH", "预测产物原始配置不匹配")
    p = copy.deepcopy(provenance or {})
    if not isinstance(p, dict):
        fail("INVALID_PROVENANCE", "来源记录须为对象")
    panel, dates, audit = _prepare_data(data, s, p)
    if a.get("dataFingerprint") != audit["dataSha256"]:
        fail("FORECAST_ARTIFACT_MISMATCH", "必须使用原始冻结行情与日历，不能以新取数据重放")
    a["artifactId"] = ident
    return _envelope(s, p, audit, a, panel, dates, True)
