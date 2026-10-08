"""Process-local source checking with synthetic statements and no network."""

from copy import deepcopy
import gc
import json
import weakref

import pandas as pd
import pytest

from atlas_quant.engine import ResearchError, _prepare_data
from atlas_quant.fixtures import make_demo_data
from atlas_quant.provider import ProviderError, validate_upload
from atlas_quant.financial_statements import RECIPES
from atlas_quant.financial_statements.dataset import compose_financial_dataset
from atlas_quant.financial_statements.package import freeze_package
from atlas_quant.financial_statements.prepare import _safe_rows
from atlas_quant.statistical_quant.schema import validate
from atlas_quant.statistical_quant.targets import build_samples
from test_financial_adapter import run, UNITS
from test_financial_statements import calendar, SYMBOL


@pytest.fixture(scope="module")
def inputs():
    strategy = {
        "schemaVersion": 2,
        "name": "Synthetic financial admission",
        "universe": {"symbols": [SYMBOL], "start": "20240101", "end": "20241231"},
        "research": {"mode": "statistical_quant"},
        "target": {"kind": "asset_price", "horizonSessions": 5},
        "model": {"family": "fundamental", "estimator": "ridge"},
        "execution": {"enabled": False},
        "factors": [
            {"id": name, "expression": name, "role": "predictor"} for name in RECIPES
        ],
    }
    source = run(strategy=strategy)
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
    data, provenance = make_demo_data(strategy)
    market = {"schemaVersion": 1, "rows": _safe_rows(data), "provenance": provenance}
    return validate(strategy), market, package


def compose(inputs, package=None):
    strategy, market, original = inputs
    return compose_financial_dataset(
        strategy, market, [package or original], trusted_unit_proofs=True
    )


def assert_rejected(data, provenance, strategy):
    with pytest.raises(ResearchError, match="重新 compose") as error:
        _prepare_data(data, strategy, provenance)
    assert error.value.code == "FINANCIAL_RECOMPOSITION_REQUIRED"


def test_all_sixteen_registered_states_enter_fundamental_with_real_missing_masks(
    inputs,
):
    result = compose(inputs)
    panel, dates, audit = _prepare_data(
        result.data, inputs[0], deepcopy(result.provenance)
    )
    samples = build_samples(panel, dates, inputs[0])
    assert len([key for key in samples.X if key.startswith("factor:")]) == 16
    assert samples.meta.inputValid.any()
    # The fixture includes prior filings, so first-quarter inputs can legitimately
    # exist. Verify the target builder's missing-input gate separately, without
    # inventing a claim that those prior filings were absent.
    missing = panel.copy()
    missing[list(RECIPES)] = float("nan")
    unavailable = build_samples(missing, dates, inputs[0])
    assert not unavailable.meta.inputValid.any()
    assert set(unavailable.meta.invalidReason) == {"no_observed_fundamental_predictor"}
    assert (
        audit["financialSourceCommitment"]["financialDatasetRoot"]
        == result.provenance["financialDatasetRoot"]
    )
    assert all(
        item["semanticKind"] == "native_statement_state"
        for item in audit["externalFieldAudit"]
    )
    assert all(
        "independentTrainingHistoryVerified" not in item
        for item in audit["externalFieldAudit"]
    )


@pytest.mark.parametrize("operation", ["copy", "deepcopy", "json"])
def test_copy_and_serialized_roundtrip_never_inherit_source_check(inputs, operation):
    result = compose(inputs)
    if operation == "json":
        payload = json.loads(json.dumps(result.to_dataset(), allow_nan=False))
        data, provenance = pd.DataFrame(payload["rows"]), payload["provenance"]
    else:
        data = result.data.copy() if operation == "copy" else deepcopy(result.data)
        provenance = result.provenance
    assert_rejected(data, provenance, inputs[0])


def test_legal_name_and_claimed_semantic_kind_are_not_admission(inputs):
    result = compose(inputs)
    data = pd.DataFrame(result.to_dataset()["rows"])
    provenance = deepcopy(result.provenance)
    provenance["externalFields"][next(iter(RECIPES))]["unitVerified"] = True
    provenance["externalFields"][next(iter(RECIPES))][
        "semanticKind"
    ] = "native_statement_state"
    assert_rejected(data, provenance, inputs[0])


@pytest.mark.parametrize(
    "mutation", ["value", "price", "provenance", "field", "available_date"]
)
def test_any_data_provenance_or_field_change_requires_recomposition(inputs, mutation):
    result = compose(inputs)
    field = "model_fin_cash_asset_share"
    row = result.data[field].first_valid_index()
    if mutation == "value":
        result.data.loc[row, field] += 0.1
    elif mutation == "price":
        result.data.loc[row, "close"] += 0.01
    elif mutation == "available_date":
        result.data.loc[row, field + "__available_date"] = "20240101"
    elif mutation == "field":
        result.data.rename(columns={field: "model_fin_fake"}, inplace=True)
    else:
        result.provenance["financialInputs"][0]["unitPolicy"] = "allow_declared"
    assert_rejected(result.data, result.provenance, inputs[0])


@pytest.mark.parametrize("where", ["rows", "registry", "root", "strategy"])
def test_generic_upload_rejects_reserved_namespace_even_without_factor_use(
    inputs, where
):
    strategy = deepcopy(inputs[0])
    strategy["model"]["family"] = "trend"
    strategy["factors"] = []
    payload = deepcopy(inputs[1])
    if where == "rows":
        payload["rows"][0]["model_fin_cash_asset_share"] = 1
    elif where == "registry":
        payload["provenance"]["externalFields"] = {
            "model_fin_cash_asset_share": {"semanticKind": "native_statement_state"}
        }
    elif where == "root":
        payload["provenance"]["preparedRoot"] = "0" * 64
    else:
        strategy = inputs[0]
    with pytest.raises(ProviderError) as error:
        validate_upload(strategy, payload)
    assert error.value.code == "FINANCIAL_RECOMPOSITION_REQUIRED"


def test_unregistered_financial_names_and_ordinary_model_forecasts_do_not_qualify(
    inputs,
):
    for field, code in [
        ("model_fin_fake", "UNREGISTERED_FINANCIAL_STATE"),
        ("model_external_forecast", "MISSING_MODEL_DATA"),
    ]:
        strategy = deepcopy(inputs[0])
        strategy["factors"] = [{"id": "test", "expression": field}]
        with pytest.raises(ResearchError) as error:
            validate(strategy)
        assert error.value.code == code


def test_serializable_roots_not_process_identity_bind_forecast_fingerprint(inputs):
    first, second = compose(inputs), compose(inputs)
    left = _prepare_data(first.data, inputs[0], first.provenance)[2]
    right = _prepare_data(second.data, inputs[0], second.provenance)[2]
    assert left == right
    # Revalidating identical values under a different explicit policy creates a
    # new package/closure root; the forecast's data identity must also change.
    from atlas_quant.financial_statements.results import canonical_hash

    package = deepcopy(inputs[2])
    package["unitPolicy"] = "allow_declared"
    package["packRoot"] = canonical_hash(
        {k: v for k, v in package.items() if k != "packRoot"}
    )
    other = compose(inputs, package)
    pd.testing.assert_frame_equal(first.data, other.data)
    changed = _prepare_data(other.data, inputs[0], other.provenance)[2]
    assert changed["dataSha256"] != left["dataSha256"]
    assert changed["calendarSha256"] == left["calendarSha256"]
    assert "reference" not in json.dumps(left)


def test_weak_admission_does_not_retain_frames_or_affect_other_runs(inputs):
    from atlas_quant.financial_statements.admission import _REGISTRY

    other = compose(inputs)
    result = compose(inputs)
    key, reference = id(result.data), weakref.ref(result.data)
    assert key in _REGISTRY
    del result
    gc.collect()
    assert reference() is None and key not in _REGISTRY
    _prepare_data(other.data, inputs[0], other.provenance)


def test_ordinary_external_fields_keep_existing_pit_upload_path(inputs):
    payload = deepcopy(inputs[1])
    strategy = deepcopy(inputs[0])
    strategy["factors"] = [{"id": "ordinary", "expression": "pcd_custom"}]
    for row in payload["rows"]:
        row.update(pcd_custom=0.5, pcd_custom__available_date=row["trade_date"])
    payload["provenance"]["externalFields"] = {
        "pcd_custom": {
            "source": "SYNTHETIC",
            "path": "fixture/custom",
            "dataType": "number",
            "availabilityPolicy": "point_in_time_asof",
            "availableDateColumn": "pcd_custom__available_date",
        }
    }
    data, provenance = validate_upload(strategy, payload)
    panel, _, audit = _prepare_data(data, validate(strategy), provenance)
    assert panel.pcd_custom.eq(0.5).all()
    assert "financialSourceCommitment" not in audit


def test_live_composed_object_cannot_authorize_financial_execution_replay(
    inputs, monkeypatch
):
    from atlas_quant.statistical_quant import execute_forecasts
    import atlas_quant.engine as engine

    result = compose(inputs)

    def forbidden(*args, **kwargs):
        pytest.fail("No data or execution work before financial replay is supported")

    monkeypatch.setattr(engine, "_prepare_data", forbidden)
    with pytest.raises(ResearchError) as error:
        execute_forecasts(inputs[0], result.data, {}, result.provenance)
    assert error.value.code == "FINANCIAL_REPLAY_NOT_AVAILABLE"


@pytest.mark.parametrize("execution", [True, None])
def test_financial_initial_execution_requires_explicit_forecast_only(inputs, execution):
    strategy = deepcopy(inputs[0])
    if execution is None:
        del strategy["execution"]
    else:
        strategy["execution"]["enabled"] = execution
    with pytest.raises(ResearchError) as error:
        validate(strategy)
    assert error.value.code == "FINANCIAL_FORECAST_ONLY_REQUIRED"


@pytest.mark.parametrize("mode", ["legacy", "stat_arb", "execute", "no_selected_state"])
def test_composed_dataset_itself_blocks_non_forecast_modes(inputs, mode):
    result = compose(inputs)
    strategy = deepcopy(inputs[0])
    if mode in ("legacy", "stat_arb"):
        strategy["schemaVersion"] = 1
        strategy["research"]["mode"] = mode
    else:
        strategy["execution"]["enabled"] = True
        if mode == "no_selected_state":
            strategy["model"]["family"] = "trend"
            strategy["factors"] = []
    with pytest.raises(ResearchError) as error:
        _prepare_data(result.data, strategy, result.provenance)
    assert error.value.code == "FINANCIAL_FORECAST_ONLY_REQUIRED"
