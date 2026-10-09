"""Frozen global observations survive stock gaps; never fabricate stock bars.

All sources below are in-memory test fixtures. No provider, training, or F run.
"""
import copy
import json

import numpy as np
import pandas as pd
import pytest

from atlas_quant.bundle import encode
from atlas_quant.context_sources import load_context_fields, validate_context_sources
from atlas_quant.engine import _prepare_data, ResearchError
from atlas_quant.provider import ProviderError, canonical_hash, _exact_records, validate_upload

FIELD = 'ext_ctx_000300_sh_close'
AMOUNT = 'ext_ctx_000300_sh_amount'


@pytest.fixture
def frozen():
    dates = pd.bdate_range('20230102', periods=200).strftime('%Y%m%d').tolist()
    symbols = ['000001.SZ', '600000.SH']
    rows = [{'ts_code': symbol, 'trade_date': date, 'open': 10., 'close': 10.,
             'high': 11., 'low': 9., 'raw_close': 10., 'adj_factor': 1., 'vol': 100., 'amount': 100.}
            for date in dates if date != dates[80] for symbol in symbols
            if (date, symbol) != (dates[81], symbols[0])]
    source = pd.DataFrame([{'ts_code': '000300.SH', 'trade_date': date,
                           'close': 3000.1234567890123 + i, 'amount': 100.}
                          for i, date in enumerate(dates) if i != 100])
    source.loc[source.trade_date == dates[101], 'close'] = np.nan

    class FixtureSource:
        def call(self, api, params):
            assert api == 'index_daily' and params['ts_code'] == '000300.SH'
            return source.copy()

    frame, meta = load_context_fields(FixtureSource(), pd.DataFrame(rows), [FIELD, AMOUNT], dates, dates[0], dates[-1])
    provenance = {**meta, 'tradingDates': dates, 'source': 'SYNTHETIC', 'synthetic': True}
    strategy = {'schemaVersion': 2, 'universe': {'symbols': symbols, 'start': dates[0], 'end': dates[-1]},
                'factors': [{'id': 'market', 'expression': FIELD, 'direction': 1}]}
    return frame, provenance, strategy


def rehash(provenance):
    for source in provenance['contextSources']:
        source['sha256'] = canonical_hash(source['records'])
    provenance['contextSourceRoot'] = canonical_hash(provenance['contextSources'])


def validate(frame, meta, strategy):
    universe = strategy['universe']
    return validate_context_sources(frame, meta, [FIELD], meta['tradingDates'], universe['start'], universe['end'])


def test_restore_independent_dates_preserves_stock_gaps_and_exact_values(frozen):
    frame, meta, strategy = frozen
    panel, dates, audit = _prepare_data(frame, strategy, meta)
    gap, partial = dates[80], dates[81]
    assert panel.loc[gap, FIELD].tolist() == [3080.1234567890123] * 2
    assert panel.loc[partial, FIELD].tolist() == [3081.1234567890123] * 2
    assert panel.loc[gap, ['open', 'high', 'low', 'close', 'raw_close', 'vol', 'amount', 'adj_factor']].isna().all().all()
    assert pd.isna(panel.loc[(partial, '000001.SZ'), 'close'])
    assert panel.loc[dates[100:102], FIELD].isna().all()  # missing source row and explicit null
    assert audit['contextGridRestoredValues'] == {FIELD: 3}
    assert audit['contextObservedDates'] == {FIELD: 198}
    assert audit['contextSourceRoot'] == meta['contextSourceRoot']
    assert audit['contextSourceAuthentication'] == 'not_established_by_content_hash'
    assert AMOUNT not in panel  # only selected context fields restored


def test_source_only_date_change_changes_fingerprint_even_when_stock_rows_identical(frozen):
    frame, meta, strategy = frozen
    original, _, before = _prepare_data(frame, strategy, meta)
    changed = copy.deepcopy(meta)
    changed['contextSources'][0]['records'][80]['close'] += 0.0000000001
    rehash(changed)
    other, _, after = _prepare_data(frame, strategy, changed)
    assert before['dataSha256'] != after['dataSha256']
    assert before['calendarSha256'] == after['calendarSha256']
    assert not original[FIELD].equals(other[FIELD])


def test_reserved_context_cannot_downgrade_to_unarchived_upload(frozen):
    frame, meta, strategy = frozen
    plain = {k: v for k, v in meta.items() if not k.startswith('context')}
    with pytest.raises(ProviderError): validate(frame, plain, strategy)
    with pytest.raises(ResearchError): _prepare_data(frame, strategy, plain)
    with pytest.raises(ProviderError):
        validate_upload(strategy, {'rows': _exact_records(frame), 'provenance': plain})


def test_generic_unarchived_external_input_remains_only_observed_broadcast(frozen):
    frame, meta, strategy = frozen
    name = 'ext_manual_market'
    frame = frame.drop(columns=[AMOUNT, AMOUNT+'__available_date']).rename(columns={FIELD:name, FIELD+'__available_date':name+'__available_date'})
    meta = {k:v for k,v in meta.items() if not k.startswith('context')}
    mapping=meta['externalFields'][FIELD].copy();mapping['availableDateColumn']=name+'__available_date'
    meta['externalFields']={name:mapping}
    strategy['factors'][0]['expression']=name
    panel,dates,audit=_prepare_data(frame,strategy,meta)
    assert panel.loc[dates[80],name].isna().all()
    assert 'contextSourceRoot' not in audit


def test_snapshot_json_and_bundle_encoding_reconstruct_identical_fingerprint(frozen):
    frame, meta, strategy = frozen
    snapshot = {'rows': _exact_records(frame), 'provenance': meta}
    restored = json.loads(encode(snapshot))
    panel, dates, audit = _prepare_data(frame, strategy, meta)
    other, other_dates, other_audit = _prepare_data(pd.DataFrame(restored['rows']), strategy, restored['provenance'])
    pd.testing.assert_frame_equal(panel, other)
    assert dates == other_dates and audit == other_audit
    assert meta['contextSources'][0]['records'][0]['close'] == 3000.1234567890123


def test_upload_retains_frozen_grid_without_authenticating_user_package(frozen):
    frame, meta, strategy = frozen
    uploaded, provenance = validate_upload(strategy, {'rows': _exact_records(frame), 'provenance': meta})
    assert provenance['source'] == 'USER_UPLOAD'
    assert provenance['classification'] == 'SYNTHETIC_USER_UPLOAD_UNVERIFIED'
    assert provenance['contextSourceRoot'] == meta['contextSourceRoot']
    panel, dates, audit = _prepare_data(uploaded, strategy, provenance)
    assert panel.loc[dates[80], FIELD].notna().all()
    assert audit['contextSourceRoot'] == meta['contextSourceRoot']


@pytest.mark.parametrize('mutation', [
    lambda p: p.pop('contextSourceRoot'),
    lambda p: p.update(contextSourceRoot='0'*64),
    lambda p: p.update(contextScope='fill_previous'),
    lambda p: p.update(contextObservationClock='before_daily_publication'),
    lambda p: p['contextSources'][0].update(sha256='0'*64),
    lambda p: p['contextSources'][0]['records'][0].update(close=99),
])
def test_source_hash_and_clock_tampering_rejected(frozen, mutation):
    frame, meta, strategy = frozen
    mutation(meta)
    with pytest.raises(ProviderError): validate(frame, meta, strategy)


@pytest.mark.parametrize('mutation', [
    lambda s: s.update(api='daily'),
    lambda s: s.update(extra='unrecognized'),
    lambda s: s.update(historicalRevisionVerified=True),
    lambda s: s['params'].update(ts_code='600000.SH'),
    lambda s: s['params'].update(start_date='20230103'),
    lambda s: s['params'].update(trade_date='20230102'),
    lambda s: s['fields'].append('open'),
    lambda s: s['fields'].reverse(),
    lambda s: s['records'][0].update(ts_code='000001.SH'),
    lambda s: s['records'][0].update(extra=1),
    lambda s: s['records'][0].update(trade_date='20230101'),
    lambda s: s['records'][0].update(trade_date='20230107'),
    lambda s: s['records'].append(s['records'][0]),
    lambda s: s['records'][0].update(close=True),
    lambda s: s['records'][0].update(close='3000'),
    lambda s: s['records'][0].update(close=-1),
    lambda s: s['records'][0].update(amount=-1),
    lambda s: s['records'][0].update(close=None),
])
def test_well_hashed_semantically_invalid_source_rejected(frozen, mutation):
    frame, meta, strategy = frozen
    mutation(meta['contextSources'][0]); rehash(meta)
    with pytest.raises(ProviderError): validate(frame, meta, strategy)


@pytest.mark.parametrize('column,value', [(FIELD, 3001.), (FIELD, None), (AMOUNT, 999.),
                                         (FIELD+'__available_date', '20230101')])
def test_every_stock_broadcast_and_unselected_archived_field_checked(frozen, column, value):
    frame, meta, strategy = frozen
    frame.loc[1, column] = value  # second member, not the first broadcast row
    with pytest.raises(ProviderError): validate(frame, meta, strategy)
    with pytest.raises(ResearchError): _prepare_data(frame, strategy, meta)


def test_missing_broadcast_cannot_be_silently_backfilled(frozen):
    frame, meta, strategy = frozen
    frame.loc[1, FIELD] = None
    frame.loc[1, FIELD+'__available_date'] = None
    with pytest.raises(ProviderError): validate(frame, meta, strategy)


def test_external_mapping_and_nonfinite_source_rejected(frozen):
    frame, meta, strategy = frozen
    meta['externalFields'][FIELD]['path'] = 'index_daily/000001.SH/close'
    with pytest.raises(ProviderError): validate(frame, meta, strategy)
    meta['contextSources'][0]['records'][80]['close'] = float('inf')
    with pytest.raises(ProviderError): validate(frame, meta, strategy)


def test_upload_integer_float_hash_must_match_archive_numeric_canonical(frozen):
    frame, meta, strategy = frozen
    original = copy.deepcopy(meta)
    # The value is unchanged, but a hash over 100.0 tokens is not archive-stable.
    meta['contextSources'][0]['records'][0]['amount'] = 100.0
    rehash(meta)
    with pytest.raises(ProviderError): validate(frame, meta, strategy)
    # A sender may spell a numeric input as 100.0 only when its declared hashes
    # already commit to canonical 100. Never replace a different declared root.
    meta['contextSources'] = copy.deepcopy(original['contextSources'])
    meta['contextSources'][0]['records'][0]['amount'] = 100.0
    meta['contextSourceRoot'] = original['contextSourceRoot']
    result = validate(frame, meta, strategy)
    assert result['contextSourceRoot'] == original['contextSourceRoot']
    assert type(result['contextSources'][0]['records'][0]['amount']) is int
