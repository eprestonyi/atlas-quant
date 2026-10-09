"""Foreign ETF marks are adjusted and available strictly after their US date."""
import copy
import pandas as pd
import pytest

from atlas_quant.context_sources import load_context_fields, project_context_records, validate_context_sources
from atlas_quant.provider import ProviderError, _exact_records
from atlas_quant.statistical_quant.preprocessing import factor_descriptor
from test_context_snapshot_audit import make_auditor

FIELD = 'ext_ctx_xsd_close'
DATES = ['20240311', '20240312', '20240318', '20240325', '20240326']


def fixture():
    records = [{'ts_code': 'XSD', 'trade_date': date, 'close': close, 'adj_factor': adjustment}
               for date, close, adjustment in [('20240308',100.,.5), ('20240311',51.,1.),
                                               ('20240312',52.,1.), ('20240315',53.,1.), ('20240318',400.,1.)]]
    class Source:
        def call(self, api, params):
            assert api == 'us_daily_adj' and params == {'ts_code': 'XSD', 'start_date': '20240304', 'end_date': DATES[-1]}
            return pd.DataFrame(records)
    frame = pd.DataFrame([{'ts_code': symbol, 'trade_date': date} for date in DATES for symbol in ['000001.SZ', '600000.SH']])
    frame, provenance = load_context_fields(Source(), frame, [FIELD], DATES, DATES[0], DATES[-1])
    return frame, provenance


def test_previous_us_session_is_adjusted_and_never_same_date_lookahead():
    frame, provenance = fixture()
    observed = frame.groupby('trade_date')[FIELD].first()
    assert observed.iloc[:4].tolist() == [50.,51.,53.,400.]
    assert pd.isna(observed.iloc[4])  # last real mark exceeds seven days
    available = frame.groupby('trade_date')[FIELD+'__available_date'].first()
    assert available.iloc[:4].tolist() == ['20240309','20240312','20240316','20240319']
    assert provenance['contextScope'] == 'named_market_series_asof_broadcast_by_date'
    assert provenance['contextSources'][0]['records'][0]['close'] == 100.
    assert provenance['contextSources'][0]['records'][0]['adj_factor'] == .5
    validate_context_sources(frame, provenance, [FIELD], DATES, DATES[0], DATES[-1])
    descriptor = factor_descriptor({'id':'etf','expression':FIELD,'direction':1})
    assert descriptor['scope'] == 'global'
    assert descriptor['transform']['kind'] == 'return_over_trailing_volatility'


@pytest.mark.parametrize('date', ['20240311', '20241104'])
def test_dst_does_not_permit_the_current_us_session_at_cn_origin(date):
    records = [{'trade_date':date,'close':999.,'adj_factor':1.}]
    assert project_context_records('us_daily_adj', records, 'close', [date]) == {date: (None,None)}


@pytest.mark.parametrize('damage', ['same_day', 'unadjusted', 'clock', 'available'])
def test_source_validation_rejects_wrong_time_or_price_projection(damage):
    frame, provenance = fixture()
    if damage == 'same_day': frame.loc[frame.trade_date==DATES[0],FIELD] = 51.
    elif damage == 'unadjusted': frame.loc[frame.trade_date==DATES[0],FIELD] = 100.
    elif damage == 'clock': provenance['contextObservationClock'] = 'after_daily_publication_before_next_open'
    else: frame.loc[frame.trade_date==DATES[0],FIELD+'__available_date'] = '20240308'
    with pytest.raises(ProviderError):
        validate_context_sources(frame, provenance, [FIELD], DATES, DATES[0], DATES[-1])


def test_independent_standard_library_audit_recomputes_adjustment_and_asof():
    frame, provenance = fixture()
    snapshot = {'provenance':provenance, 'rows':_exact_records(frame), 'dataFingerprint':'a'*64}
    obj = make_auditor(snapshot)
    try:
        obj.validate_context_sources(provenance)
        assert obj.checks >= len(frame)
    finally:
        obj.db.close()
    wrong = copy.deepcopy(snapshot)
    wrong['rows'][0][FIELD] = 51.
    obj = make_auditor(wrong)
    try:
        with pytest.raises(ValueError, match='broadcast'):
            obj.validate_context_sources(provenance)
    finally:
        obj.db.close()


def test_adjusted_price_overflow_is_rejected_before_factor_transform():
    records = [{'trade_date':'20240308','close':1e308,'adj_factor':1e308}]
    with pytest.raises(ProviderError) as failure:
        project_context_records('us_daily_adj', records, 'close', ['20240311'])
    assert failure.value.code == 'CONTEXT_SOURCE_INVALID'
