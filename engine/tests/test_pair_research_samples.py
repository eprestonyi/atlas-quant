"""Tiny fixed archives and hand calculations only: no F/provider/HTTP calls."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
import uuid

import numpy as np
import pandas as pd
import pytest

from atlas_quant.market_acquisition.normalize import build_publication
from atlas_quant.market_acquisition.protocol import BASE_FIELDS, LIMITS, encode, sha
from atlas_quant.market_acquisition.reader import MarketSourceReader
from atlas_quant.pair_research import PairContractError, declare_targets
from atlas_quant.pair_research.contract import ADJUSTMENT, digest
from atlas_quant.pair_research.source import read_market_source
from atlas_quant.pair_research.research_contract import (
    declare_research, research_origins, validate_research_contract,
)
from atlas_quant.pair_research.samples import build_samples

ROOT = Path(__file__).resolve().parents[2]
A, B, C, D, E = [f"{i:06d}.SZ" for i in range(1, 6)]
SYMBOLS = [A, B, C, D, E]
START, END = date(2024, 1, 1), date(2024, 4, 26)
DAYS = [START + timedelta(days=i) for i in range((END - START).days + 1)]
DATES = [d.strftime("%Y%m%d") for d in DAYS if d.weekday() < 5]
RANK = [{"id": "rank_close", "expression": "rank(close)", "direction": 1, "role": "predictor"}]


@pytest.fixture(autouse=True)
def no_fit_or_network(monkeypatch):
    from atlas_quant.statistical_quant import models
    import requests

    def forbidden(*args, **kwargs):
        raise AssertionError("Stage 2A must not fit, estimate q, or call a network/provider")
    monkeypatch.setattr(models, "fit", forbidden)
    monkeypatch.setattr(np.linalg, "lstsq", forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)


def archive(*, edit=None, missing=frozenset(), with_basic=False):
    """Deterministic tiny raw endpoint fixtures, with explicit test-only pins.

    These synthetic pins are NOT independent source authorization. Production
    callers must obtain the expected pins separately; every authenticity bit
    stays false regardless of this fixture's internal consistency.
    """
    base = json.loads((ROOT / "contracts/fixtures/market-plan-v1.json").read_text())
    scope = json.loads((ROOT / "contracts/fixtures/market-scope-v1.json").read_text())
    scope.update(symbols=SYMBOLS, symbolCount=5, start=START.strftime("%Y%m%d"), end=END.strftime("%Y%m%d"))
    scope["selection"]["includeSymbols"] = SYMBOLS
    ref = {**base["universeScopeRef"], "scopeRoot": sha(encode(scope))}
    plan = {**base, "universeScopeRef": ref,
            "scope": {**{k: scope[k] for k in ("symbols", "symbolCount", "start", "end")},
                      "scopeRoot": ref["scopeRoot"]},
            "fields": sorted([*BASE_FIELDS, *(["pb"] if with_basic else [])]), "requests": []}
    requests = [("trade_cal", {"exchange": "SZSE"}, "exchange,cal_date,is_open,pretrade_date", 65536)]
    for symbol in SYMBOLS:
        requests.extend([
            ("daily", {"ts_code": symbol}, "ts_code,trade_date,open,high,low,close,vol,amount", 131072),
            ("adj_factor", {"ts_code": symbol}, "ts_code,trade_date,adj_factor", 65536),
        ])
        if with_basic:
            requests.append(("daily_basic", {"ts_code": symbol}, "ts_code,trade_date,pb", 262144))
    for i, (api, params, fields, budget) in enumerate(requests):
        definition = {"provider": "TUSHARE_PRO", "authorizationScope": plan["authorizationScope"],
                      "apiName": api, "params": {**params, "start_date": scope["start"], "end_date": scope["end"]},
                      "fields": fields, "responseBytes": budget, "maxAttempts": 1}
        plan["requests"].append({"ordinal": i, **definition, "requestKey": sha(encode(definition))})
    plan["budget"] = {**LIMITS, "calendarDays": len(DAYS), "declaredRequests": len(requests),
                      "materializedRequests": len(requests),
                      "rawResponseCeilingBytes": sum(r["responseBytes"] for r in plan["requests"])}
    plan["planRoot"] = sha(encode({k: v for k, v in plan.items() if k != "planRoot"}))
    receipts, parts = {}, {}
    for request in plan["requests"]:
        records = []
        for day in DAYS:
            day_text = day.strftime("%Y%m%d")
            if request["apiName"] == "trade_cal":
                row = {"exchange": "SZSE", "cal_date": day_text,
                       "is_open": int(day.weekday() < 5), "pretrade_date": None}
            elif day.weekday() >= 5:
                continue
            else:
                t, symbol = DATES.index(day_text), request["params"]["ts_code"]
                if request["apiName"] == "daily" and (t, symbol) in missing:
                    continue
                close = {A: 100 + t, B: 40 + .5 * t, C: 60 + .25 * t, D: 80., E: 120.}[symbol]
                row = {"ts_code": symbol, "trade_date": day_text, "close": close, "open": close + 1,
                       "vol": 100., "amount": 500., "adj_factor": 2., "pb": 1.5}
                if edit:
                    edit(row, t, symbol)
                row.update(high=max(row["open"], row["close"]) + 2, low=min(row["open"], row["close"]) - 2)
            records.append([row[k] for k in request["fields"].split(",")])
        raw = encode({"code": 0, "data": {"fields": request["fields"].split(","), "items": records}})
        receipts[request["requestKey"]] = {
            "requestKey": request["requestKey"],
            "receiptId": str(uuid.uuid5(uuid.NAMESPACE_URL, "PAIR_2A_FIXTURE:" + request["requestKey"])),
            "raw": raw, "sha256": sha(raw), "byteLength": len(raw), "httpStatus": 200,
            "retrievedAt": "2026-10-08T00:00:00Z", "sourceKind": "fixture",
        }
    manifest = build_publication({}, plan, lambda r: receipts[r["requestKey"]],
                                 lambda n, i, raw: parts.__setitem__((n, i), raw))
    calls = []

    def read_part(n, i):
        calls.append((n, i))
        return parts[n, i]

    reader = MarketSourceReader(encode(manifest), encode(plan), encode(scope), read_part)
    expected = {
        "ownerKey": "explicit-test-owner",
        "marketDatasetRef": {"datasetId": "00000000-0000-4000-8000-000000000011",
                             "datasetRoot": sha(encode(manifest)), "format": "atlas.quant.market_dataset", "version": 1},
        "universeScopeRef": ref, "symbols": SYMBOLS, "start": scope["start"], "end": scope["end"],
        "calendar": DATES, "membershipPolicy": "complete_filtered_set", "currency": "CNY",
        "priceUnit": "CNY_per_adjusted_share", "quantityUnit": "adjusted_share", "adjustment": ADJUSTMENT,
    }
    return reader, expected, calls, parts


@pytest.fixture(scope="module")
def fixed_source():
    reader, pins, _, _ = archive()
    return read_market_source(reader, expected_domain=pins)


def prepare(source, *, factors=None, pairs=None, cutoff=None, observation_days=1, horizon=2):
    factors = RANK if factors is None else factors
    pairs = [
        {"pairId": "AB", "legs": [{"symbol": A, "quantity": 1}, {"symbol": B, "quantity": -2}]},
        {"pairId": "BC", "legs": [{"symbol": B, "quantity": 1}, {"symbol": C, "quantity": -1}]},
    ] if pairs is None else pairs
    cutoff = DATES[1] if cutoff is None else cutoff
    origins = research_origins(source, quantity_cutoff=cutoff, factors=factors, observation_days=observation_days)
    declaration = declare_targets(source.price_input, pairs, quantity_cutoff=cutoff, origins=origins,
                                  horizon_sessions=horizon)
    contract = declare_research(source, declaration, factors=factors, observation_days=observation_days)
    return declaration, contract, build_samples(source, declaration, contract)


def test_hand_targets_shared_leg_and_unmatched_complete_grid(fixed_source):
    declaration, contract, prepared = prepare(fixed_source)
    s = prepared.samples
    ab, bc = s.meta.iloc[0], s.meta.iloc[1]
    assert ab.dateIndex == 61 and ab.currentState == 20 and ab.scale == 302
    assert ab.entryDate == DATES[62] and ab.targetDate == DATES[64]
    assert s.y.iloc[0].tolist() == pytest.approx([-1 / 302, -1 / 302])
    assert bc.currentState == -4.75 and bc.scale == 145.75
    assert s.y.iloc[1].tolist() == pytest.approx([.25 / 145.75, .75 / 145.75])
    assert s.X.iloc[0]["change1"] == 0
    assert s.X.iloc[1]["change1"] == pytest.approx(.25 / 145.75)
    assert s.X.iloc[1]["state_deviation20"] == pytest.approx((.25 * 9.5) / 145.75)
    assert s.X.iloc[1]["state_deviation60"] == pytest.approx((.25 * 29.5) / 145.75)
    assert s.X.iloc[1]["volatility20"] == 0
    assert s.hedge_fits == [] and len(s.meta) == 2 * len(declaration["origins"])
    assert prepared.evidence["memberStates"][1]["pairIds"] == ["AB", "BC"]
    assert [m["symbol"] for m in prepared.evidence["memberStates"] if m["status"] == "unmatched"] == [D, E]
    assert contract["targetTiming"] == "legacy_next_open_plus_h"
    assert not s.meta.modelAvailable.any()


def test_source_pin_and_authority_boundaries(fixed_source):
    evidence = fixed_source.evidence
    assert evidence["transportVerified"] and evidence["normalizationRecomputed"] and evidence["contentPinsMatched"]
    assert evidence["sourceKind"] == "fixture" and evidence["pinAuthority"] == "caller_assertion_not_authenticated"
    assert evidence["unverifiedCallerIdentityFields"] == ["ownerKey", "marketDatasetRef.datasetId"]
    for flag in ("sourceAuthorityVerified", "hostedOwnerAuthorizationVerified", "historicalMembershipVerified",
                 "historicalRevisionVintageVerified", "originalProviderWireVerified"):
        assert evidence[flag] is False
    for key in ("sourceAuthorityVerified", "quantityProvenanceVerified", "pairSelectionLeakageVerified"):
        assert prepare(fixed_source)[2].evidence[key] is False
    # Returned values cannot change later source projections or feature frames.
    price, frame = fixed_source.price_input, fixed_source.frame()
    price["rows"][0]["close"] = 1
    frame.iloc[0, 0] = -123
    assert fixed_source.price_input["rows"][0]["close"] == 100
    assert fixed_source.frame().iloc[0, 0] != -123


@pytest.mark.parametrize("change", ["dataset_root", "scope_root", "scope_id", "symbols", "range", "calendar", "units", "extra"])
def test_bad_pins_reject_before_part_reads(change):
    reader, pins, calls, _ = archive()
    pins = deepcopy(pins)
    if change == "dataset_root": pins["marketDatasetRef"]["datasetRoot"] = "0" * 64
    if change == "scope_root": pins["universeScopeRef"]["scopeRoot"] = "0" * 64
    if change == "scope_id": pins["universeScopeRef"]["scopeId"] = "00000000-0000-4000-8000-000000000022"
    if change == "symbols": pins["symbols"] = pins["symbols"][:-1]
    if change == "range": pins["start"] = "20231231"
    if change == "calendar": pins["calendar"] = pins["calendar"][1:]
    if change == "units": pins["quantityUnit"] = "raw_share"
    if change == "extra": pins["ignoreMe"] = True
    with pytest.raises(PairContractError):
        read_market_source(reader, expected_domain=pins)
    assert calls == []


def test_complete_1000_is_rejected_without_source_materialization():
    reader, pins, calls, _ = archive()
    pins["symbols"] = [f"{i:06d}.SZ" for i in range(1, 1001)]
    with pytest.raises(PairContractError, match="never truncate"):
        read_market_source(reader, expected_domain=pins)
    assert calls == []


def test_source_parts_are_actually_verified_and_cache_is_not_reader():
    reader, pins, _, parts = archive()
    parts["rows", 0] = parts["rows", 0][:-1] + b" "
    with pytest.raises(ValueError, match="bytes differ"):
        read_market_source(reader, expected_domain=pins)
    with pytest.raises(PairContractError, match="no cache/forecast fallback"):
        read_market_source({"legacyCache": []}, expected_domain=pins)


@pytest.mark.parametrize("a_base,expected_state", [(80., 0.), (60., -20.)])
def test_zero_and_negative_spread_never_divide_by_s(a_base, expected_state):
    def edit(row, t, symbol):
        if symbol == A:
            row.update(close=a_base + t, open=a_base + t + 1)
    reader, pins, _, _ = archive(edit=edit)
    source = read_market_source(reader, expected_domain=pins)
    _, _, result = prepare(source)
    row = result.samples.meta.iloc[0]
    assert row.currentState == expected_state and row.inputValid
    assert row.scale == a_base + 61 + 2 * (40 + .5 * 61)
    assert result.samples.y.iloc[0].tolist() == pytest.approx([-1 / row.scale, -1 / row.scale])


def test_quantity_orientation_and_factor_direction_are_not_renormalized(fixed_source):
    declaration, _, original = prepare(fixed_source)
    reversed_pairs = deepcopy(declaration["targetScope"]["pairMap"])
    for pair in reversed_pairs:
        for leg in pair["legs"]:
            leg["quantity"] *= -1
    _, _, reverse = prepare(fixed_source, pairs=reversed_pairs)
    np.testing.assert_allclose(reverse.samples.meta.currentState, -original.samples.meta.currentState)
    np.testing.assert_allclose(reverse.samples.meta.scale, original.samples.meta.scale)
    np.testing.assert_allclose(reverse.samples.y, -original.samples.y, equal_nan=True)
    for feature in ("state_deviation20", "state_deviation60", "change1", "factor:rank_close"):
        np.testing.assert_allclose(reverse.samples.X[feature], -original.samples.X[feature], equal_nan=True)
    _, _, direction = prepare(fixed_source, factors=[{**RANK[0], "direction": -1}])
    np.testing.assert_allclose(direction.samples.X["factor:rank_close"], -original.samples.X["factor:rank_close"])


def test_nonconstant_state_feature_units_by_hand():
    def edit(row, t, symbol):
        if t == 61 and symbol == A:
            row["close"] += 20
    reader, pins, _, _ = archive(edit=edit)
    _, _, result = prepare(read_market_source(reader, expected_domain=pins), factors=[])
    x = result.samples.X.iloc[0]
    assert result.samples.meta.iloc[0].currentState == 40
    assert result.samples.meta.iloc[0].scale == 322
    # Twenty changes [0]*19+[20] have sample variance 20, independently
    # calculated; state windows are [20]*19+[40] and [20]*59+[40].
    assert x.volatility20 == pytest.approx(np.sqrt(20) / 322)
    assert x.state_deviation20 == pytest.approx(19 / 322)
    assert x.state_deviation60 == pytest.approx((59 / 3) / 322)
    assert x.change1 == pytest.approx(20 / 322)


def test_unmatched_member_participates_in_same_day_rank(fixed_source):
    _, _, original = prepare(fixed_source)
    def edit(row, t, symbol):
        if symbol == E and t == 61:
            row.update(close=1000., open=1000.)
    reader, pins, _, _ = archive(edit=edit)
    _, _, changed = prepare(read_market_source(reader, expected_domain=pins))
    # At t=61 B=70.5, C=75.25, D=80, E=120, A=161; ranks A=1,B=.2.
    assert original.samples.X.iloc[0]["factor:rank_close"] == pytest.approx((161 - 141 * .2) / 302)
    assert changed.samples.X.iloc[0]["factor:rank_close"] == pytest.approx((161 * .8 - 141 * .2) / 302)
    assert original.targets["rows"][0]["current"] == changed.targets["rows"][0]["current"]
    assert changed.evidence["memberStates"][-1]["status"] == "unmatched"


def test_prefix_future_perturbation_and_dsl_receives_no_future(fixed_source, monkeypatch):
    import atlas_quant.pair_research.features as module
    called = []
    original_eval = module.evaluate_expression
    def observed_eval(expression, frame):
        called.append(frame.index.get_level_values("trade_date").max())
        return original_eval(expression, frame)
    monkeypatch.setattr(module, "evaluate_expression", observed_eval)
    factors = [*RANK, {"id": "moment", "expression": "returns(close, 5) + ts_mean(close, 3)", "direction": 1, "role": "predictor"}]
    _, _, first = prepare(fixed_source, factors=factors)
    assert called == [d for d in DATES[61:] for _ in factors]
    def edit(row, t, symbol):
        if t > 70:
            row.update(close=row["close"] * (2 + int(symbol[:6])), open=row["open"] * 3, vol=10000.)
    reader, pins, _, _ = archive(edit=edit)
    _, _, second = prepare(read_market_source(reader, expected_domain=pins), factors=factors)
    before = first.samples.meta.date <= DATES[70]
    pd.testing.assert_frame_equal(first.samples.X.loc[before], second.samples.X.loc[before])
    assert first.samples.meta.loc[before, "featurePrefixRoot"].tolist() == second.samples.meta.loc[before, "featurePrefixRoot"].tolist()
    assert first.evidence["featureInputRoot"] != second.evidence["featureInputRoot"]


def test_missing_session_and_leg_keep_calendar_origins_and_each_status():
    reader, pins, _, _ = archive(missing={(60, B), (62, C)})
    source = read_market_source(reader, expected_domain=pins)
    spec, _, result = prepare(source)
    assert result.samples.dates == DATES and spec["origins"] == DATES[61:]
    assert len(source.frame()) == 5 * len(DATES)
    assert source.evidence["missingRows"] == 2
    ab, bc = result.samples.meta.iloc[0], result.samples.meta.iloc[1]
    assert ab.currentState == 20 and ab.targetStateValid and not ab.inputValid
    assert ab.invalidReason == "missing_close_history" and ab.missingHistoryLegs == [B]
    assert ab.entryLabelStatus == "complete"
    assert bc.entryDate == DATES[62] and bc.targetDate == DATES[64]
    assert bc.entryLabelStatus == "valuation_unavailable" and np.isnan(result.samples.y.iloc[1].entry)
    assert len(result.samples.meta) == 2 * len(DATES[61:])


def test_missing_predictor_does_not_fill_or_discard_target_state():
    def edit(row, t, symbol):
        if t == 61 and symbol == B:
            row["pb"] = None
    reader, pins, _, _ = archive(edit=edit, with_basic=True)
    factors = [{"id": "pb", "expression": "pb", "direction": 1, "role": "predictor"}]
    _, _, result = prepare(read_market_source(reader, expected_domain=pins), factors=factors)
    row = result.samples.meta.iloc[0]
    assert row.inputValid and row.missingFactorLegs == {"pb": [B]}
    assert np.isnan(result.samples.X.iloc[0]["factor:pb"])
    assert result.samples.y.iloc[0].notna().all()


def test_tails_and_empty_t_remain_explicit(fixed_source):
    _, _, result = prepare(fixed_source)
    tail = result.samples.meta.iloc[-1]
    assert tail.date == DATES[-1] and tail.inputValid and tail.entryDate is None and tail.targetDate is None
    assert tail.exitLabelStatus == "valuation_unavailable" and result.samples.y.iloc[-1].isna().all()
    _, _, empty = prepare(fixed_source, pairs=[])
    assert empty.samples.X.empty and empty.samples.y.empty and empty.samples.meta.empty
    assert empty.samples.definitions == {} and empty.samples.hedge_fits == []
    assert empty.evidence["status"] == "empty_target_scope_no_fit"
    assert len(empty.evidence["memberStates"]) == 5 and all(x["status"] == "unmatched" for x in empty.evidence["memberStates"])


def test_quantity_cutoff_is_before_every_sample_and_no_silent_grid_trim(fixed_source):
    spec, contract, result = prepare(fixed_source, cutoff=DATES[70], observation_days=3)
    assert spec["origins"] == DATES[71::3]
    assert result.samples.start_index == 71
    assert all(result.samples.meta.date > DATES[70])
    changed = declare_targets(fixed_source.price_input, spec["targetScope"]["pairMap"],
                              quantity_cutoff=DATES[70], origins=spec["origins"][1:], horizon_sessions=2)
    with pytest.raises(PairContractError, match="never trim or compress"):
        declare_research(fixed_source, changed, factors=RANK, observation_days=3)
    with pytest.raises(PairContractError, match="No origins"):
        research_origins(fixed_source, quantity_cutoff=DATES[-1], factors=RANK, observation_days=1)


@pytest.mark.parametrize("factor", [
    {**RANK[0], "role": "hedge"}, {**RANK[0], "role": "event"},
    {**RANK[0], "direction": True}, {**RANK[0], "direction": .5},
    {**RANK[0], "expression": "lag(close,-1)"}, {**RANK[0], "expression": "pb"},
    {**RANK[0], "expression": "ext_custom"}, {**RANK[0], "extra": "ignore"},
])
def test_strict_predictors_and_source_fields(fixed_source, factor):
    with pytest.raises(PairContractError):
        prepare(fixed_source, factors=[factor])


def test_feature_lookback_and_finite_factor_count_envelope(fixed_source):
    long = [{**RANK[0], "expression": "lag(close,70)"}]
    spec, _, result = prepare(fixed_source, factors=long)
    assert spec["origins"][0] == DATES[71] and result.samples.start_index == 71
    with pytest.raises(PairContractError, match="16"):
        prepare(fixed_source, factors=[{**RANK[0], "id": f"f{i}"} for i in range(17)])
    with pytest.raises(PairContractError, match="Unique"):
        prepare(fixed_source, factors=[RANK[0], RANK[0]])
    for interval in (0, 61, True, 1.5):
        with pytest.raises(PairContractError):
            prepare(fixed_source, observation_days=interval)


@pytest.mark.parametrize("change", ["execution", "formation_claim", "selection_claim", "family", "feature_root",
                                     "dsl_root", "target_root", "version", "unknown", "root"])
def test_rehashed_contract_cannot_change_supported_semantics(fixed_source, change):
    spec, contract, _ = prepare(fixed_source)
    altered = deepcopy(contract)
    if change == "execution": altered["execution"]["enabled"] = True
    if change == "formation_claim": altered["quantityProvenanceVerified"] = True
    if change == "selection_claim": altered["pairSelectionLeakageVerified"] = True
    if change == "family": altered["family"] = "trend"
    if change == "feature_root": altered["featureSourceRoot"] = "0" * 64
    if change == "dsl_root": altered["dslContractRoot"] = "0" * 64
    if change == "target_root": altered["targetDeclarationRoot"] = "0" * 64
    if change == "version": altered["version"] = True
    if change == "unknown": altered["model"] = {"estimator": "auto"}
    altered["researchRoot"] = digest({k: v for k, v in altered.items() if k != "researchRoot"})
    if change == "root": altered["researchRoot"] = "0" * 64
    with pytest.raises(PairContractError):
        validate_research_contract(altered, fixed_source, spec)


def test_external_feature_source_and_owner_cannot_reuse_a_target(fixed_source):
    spec, contract, _ = prepare(fixed_source)
    def edit(row, t, symbol):
        row["vol"] += 1  # open/close values unchanged, but source identity differs.
    reader, pins, _, _ = archive(edit=edit)
    source = read_market_source(reader, expected_domain=pins)
    with pytest.raises(PairContractError, match="feature source differ"):
        build_samples(source, spec, contract)
    reader, pins, _, _ = archive()
    pins["ownerKey"] = "different-caller-owner"
    source = read_market_source(reader, expected_domain=pins)
    # Local owner assertion itself is not authenticated; it binds this experiment.
    assert source.evidence["hostedOwnerAuthorizationVerified"] is False
    with pytest.raises(PairContractError, match="feature source differ"):
        build_samples(source, spec, contract)


def test_replaced_in_process_feature_values_cannot_retain_the_old_root(fixed_source):
    spec, contract, _ = prepare(fixed_source)
    rows = list(fixed_source._rows)
    row = list(rows[0])
    row[-1] += 1
    rows[0] = tuple(row)
    replaced = replace(fixed_source, _rows=tuple(rows))
    with pytest.raises(PairContractError, match="bound source root"):
        build_samples(replaced, spec, contract)


def test_sample_budget_rejects_whole_domain_before_feature_calculation(fixed_source, monkeypatch):
    import atlas_quant.pair_research.research_contract as module
    monkeypatch.setattr(module, "MAX_SAMPLES", 2)
    with pytest.raises(PairContractError, match="never truncate"):
        prepare(fixed_source)
