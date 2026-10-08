"""Pure tiny-price contract tests. No model, OLS, provider or HTTP is invoked."""
from copy import deepcopy
from datetime import date, timedelta
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from atlas_quant.pair_research import (
    PairContractError, build_targets, declare_targets, label_is_mature,
    prepare_price_input, validate_declaration, validate_price_input,
)
from atlas_quant.pair_research.contract import ADJUSTMENT, LIMITS, digest


A, B, C, D, E = [f"{i:06d}.SZ" for i in range(1, 6)]
DATES = ["20240102", "20240103", "20240104", "20240105", "20240108", "20240109", "20240110"]


def domain():
    return {
        "ownerKey": "test-owner",
        "marketDatasetRef": {"datasetId": "00000000-0000-4000-8000-000000000001",
                             "datasetRoot": "a" * 64, "format": "atlas.quant.market_dataset", "version": 1},
        "universeScopeRef": {"scopeId": "00000000-0000-4000-8000-000000000002",
                             "scopeRoot": "b" * 64, "format": "atlas.quant.universe_scope", "version": 1},
        "symbols": [A, B, C, D, E], "start": DATES[0], "end": DATES[-1], "calendar": list(DATES),
        "membershipPolicy": "complete_filtered_set", "currency": "CNY",
        "priceUnit": "CNY_per_adjusted_share", "quantityUnit": "adjusted_share", "adjustment": ADJUSTMENT,
    }


def raw_rows():
    rows = [{"symbol": symbol, "date": date, "open": 30. + 5 * s + t,
             "close": 31. + 5 * s + t} for t, date in enumerate(DATES) for s, symbol in enumerate([A, B, C, D, E])]
    edits = {
        (DATES[1], A): {"close": 100}, (DATES[1], B): {"close": 40},
        (DATES[2], A): {"open": 102, "close": 100}, (DATES[2], B): {"open": 41, "close": 50},
        (DATES[3], A): {"open": 108}, (DATES[3], B): {"open": 42},
    }
    for row in rows:
        row.update(edits.get((row["date"], row["symbol"]), {}))
    return rows


def pair_map():
    return [
        {"pairId": "AB", "legs": [{"symbol": A, "quantity": 1}, {"symbol": B, "quantity": -2}]},
        {"pairId": "BC", "legs": [{"symbol": B, "quantity": 1}, {"symbol": C, "quantity": -1}]},
    ]


def inputs(*, rows=None, pairs=None, source_domain=None, origins=None, horizon=1):
    source = prepare_price_input(domain() if source_domain is None else source_domain,
                                 raw_rows() if rows is None else rows)
    spec = declare_targets(source, pair_map() if pairs is None else pairs, quantity_cutoff=DATES[0],
                           origins=[DATES[1], DATES[2], DATES[-2], DATES[-1]] if origins is None else origins,
                           horizon_sessions=horizon)
    return source, spec


def test_hand_calculation_fixed_quantities_and_full_u_t_grid():
    source, spec = inputs()
    result = build_targets(spec, source)
    row = result["rows"][0]
    assert row["current"]["state"] == 20
    assert row["current"]["grossScale"] == 180
    assert row["entry"]["date"] == DATES[2]
    assert row["entry"]["state"] == 20
    assert row["entry"]["normalizedChange"] == 0
    assert row["exit"]["date"] == DATES[3]
    assert row["exit"]["state"] == 24
    assert row["exit"]["normalizedChange"] == pytest.approx(4 / 180)
    # Neither spread-normalized return (4/20) nor entry-gross normalization.
    assert row["exit"]["normalizedChange"] not in (4 / 20, 4 / 184)
    assert result["coverage"] == {"universeMembers": 5, "targetPairs": 2, "origins": 4,
                                   "expectedTargetRows": 8, "actualTargetRows": 8}
    assert len({(r["pairId"], r["origin"]) for r in result["rows"]}) == 8
    assert result["memberStates"] == [
        {"symbol": A, "status": "targeted", "pairIds": ["AB"], "reason": None},
        {"symbol": B, "status": "targeted", "pairIds": ["AB", "BC"], "reason": None},
        {"symbol": C, "status": "targeted", "pairIds": ["BC"], "reason": None},
        {"symbol": D, "status": "unmatched", "pairIds": [], "reason": "not_in_explicit_map"},
        {"symbol": E, "status": "unmatched", "pairIds": [], "reason": "not_in_explicit_map"},
    ]
    assert result["resultRoot"] == digest({k: v for k, v in result.items() if k != "resultRoot"})
    assert spec["sourceDomain"]["symbols"] == [A, B, C, D, E]


def test_zero_and_negative_spread_are_valid():
    source, spec = inputs()
    zero = build_targets(spec, source)["rows"][2]
    assert zero["current"]["state"] == 0
    assert zero["current"]["grossScale"] == 200
    assert zero["current"]["status"] == zero["exit"]["labelStatus"] == "complete"
    pairs = pair_map()
    for leg in pairs[0]["legs"]:
        leg["quantity"] *= -1
    source, spec = inputs(pairs=pairs)
    negative = build_targets(spec, source)["rows"][0]
    assert negative["current"]["state"] == -20
    assert negative["current"]["grossScale"] == 180
    assert negative["exit"]["normalizedChange"] == pytest.approx(-4 / 180)


def test_direction_and_quantity_units_are_preserved_not_silently_normalized():
    source, original = inputs()
    first = build_targets(original, source)
    pairs = pair_map()
    for leg in pairs[0]["legs"]:
        leg["quantity"] *= 3
    _, scaled = inputs(pairs=pairs)
    second = build_targets(scaled, source)
    assert second["rows"][0]["current"]["state"] == 60
    assert second["rows"][0]["current"]["grossScale"] == 540
    assert second["rows"][0]["exit"]["normalizedChange"] == first["rows"][0]["exit"]["normalizedChange"]
    assert second["targets"][0]["targetVersionId"] != first["targets"][0]["targetVersionId"]
    assert second["targets"][0]["legs"] == pairs[0]["legs"]
    pairs[0]["legs"].reverse()
    _, reversed_spec = inputs(pairs=pairs)
    third = build_targets(reversed_spec, source)
    assert third["rows"][0]["current"] == second["rows"][0]["current"]
    assert third["targets"][0]["targetVersionId"] != second["targets"][0]["targetVersionId"]


def test_legacy_exit_is_next_plus_h_not_origin_plus_h():
    source, spec = inputs(origins=[DATES[1]], horizon=3)
    row = build_targets(spec, source)["rows"][0]
    assert row["entry"]["date"] == DATES[2]
    assert row["exit"]["date"] == DATES[5]
    assert row["exit"]["date"] != DATES[4]


def test_cutoff_excludes_label_on_same_day_and_all_tail_rows_survive():
    source, spec = inputs()
    rows = build_targets(spec, source)["rows"]
    assert not label_is_mature(rows[0], "entry", DATES[2])
    assert label_is_mature(rows[0], "entry", DATES[3])
    assert not label_is_mature(rows[0], "exit", DATES[3])
    assert label_is_mature(rows[0], "exit", DATES[4])
    assert rows[4]["entry"]["labelStatus"] == "complete"
    assert rows[4]["exit"]["status"] == "outside_calendar"
    assert rows[6]["entry"]["status"] == rows[6]["exit"]["status"] == "outside_calendar"
    assert not label_is_mature(rows[6], "exit", "20250101")


@pytest.mark.parametrize("stage,field,date", [("current", "close", DATES[1]), ("entry", "open", DATES[2]), ("exit", "open", DATES[3])])
@pytest.mark.parametrize("absent_row", [False, True])
def test_missing_one_leg_preserves_targets_and_never_values_half_pair(stage, field, date, absent_row):
    rows = raw_rows()
    if absent_row:
        rows = [r for r in rows if not (r["symbol"] == B and r["date"] == date)]
    else:
        next(r for r in rows if r["symbol"] == B and r["date"] == date)[field] = None
    source, spec = inputs(rows=rows)
    result = build_targets(spec, source)
    row = result["rows"][0]
    assert len(result["rows"]) == 8
    assert row[stage]["state"] is None
    assert row[stage]["status"] == "missing_legs"
    assert row[stage]["missingLegs"] == [B]
    assert row["entry"]["date"] == DATES[2] and row["exit"]["date"] == DATES[3]
    if stage == "current":
        assert row["current"]["grossScale"] is None
        assert row["entry"]["normalizedChange"] is None
        assert row["exit"]["labelStatus"] == "current_unavailable"
    else:
        assert row["current"]["state"] == 20
        assert row[stage]["normalizedChange"] is None
        assert row[stage]["labelStatus"] == "valuation_unavailable"


def test_absent_unmatched_members_remain_visible_and_do_not_change_target_values():
    source, spec = inputs()
    full = build_targets(spec, source)
    source, spec = inputs(rows=[r for r in raw_rows() if r["symbol"] not in (D, E)])
    missing = build_targets(spec, source)
    assert full["rows"] == missing["rows"]
    assert full["memberStates"] == missing["memberStates"]
    assert missing["coverage"]["universeMembers"] == 5


def test_future_perturbation_cannot_reestimate_q_or_origin_state():
    source, spec = inputs(origins=[DATES[1]])
    before = build_targets(spec, source)
    perturbed = raw_rows()
    for row in perturbed:
        if row["date"] > DATES[1] and row["symbol"] == A:
            row["open"] *= 17
            row["close"] *= 13
    future_source, future_spec = inputs(rows=perturbed, origins=[DATES[1]])
    after = build_targets(future_spec, future_source)
    # A counterfactual price projection must receive a different declaration.
    assert spec["priceProjectionRoot"] != future_spec["priceProjectionRoot"]
    assert spec["declarationRoot"] != future_spec["declarationRoot"]
    assert before["targets"] == after["targets"]
    assert before["rows"][0]["current"] == after["rows"][0]["current"]
    assert before["rows"][0]["entry"] != after["rows"][0]["entry"]
    assert before["rows"][0]["exit"] != after["rows"][0]["exit"]
    with pytest.raises(PairContractError, match="identity differ"):
        build_targets(spec, future_source)


def test_empty_t_and_single_odd_u_are_valid_without_fallback_targets():
    one = domain()
    one["symbols"] = [A]
    source, spec = inputs(rows=[], pairs=[], source_domain=one)
    result = build_targets(spec, source)
    assert result["status"] == "empty_target_scope"
    assert result["rows"] == result["targets"] == []
    assert result["memberStates"] == [{"symbol": A, "status": "unmatched", "pairIds": [], "reason": "not_in_explicit_map"}]


@pytest.mark.parametrize("change", ["self", "outside", "three_legs", "duplicate_id", "reverse_duplicate", "same_sign", "zero", "bool", "nan", "inf", "huge", "zero_after_float", "leg_extra", "pair_extra"])
def test_invalid_pair_map_is_rejected_whole(change):
    pairs = pair_map()
    if change == "self": pairs[0]["legs"][1]["symbol"] = A
    elif change == "outside": pairs[0]["legs"][1]["symbol"] = "999999.SH"
    elif change == "three_legs": pairs[0]["legs"].append({"symbol": C, "quantity": -1})
    elif change == "duplicate_id": pairs[1]["pairId"] = "AB"
    elif change == "reverse_duplicate": pairs.append({"pairId": "BA", "legs": [{"symbol": B, "quantity": 4}, {"symbol": A, "quantity": -2}]})
    elif change == "same_sign": pairs[0]["legs"][1]["quantity"] = 2
    elif change == "leg_extra": pairs[0]["legs"][0]["dollarWeight"] = .5
    elif change == "pair_extra": pairs[0]["formation"] = "OLS"
    else:
        pairs[0]["legs"][0]["quantity"] = {"zero": 0, "bool": True, "nan": math.nan, "inf": math.inf,
                                            "huge": 1000001, "zero_after_float": 0.0}[change]
    with pytest.raises(PairContractError): inputs(pairs=pairs)


@pytest.mark.parametrize("quantity", [10 ** 1000, "1", None, {}, []])
def test_non_json_or_unbounded_quantities_raise_contract_error(quantity):
    pairs = pair_map()
    pairs[0]["legs"][0]["quantity"] = quantity
    with pytest.raises(PairContractError) as error: inputs(pairs=pairs)
    assert error.value.code == "PAIR_QUANTITY"


@pytest.mark.parametrize("value", [0, -1, True, math.nan, math.inf, 10 ** 1000, "100"])
def test_prices_have_strict_finite_units(value):
    rows = raw_rows()
    rows[0]["close"] = value
    with pytest.raises(PairContractError) as error: inputs(rows=rows)
    assert error.value.code == "PAIR_PRICE"


@pytest.mark.parametrize("change", ["duplicate", "outside_symbol", "outside_date", "extra", "missing_field"])
def test_price_projection_rejects_duplicate_or_out_of_domain_rows(change):
    rows = raw_rows()
    if change == "duplicate": rows.append(deepcopy(rows[0]))
    elif change == "outside_symbol": rows[0]["symbol"] = "999999.SH"
    elif change == "outside_date": rows[0]["date"] = "20240106"
    elif change == "extra": rows[0]["raw_close"] = 25
    else: del rows[0]["open"]
    with pytest.raises(PairContractError): inputs(rows=rows)


@pytest.mark.parametrize("change", ["scope_extra", "dataset_extra", "version_bool", "version_two", "format", "bad_uuid", "uppercase_root", "root_length", "duplicate_u", "empty_u", "symbol_type", "range", "invalid_date", "calendar_order", "calendar_duplicate", "calendar_outside", "raw_basis", "raw_share", "currency", "price_unit", "domain_extra"])
def test_source_domain_closed_schema_and_unit_rejections(change):
    d = domain()
    if change == "scope_extra": d["universeScopeRef"]["extra"] = 1
    elif change == "dataset_extra": d["marketDatasetRef"]["extra"] = 1
    elif change == "version_bool": d["universeScopeRef"]["version"] = True
    elif change == "version_two": d["marketDatasetRef"]["version"] = 2
    elif change == "format": d["marketDatasetRef"]["format"] = "other"
    elif change == "bad_uuid": d["marketDatasetRef"]["datasetId"] = "not-uuid"
    elif change == "uppercase_root": d["universeScopeRef"]["scopeRoot"] = "B" * 64
    elif change == "root_length": d["marketDatasetRef"]["datasetRoot"] = "a" * 63
    elif change == "duplicate_u": d["symbols"].append(A)
    elif change == "empty_u": d["symbols"] = []
    elif change == "symbol_type": d["symbols"][0] = []
    elif change == "range": d["end"] = "20260101"
    elif change == "invalid_date": d["end"] = "20240230"
    elif change == "calendar_order": d["calendar"].reverse()
    elif change == "calendar_duplicate": d["calendar"].append(DATES[-1])
    elif change == "calendar_outside": d["calendar"].append("20240111")
    elif change == "raw_basis": d["adjustment"] = "raw"
    elif change == "raw_share": d["quantityUnit"] = "raw_share"
    elif change == "currency": d["currency"] = "USD"
    elif change == "price_unit": d["priceUnit"] = "CNY"
    else: d["extra"] = "ignored?"
    with pytest.raises(PairContractError): inputs(source_domain=d)


@pytest.mark.parametrize("change", ["owner", "scope_root", "scope_id", "dataset_root", "dataset_id", "u_order", "calendar"])
def test_other_valid_source_domain_cannot_be_bound_to_saved_targets(change):
    source, spec = inputs()
    d = domain()
    if change == "owner": d["ownerKey"] = "another-owner"
    elif change == "scope_root": d["universeScopeRef"]["scopeRoot"] = "c" * 64
    elif change == "scope_id": d["universeScopeRef"]["scopeId"] = d["marketDatasetRef"]["datasetId"]
    elif change == "dataset_root": d["marketDatasetRef"]["datasetRoot"] = "d" * 64
    elif change == "dataset_id": d["marketDatasetRef"]["datasetId"] = d["universeScopeRef"]["scopeId"]
    elif change == "u_order": d["symbols"].reverse()
    else: d["calendar"].remove(DATES[0])
    rows = [r for r in raw_rows() if r["date"] in d["calendar"]]
    changed = prepare_price_input(d, rows)
    with pytest.raises(PairContractError) as error: build_targets(spec, changed)
    assert error.value.code == "PAIR_SOURCE_BINDING"


@pytest.mark.parametrize("change", ["on_cutoff", "before_cutoff", "unknown_origin", "duplicate_origin", "reverse_origins", "empty_origins", "float_horizon", "bool_horizon", "zero_horizon", "large_horizon", "outside_cutoff"])
def test_invalid_time_declarations_never_reinterpret_calendar(change):
    source, _ = inputs()
    kwargs = {"quantity_cutoff": DATES[0], "origins": [DATES[1], DATES[2]], "horizon_sessions": 1}
    if change == "on_cutoff": kwargs["origins"] = [DATES[0]]
    elif change == "before_cutoff": kwargs["quantity_cutoff"] = DATES[3]
    elif change == "unknown_origin": kwargs["origins"] = ["20240106"]
    elif change == "duplicate_origin": kwargs["origins"] = [DATES[1], DATES[1]]
    elif change == "reverse_origins": kwargs["origins"].reverse()
    elif change == "empty_origins": kwargs["origins"] = []
    elif change == "outside_cutoff": kwargs["quantity_cutoff"] = "20231229"
    else: kwargs["horizon_sessions"] = {"float_horizon": 1.0, "bool_horizon": True, "zero_horizon": 0, "large_horizon": 61}[change]
    with pytest.raises(PairContractError): declare_targets(source, pair_map(), **kwargs)


@pytest.mark.parametrize("change", ["omit_member", "false_unmatched", "omit_shared_incidence", "add_member", "ledger_extra", "scope_extra", "declaration_extra", "version_bool", "quantity", "quantity_cutoff", "origin", "timing"])
def test_declaration_tampering_and_even_rehashed_false_ledgers_rejected(change):
    _, spec = inputs()
    if change == "omit_member": spec["memberStates"].pop()
    elif change == "false_unmatched": spec["memberStates"][0].update(status="unmatched", pairIds=[], reason="not_in_explicit_map")
    elif change == "omit_shared_incidence": spec["memberStates"][1]["pairIds"].pop()
    elif change == "add_member": spec["memberStates"].append(deepcopy(spec["memberStates"][-1]))
    elif change == "ledger_extra": spec["memberStates"][0]["extra"] = 1
    elif change == "scope_extra": spec["targetScope"]["graph"] = []
    elif change == "declaration_extra": spec["F"] = "OLS"
    elif change == "version_bool": spec["version"] = True
    elif change == "quantity": spec["targetScope"]["pairMap"][0]["legs"][1]["quantity"] = -3
    elif change == "quantity_cutoff": spec["targetScope"]["quantityCutoff"] = DATES[1]
    elif change == "origin": spec["origins"] = [DATES[2]]
    else: spec["targetTiming"] = "origin_plus_h"
    if change in ("omit_member", "false_unmatched", "omit_shared_incidence", "add_member"):
        spec["declarationRoot"] = digest({k: v for k, v in spec.items() if k != "declarationRoot"})
    with pytest.raises(PairContractError): validate_declaration(spec)


def test_reordered_keys_rows_and_integral_floats_are_identity_stable_without_mutation():
    source, spec = inputs()
    original = deepcopy((source, spec))
    reversed_keys = json.loads(json.dumps(spec, sort_keys=True))
    assert validate_declaration(reversed_keys) == spec
    shuffled = deepcopy(source)
    shuffled["rows"].reverse()
    assert validate_price_input(shuffled) == source
    mapped = pair_map()
    for pair in mapped:
        for leg in pair["legs"]:
            leg["quantity"] = float(leg["quantity"])
    _, as_floats = inputs(pairs=mapped)
    assert as_floats == spec
    first = build_targets(spec, source)
    second = build_targets(spec, source)
    assert first == second
    assert (source, spec) == original
    first["memberStates"][0]["pairIds"].clear()
    first["targets"][0]["legs"][0]["quantity"] = 99
    assert (source, spec) == original


def test_projection_tamper_cannot_be_hidden_by_rehashing_input_only():
    source, spec = inputs()
    source["rows"][0]["close"] += 1
    with pytest.raises(PairContractError): validate_price_input(source)
    rehashed = prepare_price_input(source["sourceDomain"], source["rows"])
    with pytest.raises(PairContractError): build_targets(spec, rehashed)


@pytest.mark.parametrize("magnitude", [1e308, 5e-324])
def test_float_overflow_or_underflow_stays_explicitly_unavailable(magnitude):
    rows = raw_rows()
    for row in rows:
        row["open"] = row["close"] = magnitude
    pairs = pair_map()
    pairs[0]["legs"][0]["quantity"] = 1e6 if magnitude > 1 else 5e-324
    source, spec = inputs(rows=rows, pairs=pairs)
    result = build_targets(spec, source)
    assert result["rows"][0]["current"]["status"] == "arithmetic_unavailable"
    assert result["rows"][0]["current"]["state"] is None
    assert result["rows"][0]["exit"]["normalizedChange"] is None
    json.dumps(result, allow_nan=False)


def test_shape_bounds_never_truncate_u_t_and_do_not_register_a_profile():
    d = domain()
    d["symbols"] = [f"{i:06d}.SZ" for i in range(1000)]
    source, spec = inputs(rows=[], pairs=[], source_domain=d)
    assert len(spec["memberStates"]) == 1000  # Ledger shape only, no capacity or F test.
    d["symbols"].append("001000.SZ")
    with pytest.raises(PairContractError): prepare_price_input(d, [])
    source, _ = inputs()
    with pytest.raises(PairContractError) as error:
        declare_targets(source, pair_map() * 501, quantity_cutoff=DATES[0], origins=[DATES[1]], horizon_sessions=1)
    assert error.value.code == "PAIR_BUDGET"


def test_output_product_and_input_row_bounds_reject_before_partial_output():
    d = domain()
    d["symbols"] = [f"{i:06d}.SZ" for i in range(1000)]
    d["calendar"] = [(date(2024, 1, 1) + timedelta(days=i)).strftime("%Y%m%d") for i in range(366)]
    d["start"], d["end"] = d["calendar"][0], d["calendar"][-1]
    source = prepare_price_input(d, [])
    pairs = [{"pairId": f"p{i}", "legs": [{"symbol": d["symbols"][0], "quantity": 1},
              {"symbol": symbol, "quantity": -1}]} for i, symbol in enumerate(d["symbols"][1:])]
    # Every pair is distinct and all individual shape bounds fit, but the full
    # Cartesian output would exceed 300000. Reject instead of dropping a tail.
    with pytest.raises(PairContractError) as error:
        declare_targets(source, pairs, quantity_cutoff=d["calendar"][0],
                        origins=d["calendar"][1:], horizon_sessions=1)
    assert error.value.code == "PAIR_BUDGET"
    with pytest.raises(PairContractError) as error:
        prepare_price_input(domain(), [raw_rows()[0]] * 300001)
    assert error.value.code == "PAIR_BUDGET"


def test_future_gross_is_not_used_to_normalize_or_discard_valid_labels():
    pairs = [{"pairId": "AB", "legs": [{"symbol": A, "quantity": 1}, {"symbol": B, "quantity": -1}]}]
    rows = raw_rows()
    for row in rows:
        if row["date"] > DATES[1] and row["symbol"] in (A, B):
            row["open"] = 1e308
    source, spec = inputs(rows=rows, pairs=pairs, origins=[DATES[1]])
    row = build_targets(spec, source)["rows"][0]
    assert row["current"]["state"] == 60 and row["current"]["grossScale"] == 140
    assert row["entry"]["state"] == row["exit"]["state"] == 0
    # Future gross would overflow, but only origin G is the denominator.
    assert row["exit"]["normalizedChange"] == pytest.approx(-60 / 140)


def test_representable_normalized_label_survives_intermediate_subtraction_overflow():
    pairs = [{"pairId": "AB", "legs": [{"symbol": A, "quantity": 1}, {"symbol": B, "quantity": -1}]}]
    rows = raw_rows()
    for row in rows:
        if row["date"] == DATES[1] and row["symbol"] in (A, B):
            row["close"] = 1e307 if row["symbol"] == A else 1.1e308
        if row["date"] > DATES[1] and row["symbol"] in (A, B):
            row["open"] = 1.1e308 if row["symbol"] == A else 1e307
    source, spec = inputs(rows=rows, pairs=pairs, origins=[DATES[1]])
    row = build_targets(spec, source)["rows"][0]
    assert row["current"]["state"] == -1e308
    assert row["exit"]["state"] == 1e308
    assert row["exit"]["labelStatus"] == "complete"
    assert row["exit"]["normalizedChange"] == pytest.approx(2 / 1.2)


def test_contract_document_matches_implemented_shape_and_closed_keys():
    path = Path(__file__).resolve().parents[2] / "contracts" / "pair-research-v1.json"
    contract = json.loads(path.read_text())
    source, spec = inputs()
    assert contract["localShapeLimits"] == LIMITS
    assert set(contract["sourceDomainKeys"]) == set(source["sourceDomain"])
    assert set(contract["priceInput"]["keys"]) == set(source)
    assert set(contract["declarationKeys"]) == set(spec)
    assert contract["registeredProfile"] is None
    assert contract["productAdmission"] is contract["capacityAccepted"] is False


def test_import_and_kernel_are_stdlib_only():
    # A fresh interpreter demonstrates this module cannot accidentally import the
    # research engine's fit/OLS/provider path through package initialization.
    script = """
import sys
from atlas_quant.pair_research import build_targets
assert not any(name.split('.')[0] in {'numpy', 'pandas', 'sklearn', 'requests', 'scipy'} for name in sys.modules)
assert not any(name.startswith(('atlas_quant.engine', 'atlas_quant.runner', 'atlas_quant.statistical_quant', 'atlas_quant.market_acquisition')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", script], check=True)
