"""Causal semantic transforms, train-only scaling and portable v2 counterexamples."""
import copy
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits

from atlas_quant.engine import ResearchError
from atlas_quant.statistical_quant.models import candidates, fit
from atlas_quant.statistical_quant.model_function import export_function, predict_function, validate_function, function_digest
from atlas_quant.statistical_quant.preprocessing import (
    automatic_metadata, evaluate_factors, factor_descriptor, transform_values, PRICE_TRANSFORM,
)
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.targets import build_samples
from atlas_quant.statistical_quant.validation import _train
from test_statistical_quant import strategy


CONTEXT = "ext_ctx_000300_sh_close"


def automatic_strategy(kind="asset_price", estimator="ridge"):
    s = strategy(kind, estimator)
    s["factors"] = [
        {"id": "size", "expression": "total_mv", "direction": -1},
        {"id": "volume", "expression": "vol"},
        {"id": "yield", "expression": "pe_ttm"},
        {"id": "turnover", "expression": "turnover_rate"},
        {"id": "price", "expression": "close"},
        {"id": "custom", "expression": "log(total_mv)"},
        {"id": "market", "expression": CONTEXT},
    ]
    s["preprocess"] = {"automatic": {"schema": "auto-factor-preprocess/1"}, "decorrelation": "none"}
    return validate(s)


def panel_fixture(count=150):
    dates = pd.bdate_range("20230102", periods=count).strftime("%Y%m%d").tolist()
    symbols = automatic_strategy()["universe"]["symbols"]
    rng = np.random.default_rng(541)
    market = 3500 * np.exp(np.cumsum(rng.normal(.0004, .012, count)))
    pieces = []
    for j, symbol in enumerate(symbols):
        close = (10+j*12)*np.exp(np.cumsum(rng.normal(.0002, .015, count)))
        pieces.append(pd.DataFrame({"trade_date": dates, "ts_code": symbol, "close": close,
            "open": close*1.002, "total_mv": 100000*(j+1)*np.exp(rng.normal(0,.3,count)),
            "vol": rng.uniform(0, 100000, count), "pe_ttm": rng.normal(20,8,count),
            "turnover_rate": rng.uniform(.1,5,count), CONTEXT: market}))
    return pd.concat(pieces).set_index(["trade_date", "ts_code"]).sort_index(), dates, symbols


def rows(frame):
    return [{key: None if pd.isna(value) else float(value) for key, value in row.items()}
            for row in frame.to_dict(orient="records")]


def fitted_fixture(spec, *, include_source=False):
    s = automatic_strategy(estimator=spec["estimator"])
    panel, dates, _ = panel_fixture(190)
    samples = build_samples(panel, dates, s)
    # Small local unit fixture only; fit a fixed mature prefix, no protocol run.
    X, y = samples.X.iloc[:180], samples.y.iloc[:180]
    with threadpool_limits(limits=1):
        model = fit(spec, X, y, s["preprocess"], automatic_metadata=samples.automatic_preprocessing,
                    training_dates=samples.meta.date.iloc[:180].tolist())
    audit = {**model.audit, "trainStart": dates[61], "trainEnd": dates[120],
             "informationCutoff": dates[130], "labelEndMax": dates[126], "trainDates": 60}
    artifact = export_function(model, audit, s)
    probes = samples.X.iloc[200:204][model.columns].copy()
    probes.iloc[1, :] = np.nan
    probes.iloc[2, :] = -1e12
    probes.iloc[3, :] = 1e12
    return (model, artifact, probes, audit, s) if include_source else (model, artifact, probes)


@pytest.mark.parametrize("kind,values,expected", [
    ("log_positive", [0,-1,1,np.e], [np.nan,np.nan,0,1]),
    ("log1p_nonnegative", [-1,0,1], [np.nan,0,np.log(2)]),
    ("reciprocal_nonzero", [-2,0,4], [-.5,np.nan,.25]),
    ("percent_to_fraction", [-5,0,12], [-.05,0,.12]),
])
def test_declared_invalid_domains_do_not_take_absolute_value_or_silently_clip(kind, values, expected):
    result = transform_values(pd.DataFrame({"a": values}), {"kind": kind})
    np.testing.assert_allclose(result.a, expected, equal_nan=True)


def test_price_shock_is_divided_by_strictly_previous_twenty_returns_not_itself():
    r = np.array([.01,-.01,.02,-.02]*7)
    prices = np.r_[100, 100*np.cumprod(1+r)]
    actual = transform_values(pd.DataFrame({"p": prices}), PRICE_TRANSFORM)
    assert actual.p.iloc[:21].isna().all()
    assert actual.p.iloc[21] == pytest.approx(r[20]/np.std(r[:20],ddof=1))
    shocked = prices.copy(); shocked[21] = shocked[20]*1.8
    result = transform_values(pd.DataFrame({"p": shocked}), PRICE_TRANSFORM)
    assert result.p.iloc[21] == pytest.approx(.8/np.std(r[:20],ddof=1))
    np.testing.assert_array_equal(result.iloc[:21], actual.iloc[:21])
    gap = prices.copy(); gap[10] = np.nan
    assert np.isnan(transform_values(pd.DataFrame({"p":gap}), PRICE_TRANSFORM).p.iloc[21])
    assert transform_values(pd.DataFrame({"p":np.ones(40)*100}), PRICE_TRANSFORM).isna().all().all()


def test_economic_transform_precedes_negative_direction_and_short_leg_aggregation():
    s = automatic_strategy("frozen_basket")
    symbols = s["universe"]["symbols"][:2]
    s["target"]["basket"] = {"method":"fixed", "symbols":symbols, "formationDays":60,
                               "quantities":{symbols[0]:1, symbols[1]:-2}}
    panel, dates, _ = panel_fixture()
    samples = build_samples(panel, dates, s)
    t = samples.start_index; px = panel.close.unstack().loc[dates[t],symbols].to_numpy()
    mv = panel.total_mv.unstack().loc[dates[t],symbols].to_numpy()
    dollar = np.array([1,-2])*px / np.abs(np.array([1,-2])*px).sum()
    assert samples.X["factor:size"].iloc[0] == pytest.approx(dollar @ -np.log(mv))
    assert samples.X["factor:custom"].iloc[0] == pytest.approx(dollar @ np.log(mv))
    assert np.isfinite(samples.X["factor:size"]).all()


def test_global_context_survives_neutral_basket_and_missing_stock_broadcast():
    s = automatic_strategy("frozen_basket")
    panel, dates, symbols = panel_fixture()
    # Identical prices + +/-1 quantities give exactly zero net dollar weight.
    wide = panel.close.unstack()
    for symbol in symbols:
        panel.loc[(slice(None),symbol), "close"] = wide[symbols[0]].to_numpy()
    s["target"]["basket"] = {"method":"fixed", "symbols":symbols[:2], "formationDays":60,
                               "quantities":{symbols[0]:1,symbols[1]:-1}}
    panel.loc[(dates[45],symbols[0]),CONTEXT] = np.nan
    values, meta = evaluate_factors(panel, dates, symbols, s)
    assert values["market"].loc[dates[61]].nunique() == 1
    samples = build_samples(panel, dates, s)
    assert samples.X["factor:market"].iloc[0] == values["market"].loc[dates[61],symbols[0]]
    assert abs(samples.X["factor:market"].iloc[0]) > 1e-6
    assert next(x for x in meta["factors"] if x["feature"] == "factor:market")["scope"] == "global"
    panel.loc[(dates[45],symbols[1]),CONTEXT] += 1
    with pytest.raises(ResearchError, match="冲突"):
        evaluate_factors(panel, dates, symbols, s)


def test_wholly_missing_context_day_stays_missing_and_mixed_expression_stays_asset():
    s = automatic_strategy(); panel, dates, symbols = panel_fixture()
    s["factors"].append({"id":"relative","expression":"close / "+CONTEXT,"direction":1,"role":"predictor"})
    panel.loc[(dates[45],slice(None)),CONTEXT] = np.nan
    values, metadata = evaluate_factors(panel,dates,symbols,s)
    assert values["market"].loc[dates[61]].isna().all()
    assert metadata["factors"][-1]["scope"] == "asset"
    assert metadata["factors"][-1]["transform"] == {"kind":"identity"}


def test_future_changes_cannot_modify_earlier_features_or_training_scaler():
    s = automatic_strategy(); panel, dates, symbols = panel_fixture(190)
    s["validation"]["minTrainDates"] = 40
    before = build_samples(panel, dates, s)
    changed = panel.copy()
    changed.loc[(slice(dates[130],None),slice(None)), :] *= 1000
    after = build_samples(changed, dates, s)
    early = before.meta.date < dates[130]
    pd.testing.assert_frame_equal(before.X.loc[early], after.X.loc[early])
    spec = candidates("ridge")[0]
    with threadpool_limits(limits=1):
        first, first_audit = _train(before,spec,dates[:130],dates[130],s)
        second, second_audit = _train(after,spec,dates[:130],dates[130],s)
    assert first_audit == second_audit
    assert first_audit["labelEndMax"] < dates[130]
    assert first_audit["scalerMethod"] == "median_iqr"
    # Explicit independent calculation on the selected matured training rows.
    mask = (before.meta.date < dates[130]) & (before.meta.targetDate < dates[130])
    for i,column in enumerate(first.columns):
        train = before.X.loc[mask,column]
        if column=="factor:market":
            train=train.loc[~before.meta.loc[mask,"date"].duplicated()]
        clipped = train.clip(lower=train.quantile(.01),upper=train.quantile(.99))
        filled = clipped.fillna(clipped.median())
        assert first_audit["scalerMean"][i] == pytest.approx(filled.median())
        iqr=filled.quantile(.75)-filled.quantile(.25)
        assert first_audit["scalerScale"][i] == pytest.approx(iqr if iqr>=10*np.finfo(float).eps else 1.)
        assert first_audit["automaticFitRows"][i] == len(train)
    assert set(first.columns) == set(before.X.columns)  # no hidden PCA projection
    np.testing.assert_array_equal(first.predict(before.X.loc[early]),second.predict(before.X.loc[early]))


@pytest.mark.parametrize("spec", candidates("auto"), ids=lambda spec:spec["id"])
def test_all_v2_estimators_export_same_numeric_function_after_robust_preprocessing(spec):
    model, artifact, probes = fitted_fixture(spec)
    assert artifact["schema"] == "atlas-model-function/2"
    with threadpool_limits(limits=1):
        expected = model.predict(probes)
    result = predict_function(json.loads(json.dumps(artifact)),rows(probes))
    np.testing.assert_allclose(result["normalizedChanges"],expected,rtol=1e-12,atol=1e-14)
    kinds = {d["transform"]["kind"] for d in artifact["featureConstruction"]["automatic"]["factors"]}
    assert len(kinds)==6


@pytest.mark.parametrize("bad", [None, True, {}, {"schema":"auto-factor-preprocess/999"}, {"schema":"auto-factor-preprocess/1","x":1}])
def test_unknown_automatic_protocols_are_not_silently_treated_as_legacy(bad):
    s = automatic_strategy(); s["preprocess"]["automatic"] = bad
    with pytest.raises(ResearchError): validate(s)


def test_v1_absence_remains_absent_and_raw_unadjusted_price_is_rejected_before_fetch():
    s = validate(strategy())
    assert "automatic" not in s["preprocess"]
    s["factors"] = [{"id":"raw","expression":"raw_close"}]
    s["preprocess"]["automatic"] = {"schema":"auto-factor-preprocess/1"}
    with pytest.raises(ResearchError) as error: validate(s)
    assert error.value.code == "AUTO_FACTOR_REQUIRES_ADJUSTED_PRICE"


def test_context_requires_explicit_automatic_contract_and_domain_errors_are_not_guessed():
    s = automatic_strategy(); del s["preprocess"]["automatic"]
    with pytest.raises(ResearchError) as error: validate(s)
    assert error.value.code == "CONTEXT_REQUIRES_AUTOMATIC_PREPROCESSING"
    for expression, expected in (("log(total_mv)","identity"),("total_mv", "log_positive"),
                                 ("pe_ttm", "reciprocal_nonzero"),("ext_ctx_801780_si_float_mv","log_positive"),
                                 ("fd_roe","percent_to_fraction"),("fd_bps","identity"),("fd_current_ratio","identity")):
        descriptor = factor_descriptor({"id":"wrong_display_name", "expression":expression, "direction":1, "role":"predictor"})
        assert descriptor["transform"]["kind"] == expected
    unknown=automatic_strategy()
    unknown["factors"]=[{"id":"ctx", "expression":"ext_ctx_999999_fake_close"}]
    with pytest.raises(ResearchError) as error: validate(unknown)
    assert error.value.code=="CONTEXT_FIELD_UNKNOWN"


def test_global_train_statistics_count_dates_once_even_in_unbalanced_stock_panel():
    from atlas_quant.statistical_quant.fold_preprocessing import AutomaticFoldTransform
    dates=pd.bdate_range("20240101",periods=21).strftime("%Y%m%d").tolist()
    x=pd.DataFrame({"global":np.r_[np.arange(19),np.nan,100],"asset":np.arange(21)})
    plain=AutomaticFoldTransform(columns=list(x),global_columns=["global"],dates=dates,winsorize=True,standardize=True).fit(x)
    indices=np.r_[np.arange(21),np.repeat(20,100)]
    unbalanced=x.iloc[indices].reset_index(drop=True)
    duplicate_dates=[dates[i] for i in indices]
    repeated=AutomaticFoldTransform(columns=list(x),global_columns=["global"],dates=duplicate_dates,winsorize=True,standardize=True).fit(unbalanced)
    for attr in ("lower_","upper_","statistics_","center_","scale_"):
        assert getattr(plain,attr)[0] == getattr(repeated,attr)[0]
    assert repeated.fit_rows_ == [21,121]
    assert repeated.observed_rows_ == [20,121]
    assert plain.center_[1] != repeated.center_[1]
    np.testing.assert_allclose(plain.transform(x)[:,0],repeated.transform(x)[:,0])
    unbalanced.iloc[-1,0]=101
    with pytest.raises(ResearchError,match="不一致"):
        AutomaticFoldTransform(columns=list(x),global_columns=["global"],dates=duplicate_dates,winsorize=True,standardize=True).fit(unbalanced)
    with pytest.raises(ResearchError) as error:
        AutomaticFoldTransform(columns=list(x),global_columns=["global"],dates=None,winsorize=True,standardize=True).fit(x)
    assert error.value.code=="MISSING_GLOBAL_TRAIN_DATES"
    insufficient=pd.DataFrame({"global":np.repeat(np.arange(9),10)})
    with pytest.raises(ResearchError,match="全局因子按日期计数"):
        AutomaticFoldTransform(columns=list(insufficient),global_columns=["global"],dates=np.repeat(dates[:9],10).tolist(),winsorize=True,standardize=True).fit(insufficient)


@pytest.mark.parametrize("winsorize,standardize",[(False,False),(False,True),(True,False),(True,True)])
def test_optional_fold_controls_and_zero_iqr_are_exactly_archived(winsorize,standardize):
    s=automatic_strategy(); s["preprocess"].update(winsorize=winsorize,standardize=standardize)
    metadata=automatic_metadata(s)
    X=pd.DataFrame({"change1":np.ones(30)*7, "factor:market":np.linspace(-1,2,30)})
    y=pd.DataFrame({"entry":np.zeros(30),"exit":np.linspace(-.02,.02,30)})
    dates=pd.bdate_range("20240101",periods=30).strftime("%Y%m%d").tolist()
    with threadpool_limits(limits=1):
        model=fit(candidates("ridge")[0],X,y,s["preprocess"],automatic_metadata=metadata,training_dates=dates)
    audit={**model.audit,"trainStart":dates[0],"trainEnd":dates[-1],"informationCutoff":"20240301","labelEndMax":"20240220","trainDates":30}
    a=export_function(model,audit,s)
    assert (a["transforms"]["winsorLower"] is not None)==winsorize
    assert (a["transforms"]["scaleMean"] is not None)==standardize
    if standardize:
        assert a["transforms"]["scaleMean"][0]==7
        assert a["transforms"]["scaleScale"][0]==1
    probe=X.iloc[:3].copy(); probe.iloc[1,0]=np.nan
    with threadpool_limits(limits=1):
        expected=model.predict(probe)
    np.testing.assert_allclose(predict_function(a,rows(probe))["normalizedChanges"],expected,rtol=1e-12,atol=1e-14)


def test_global_temporal_series_is_independent_of_asset_suspension_mask():
    s = automatic_strategy(); panel, dates, symbols = panel_fixture()
    baseline,_ = evaluate_factors(panel,dates,symbols,s)
    panel.loc[(dates[45],symbols[0]),["close",CONTEXT]]=np.nan
    actual,_ = evaluate_factors(panel,dates,symbols,s)
    pd.testing.assert_frame_equal(actual["market"],baseline["market"])
    assert np.isnan(actual["price"].loc[dates[61],symbols[0]])


def test_context_limit_counts_independent_sources_not_factor_rows():
    from atlas_quant.context_sources import REGISTRY
    def expression(source): return "ext_ctx_"+source["ts_code"].lower().replace(".","_")+"_close"
    s=automatic_strategy()
    s["factors"]=[{"id":"ctx"+str(i),"expression":expression(source)} for i,source in enumerate(REGISTRY["items"][:17])]
    with pytest.raises(ResearchError) as error: validate(s)
    assert error.value.code=="CONTEXT_SOURCE_LIMIT"
    s["factors"]=s["factors"][:16]
    s["factors"].append({"id":"repeat","expression":"returns("+s["factors"][0]["expression"]+",20)"})
    assert len(validate(s)["factors"])==17


@pytest.mark.parametrize("mutation", ["scope","expression","extra","boolean","version","undeclared_input","flags","direction_boolean"])
def test_hashed_but_inconsistent_v2_metadata_is_rejected(mutation):
    _,a,_ = fitted_fixture(candidates("ridge")[0]); d=a["featureConstruction"]["automatic"]
    if mutation=="scope": d["factors"][0]["scope"]="global"
    elif mutation=="expression": d["factors"][0]["expression"]="close"
    elif mutation=="extra": d["factors"][0]["transform"]["code"]="x"
    elif mutation=="boolean": d["factors"][4]["transform"]["returnLag"]=True
    elif mutation=="version": a["schema"]="atlas-model-function/1"
    elif mutation=="flags": a["featureConstruction"]["preprocess"]["standardize"]=False
    elif mutation=="direction_boolean": d["factors"][1]["direction"]=True
    else: a["inputSchema"][0]["name"]="unregistered_feature"
    a["artifactId"]=function_digest({k:v for k,v in a.items() if k!="artifactId"})
    with pytest.raises(ValueError,match="INVALID_MODEL_FUNCTION"): validate_function(a)


def test_v2_checked_in_golden_preserves_exact_ids_and_predictions():
    path = Path(__file__).resolve().parents[2]/"tests/fixtures/model-function-v2-golden.json"
    fixture=json.loads(path.read_text())
    from atlas_quant.statistical_quant.model_function import edit_function
    for case in fixture["cases"]:
        result=predict_function(case["artifact"],case["rows"])
        np.testing.assert_allclose(result["normalizedChanges"],case["expected"]["normalizedChanges"],rtol=1e-12,atol=1e-14)
        edited=edit_function(case["artifact"],case["edits"])
        assert edited==case["editedArtifact"]
        np.testing.assert_allclose(predict_function(edited,case["rows"])["normalizedChanges"],case["editedExpected"]["normalizedChanges"],rtol=1e-12,atol=1e-14)
