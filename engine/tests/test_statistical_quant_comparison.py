"""Missing cross-sections and one-model failures must remain visible evidence."""
import copy
import numpy as np
import pandas as pd
import pytest

from atlas_quant.statistical_quant.models import metrics
from atlas_quant.statistical_quant.inference import evaluate_forecast_uncertainty
from atlas_quant.statistical_quant.comparison import compare_factor_increment


def row(date, target="asset", predicted=200., status="valid", maturity="20251231"):
    return {"date": date, "targetId": target, "status": status, "labelMaturedAt": maturity,
        "currentState":100., "scale":100., "expectedEntry":predicted, "expectedFuture":predicted,
        "realizedEntry":200., "realizedFuture":200., "entryDate":"20250101", "targetDate":"20251231",
        "invalidReason":None if status == "valid" else "model_unavailable"}


def test_date_balancing_matches_bootstrap_estimate_when_cross_section_coverage_changes_sign():
    rows=[]
    for i,date in enumerate(pd.bdate_range("20250101",periods=80).strftime("%Y%m%d")):
        # Ten correctly predicted targets versus one badly predicted target on
        # alternate dates. Row pooling reverses the actual date-balanced result.
        rows.extend(row(date,f"asset{k}",200. if i%2==0 else 400.) for k in range(10 if i%2==0 else 1))
    actual=np.ones((len(rows),2))
    prediction=np.array([[(x["expectedEntry"]-100)/100,(x["expectedFuture"]-100)/100] for x in rows])
    dates=[x["date"] for x in rows]
    balanced=metrics(prediction,actual,dates)
    pooled=metrics(prediction,actual)
    interval=evaluate_forecast_uncertainty(rows,5,1,replications=200)
    assert pooled["mseImprovement"]>0 and balanced["mseImprovement"] == pytest.approx(-1.)
    assert balanced["observedDates"]==80 and balanced["weighting"]=="equal_weight_daily_average"
    assert balanced["mseImprovement"] == pytest.approx(interval["intervals"]["lossImprovement"]["estimate"])
    assert balanced["biasSign"] == "predicted_minus_realized"
    assert balanced["bias"][0] == pytest.approx(-interval["intervals"]["entryBias"]["estimate"])


def test_nonfinite_rows_are_removed_before_date_balancing_with_correct_denominator():
    pred=np.array([[1.,1.],[np.nan,np.nan],[3.,3.]])
    result=metrics(pred,np.ones((3,2)),["a","a","b"])
    assert result["observations"]==2 and result["observedDates"]==2
    assert result["mseImprovement"] == pytest.approx(-1.)


def test_factor_increment_discloses_asymmetric_model_failure_and_unmatured_coverage():
    full=[row("20250101"),row("20250102",status="invalid"),row("20250103"),row("20250104",maturity=None)]
    base=[row("20250101",predicted=100.),row("20250102",predicted=100.),row("20250103",status="invalid"),row("20250104",maturity=None)]
    comparison=compare_factor_increment(full,base,["factor:test"],[],{})
    assert comparison["bothModelValidOnly"] and not comparison["outputValidityMasksIdentical"]
    assert comparison["coverage"] == {"fullValidRows":3,"baselineValidRows":3,
        "fullMatureRows":3,"baselineMatureRows":3,"fullValidMatureRows":2,"baselineValidMatureRows":2,"matchedRows":1,"matchedDates":1,
        "fullValidUnmatchedRows":2,"baselineValidUnmatchedRows":2,
        "fullUnavailableModelRows":1,"baselineUnavailableModelRows":1,
        "fullInvalidRows":1,"baselineInvalidRows":1}
    assert comparison["pairedObservations"]==1 and comparison["dateBalancedMseImprovement"]==1.
    assert len(comparison["baselineRows"])==4


@pytest.mark.parametrize("change",["target","scale","truth","duplicate","maturity"])
def test_comparison_cannot_silently_use_different_targets_or_single_sided_maturity(change):
    full=[row("20250101")]; base=copy.deepcopy(full)
    if change=="target":base[0]["targetId"]="different"
    elif change=="scale":base[0]["scale"]=200.
    elif change=="truth":base[0]["realizedFuture"]=300.
    elif change=="duplicate":base.append(copy.deepcopy(base[0]))
    else:
        base[0]["labelMaturedAt"]=None
        comparison=compare_factor_increment(full,base,["factor:test"],[],{})
        assert comparison["pairedObservations"]==0 and comparison["status"]=="unavailable"
        return
    with pytest.raises(ValueError):
        compare_factor_increment(full,base,["factor:test"],[],{})
