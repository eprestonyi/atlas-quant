"""Independent financial admission counterexamples; no provider or model fits."""

from copy import deepcopy

import pandas as pd
import pytest

from atlas_quant.engine import ResearchError, _prepare_data
from atlas_quant.financial_statements import RECIPES
from atlas_quant.financial_statements.dataset import compose_financial_dataset
from atlas_quant.financial_statements.package import freeze_package
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.targets import build_samples
from test_financial_admission import inputs, compose
from test_financial_adapter import FakeProvider, UNITS, run
from test_financial_statements import calendar


@pytest.mark.parametrize("execution", [{"enabled": True}, None])
def test_registered_financial_schema_requires_explicit_forecast_only(inputs, execution):
    strategy = deepcopy(inputs[0])
    if execution is None:
        strategy.pop("execution")
    else:
        strategy["execution"] = execution
    with pytest.raises(ResearchError):
        validate(strategy)


@pytest.mark.parametrize("mode", ["legacy_long_only", "stat_arb"])
def test_live_financial_object_cannot_enter_legacy_engines(inputs, mode):
    result = compose(inputs)
    strategy = deepcopy(inputs[0])
    strategy["schemaVersion"] = 1
    strategy["research"]["mode"] = mode
    with pytest.raises(ResearchError):
        _prepare_data(result.data, strategy, result.provenance)


def test_dropping_financial_predictors_does_not_enable_execution(inputs):
    result = compose(inputs)
    strategy = deepcopy(inputs[0])
    strategy["model"]["family"] = "trend"
    strategy["factors"] = []
    strategy["execution"]["enabled"] = True
    with pytest.raises(ResearchError):
        _prepare_data(result.data, strategy, result.provenance)


@pytest.mark.parametrize(
    "mutation", ["calendar", "source", "row_index", "column_order"]
)
def test_exact_composed_admission_rejects_non_value_mutations(inputs, mutation):
    result = compose(inputs)
    if mutation == "calendar":
        result.provenance["tradingDates"].reverse()
    elif mutation == "source":
        result.provenance["marketSource"] = "self-certified-replacement"
    elif mutation == "row_index":
        result.data.index = result.data.index + 1
    else:
        # Mutate the original object, preserving values but replacing order.
        field = result.data.pop(result.data.columns[0])
        result.data[field.name] = field
    with pytest.raises(ResearchError) as error:
        _prepare_data(result.data, inputs[0], result.provenance)
    assert error.value.code == "FINANCIAL_RECOMPOSITION_REQUIRED"


def test_admission_is_not_bound_to_provenance_dictionary_object_identity(inputs):
    result = compose(inputs)
    original = _prepare_data(result.data, inputs[0], result.provenance)
    copied = _prepare_data(result.data, inputs[0], deepcopy(result.provenance))
    pd.testing.assert_frame_equal(original[0], copied[0], check_exact=True)
    assert original[1:] == copied[1:]


def test_future_statement_value_perturbation_preserves_earlier_features(inputs):
    strategy, market, _ = inputs
    before = compose(inputs)

    def revise_future(endpoint, params, frame):
        if endpoint == "balancesheet":
            frame = frame.copy()
            selected = frame.end_date.eq("20231231")
            frame.loc[selected, "total_assets"] *= 2
        return frame

    source = run(client=FakeProvider(transform=revise_future), strategy=strategy)
    package = freeze_package(
        source.snapshots,
        calendar(),
        strategy,
        list(RECIPES),
        UNITS,
        announcement_start="20210101",
        source_kind="fixture",
        source_provider="HAND_FAKE_PROVIDER",
        trusted_unit_proofs=True,
    )
    after = compose_financial_dataset(
        strategy, market, [package], trusted_unit_proofs=True
    )
    cutoff = "20240429"
    old_panel, old_dates, old_audit = _prepare_data(
        before.data, strategy, before.provenance
    )
    new_panel, new_dates, new_audit = _prepare_data(
        after.data, strategy, after.provenance
    )
    old = build_samples(old_panel, old_dates, strategy)
    new = build_samples(new_panel, new_dates, strategy)
    prior = old.meta.date.lt(cutoff)
    assert prior.any()
    pd.testing.assert_frame_equal(old.X.loc[prior], new.X.loc[prior], check_exact=True)
    pd.testing.assert_frame_equal(old.y.loc[prior], new.y.loc[prior], check_exact=True)
    cash = "model_fin_cash_asset_share"
    observed = before.data.trade_date.ge(cutoff) & before.data[cash].notna()
    assert observed.any()
    assert (before.data.loc[observed, cash] != after.data.loc[observed, cash]).all()
    assert old_audit["dataSha256"] != new_audit["dataSha256"]


def test_json_sidecar_and_live_dataframe_cannot_be_swapped_between_sources(inputs):
    from atlas_quant.financial_statements.results import canonical_hash

    first = compose(inputs)
    changed_package = deepcopy(inputs[2])
    changed_package["unitPolicy"] = "allow_declared"
    changed_package["packRoot"] = canonical_hash(
        {k: v for k, v in changed_package.items() if k != "packRoot"}
    )
    second = compose(inputs, changed_package)
    pd.testing.assert_frame_equal(first.data, second.data, check_exact=True)
    with pytest.raises(ResearchError) as error:
        _prepare_data(first.data, inputs[0], second.provenance)
    assert error.value.code == "FINANCIAL_RECOMPOSITION_REQUIRED"
