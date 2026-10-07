import copy
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from atlas_quant.fixtures import make_demo_data
from atlas_quant.provider import (DATASETS, OFFICIAL_URL, ProviderError, TushareClient,
                                  _load, validate_upload, validate_universe)


def strategy():
    return {"universe": {"symbols": ["000001.SZ", "600000.SH"], "start": "20230102", "end": "20230106"}}


class Response:
    status_code = 200

    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield json.dumps(self.body).encode()


class Session:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            return Response(self.error)
        payload = kwargs["json"]
        name, params = payload["api_name"], payload["params"]
        dates = pd.date_range(params["start_date"], params["end_date"])
        rows = []
        for i, d in enumerate(dates):
            dt = d.strftime("%Y%m%d")
            if name == "trade_cal":
                rows.append(["SSE", dt, int(d.weekday() < 5), "20221230"])
            elif d.weekday() < 5 and name == "adj_factor":
                rows.append([params["ts_code"], dt, 1.0 if i < 2 else 2.0])
            elif d.weekday() < 5 and name == "daily_basic":
                rows.append([params["ts_code"], dt] + [None if field == "pe" and i == 2 else 2.0+i/10 for field in DATASETS[name].split(",")[2:]])
            elif d.weekday() < 5:
                close = 100.0 if i < 2 else 50.0
                rows.append([params["ts_code"], dt, close, close+1, close-1, close, 1000, 10000])
        return Response({"code": 0, "msg": None, "data": {"fields": DATASETS[name].split(","), "items": rows}})


def test_official_https_adjustment_calendar_and_fingerprint(tmp_path):
    session = Session()
    token = "private-do-not-echo-token"
    data, p = _load(strategy(), TushareClient(token, session=session), tmp_path, token)
    assert len(data) == 10 and set(data.close) == {100.0}
    assert set(data.raw_close) == {100.0, 50.0}
    assert p["tradingDates"] == ["20230102", "20230103", "20230104", "20230105", "20230106"]
    assert p["source"] == "TUSHARE_PRO" and not p["synthetic"]
    assert all(url == OFFICIAL_URL and c["allow_redirects"] is False for url, c in session.calls)
    assert len(session.calls) == 5
    assert len(p["dataFingerprint"]) == 64
    for file in tmp_path.rglob("*.json"):
        assert token not in file.read_text()
        assert file.stat().st_mode & 0o077 == 0
    second = Session(error={"code": -2002, "msg": token})
    again, cached = _load(strategy(), TushareClient(token, session=second), tmp_path, token)
    pd.testing.assert_frame_equal(data, again)
    assert cached["cacheHit"] and second.calls == []


def test_provider_permission_never_echoes_secret_or_falls_back():
    token = "SECRET-TOKEN-DO-NOT-ECHO"
    client = TushareClient(token, session=Session(error={"code": -2002, "msg": token}))
    with pytest.raises(ProviderError) as error:
        _load(strategy(), client, None, token)
    assert error.value.code == "TUSHARE_PERMISSION"
    assert token not in str(error.value)


def test_explicit_rate_limit_and_redirect_denial():
    client = TushareClient("secret", session=Session(error={"code": -1, "msg": "每分钟最多访问"}))
    with pytest.raises(ProviderError, match="频次") as e:
        client.call("trade_cal", {"exchange": "SSE", "start_date": "20230102", "end_date": "20230106"})
    assert e.value.code == "TUSHARE_RATE_LIMIT"
    session = Session()
    session.post = lambda *a, **k: Response({}, 302)
    with pytest.raises(ProviderError) as e:
        TushareClient("secret", session=session).call("trade_cal", {"exchange": "SSE", "start_date": "20230102", "end_date": "20230106"})
    assert e.value.code == "TUSHARE_HTTP_ERROR"


def test_cache_isolated_by_credential(tmp_path):
    _load(strategy(), TushareClient("token1", session=Session()), tmp_path, "token1")
    other = Session()
    _load(strategy(), TushareClient("token2", session=other), tmp_path, "token2")
    assert len(other.calls) == 5
    assert len(list(tmp_path.iterdir())) == 2


def test_incomplete_adjustments_and_calendars_fail():
    for target in ("adj_factor", "trade_cal"):
        session = Session()
        original = session.post
        def truncated(url, **kwargs):
            response = original(url, **kwargs)
            if kwargs["json"]["api_name"] == target:
                response.body["data"]["items"] = response.body["data"]["items"][1:]
            return response
        session.post = truncated
        with pytest.raises(ProviderError) as e:
            _load(strategy(), TushareClient("token", session=session), None, "token")
        assert e.value.code in ("ADJUSTMENT_MISSING", "INCOMPLETE_CALENDAR")


def test_proxy_preserves_tushare_contract_without_token_field():
    session = Session()
    _load(strategy(), TushareClient(None, proxy_url="https://atlas-aletheia.com/internal/quant-data", service_token="a"*32, session=session), None, "a"*32)
    for _, kwargs in session.calls:
        assert "token" not in kwargs["json"]
        assert kwargs["headers"]["Authorization"] == "Bearer " + "a"*32


def test_synthetic_is_deterministic_exact_requested_dates_and_order_independent():
    s = strategy()
    one, p1 = make_demo_data(s)
    two, p2 = make_demo_data(s)
    pd.testing.assert_frame_equal(one, two)
    assert p1 == p2 and p1["source"] == "SYNTHETIC" and p1["synthetic"]
    assert one.trade_date.min() == s["universe"]["start"]
    assert one.trade_date.max() == s["universe"]["end"]
    reverse = copy.deepcopy(s)
    reverse["universe"]["symbols"].reverse()
    other, _ = make_demo_data(reverse)
    pd.testing.assert_frame_equal(one, other)


def test_upload_keeps_claims_unverified_and_calendar_gaps():
    rows, _ = make_demo_data(strategy())
    entries = json.loads(rows.to_json(orient="records"))
    entries = [r for r in entries if r["trade_date"] != "20230104"]
    frame, p = validate_upload(strategy(), {"rows": entries, "provenance": {"source": "claimed Tushare", "tradingDates": ["20230102", "20230103", "20230104", "20230105", "20230106"]}})
    assert "20230104" not in set(frame.trade_date) and "20230104" in p["tradingDates"]
    assert p["source"] == "USER_UPLOAD" and p["classification"] == "USER_PROVIDED_UNVERIFIED"
    assert p["declaredSource"] == "claimed Tushare"


@pytest.mark.parametrize("label", [{"synthetic": True}, {"source": "SYNTHETIC"}, {"classification": "SYNTHETIC_EDUCATIONAL_ONLY", "synthetic": False}])
def test_synthetic_upload_never_loses_educational_boundary(label):
    frame, _ = make_demo_data(strategy())
    entries = json.loads(frame.to_json(orient="records"))
    _, provenance = validate_upload(strategy(), {"rows": entries, "provenance": label})
    assert provenance["source"] == "USER_UPLOAD"
    assert provenance["synthetic"] is True
    assert provenance["classification"] == "SYNTHETIC_USER_UPLOAD_UNVERIFIED"
    assert any("合成" in warning for warning in provenance["warnings"])


@pytest.mark.parametrize("field,value,code", [("close", float("inf"), "INVALID_DATASET"), ("low", 1000000, "INVALID_OHLC"), ("trade_date", "20230132", "INVALID_DATE"), ("ts_code", "600519.SH", "DATASET_SYMBOL"), ("vol", -1, "INVALID_PRICE")])
def test_upload_rejects_invalid_data(field, value, code):
    rows, _ = make_demo_data(strategy())
    entries = json.loads(rows.to_json(orient="records"))
    entries[0][field] = value
    with pytest.raises(ProviderError) as e:
        validate_upload(strategy(), {"rows": entries})
    assert e.value.code == code


def test_upload_duplicates_and_invalid_calendar_rejected():
    rows, _ = make_demo_data(strategy())
    entries = json.loads(rows.to_json(orient="records"))
    with pytest.raises(ProviderError) as e:
        validate_upload(strategy(), {"rows": entries + entries[:1]})
    assert e.value.code == "DUPLICATE_OBSERVATION"
    with pytest.raises(ProviderError) as e:
        validate_upload(strategy(), {"rows": entries, "provenance": {"tradingDates": ["20230102"]}})
    assert e.value.code == "INVALID_CALENDAR"


def test_universe_budget():
    s = strategy()
    s["universe"]["symbols"] *= 26
    with pytest.raises(ProviderError) as e:
        validate_universe(s)
    assert e.value.code == "UNIVERSE_LIMIT"


def test_provider_disallows_arbitrary_parameters_and_apis():
    client = TushareClient("token", session=Session())
    with pytest.raises(ProviderError) as e:
        client.call("daily", {"ts_code": "000001.SZ", "start_date": "20230102", "end_date": "20230106", "token": "override"})
    assert e.value.code == "PROVIDER_PARAMS"
    with pytest.raises(ProviderError) as e:
        client.call("arbitrary_unregistered_api", {})
    assert e.value.code == "DATASET_FORBIDDEN"


def test_daily_basic_only_requested_for_selected_fields_and_cache_separated(tmp_path):
    plain = Session()
    _load(strategy(), TushareClient("token", session=plain), tmp_path, "token")
    assert not any(call[1]["json"]["api_name"] == "daily_basic" for call in plain.calls)
    s = strategy()
    s["factors"] = [{"id": "valuation", "expression": "rank(pb) + rank(pe)"}]
    session = Session()
    data, p = _load(s, TushareClient("token", session=session), tmp_path, "token")
    assert len(session.calls) == 7
    assert {"pb", "pe"}.issubset(data.columns) and "dv_ratio" not in data.columns
    assert data.pe.isna().sum() == 2
    assert p["optionalFieldCoverage"]["pe"] == .8
    assert "daily_basic" in p["datasets"] and "pb" in p["observedColumns"]
    assert len(list(tmp_path.rglob("*.json"))) == 2


def test_upload_preserves_known_optional_fields_and_null():
    rows, _ = make_demo_data(strategy())
    entries = json.loads(rows.to_json(orient="records"))
    for i, row in enumerate(entries):
        row["pb"] = None if i == 0 else 2.5
        row["unknown_private_metadata"] = "ignored"
    s = strategy()
    s["factors"] = [{"id": "pb", "expression": "rank(pb)"}]
    data, p = validate_upload(s, {"rows": entries})
    assert "pb" in data and data.pb.isna().sum() == 1
    assert "unknown_private_metadata" not in data
    assert p["optionalFieldCoverage"]["pb"] == .9
    s["factors"][0]["expression"] = "rank(pe)"
    with pytest.raises(ProviderError) as e:
        validate_upload(s, {"rows": entries})
    assert e.value.code == "MISSING_FACTOR_DATA"


def test_amount_derivation_requires_explicit_metadata_and_preserves_boundary():
    rows, _ = make_demo_data(strategy())
    entries = json.loads(rows.to_json(orient="records"))
    for row in entries:
        del row["amount"]
    with pytest.raises(ProviderError) as e:
        validate_upload(strategy(), {"rows": entries})
    assert e.value.code == "AMOUNT_REQUIRED"
    data, p = validate_upload(strategy(), {"rows": entries, "provenance": {"amountDerivation": "vol*close*100/1000"}})
    assert data.iloc[0].amount == pytest.approx(data.iloc[0].vol * data.iloc[0].close / 10)
    assert "amount" not in p["observedColumns"]
    assert p["derivedColumns"]["amount"]["classification"] == "APPROXIMATION_NOT_OBSERVED"
    entries[0]["raw_close"] *= 2
    with pytest.raises(ProviderError) as e:
        validate_upload(strategy(), {"rows": entries, "provenance": {"amountDerivation": "vol*close*100/1000"}})
    assert e.value.code == "AMOUNT_DERIVATION"
