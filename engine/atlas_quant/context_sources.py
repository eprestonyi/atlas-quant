"""Named market/industry index inputs, independent of the output stock universe.

The identity list is a frozen catalogue, not historical constituents or coverage.
Index series are broadcast by date, never inferred from the selected stock pool.
"""
from __future__ import annotations

import json
import copy
import math
from pathlib import Path

import numpy as np
import pandas as pd

REGISTRY = json.loads(Path(__file__).with_suffix('.json').read_text())
COMMON_FIELDS = {'close': ('收盘点位', 'index_points'), 'vol': ('成交量', None),
                 'amount': ('成交额', None)}
SECTOR_FIELDS = {'pe': ('市盈率', 'ratio'), 'pb': ('市净率', 'ratio'),
                 'total_mv': ('总市值', 'CNY_10000'), 'float_mv': ('流通市值', 'CNY_10000')}


def field_registry():
    result = []
    for source in REGISTRY['items']:
        fields = {**COMMON_FIELDS, **(SECTOR_FIELDS if source['api'] == 'sw_daily' else {})}
        for field, (label, unit) in fields.items():
            unit = unit or ({'vol': 'shares_10000', 'amount': 'CNY_10000'} if source['api'] == 'sw_daily'
                            else {'vol': 'hands', 'amount': 'CNY_thousands'})[field]
            alias = 'ext_ctx_' + source['ts_code'].lower().replace('.', '_') + '_' + field
            result.append({**source, 'id': alias, 'field': field, 'name': source['name']+' · '+label,
                           'unit': unit, 'dataType': 'number', 'numericEligible': True,
                           'source': 'TUSHARE_PRO', 'dataset': source['api'],
                           'availabilityStatus': 'adapter_supported_requires_observations',
                           'availability': 'after_daily_provider_publication_before_next_open',
                           'minimumLagSessions': 0, 'revisionHistoryVerified': False,
                           'syntheticSupported': False})
    return result


FIELDS = {row['id']: row for row in field_registry()}


def context_field(field):
    return FIELDS.get(field)


def _canonical_source(value):
    """Match bundle JSON integer spelling without rounding binary64 values."""
    if isinstance(value, dict):
        return {key: _canonical_source(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_source(item) for item in value]
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def summarize_context_provenance(provenance):
    """Report metadata only; the unchanged full sources stay in the snapshot."""
    if 'contextSources' not in provenance:
        return provenance
    return {**provenance, 'contextSources': [
        {key: source[key] for key in ('api', 'params', 'fields', 'sha256')}
        | {'rowCount': len(source['records'])} for source in provenance['contextSources']]}


def validate_context_sources(frame, provenance, aliases, dates, start, end):
    """Validate a frozen independent grid without authenticating its submitter.

    A source package may have a wider original request than the current research.
    Within the current window its records must follow the supplied trading grid.
    Existing asset rows must exactly agree with the original broadcast, including
    missing values; the package cannot silently overwrite different observations.
    """
    from .provider import ProviderError, canonical_hash, parse_date

    def require(condition, message):
        if not condition:
            raise ProviderError('CONTEXT_SOURCE_INVALID', message)

    keys = {'contextSources', 'contextSourceRoot', 'contextScope', 'contextObservationClock'}
    selected = sorted(set(aliases).intersection(FIELDS))
    if not keys.intersection(provenance):
        require(not selected, '登记指数因子必须保留完整独立来源包；自定义数值请使用普通 ext_ 字段。')
        return {}
    require(keys.issubset(provenance), '独立指数来源包不完整。')
    require(provenance['contextScope'] == 'named_index_series_broadcast_by_date'
            and provenance['contextObservationClock'] == 'after_daily_publication_before_next_open',
            '独立指数来源时间或广播口径无效。')
    sources = _canonical_source(provenance['contextSources'])
    require(isinstance(sources, list) and 1 <= len(sources) <= 16, '独立指数来源数量无效。')
    try:
        root = canonical_hash(sources)
    except (TypeError, ValueError):
        raise ProviderError('CONTEXT_SOURCE_INVALID', '独立指数来源须为有限数值 JSON。') from None
    require(root == provenance['contextSourceRoot'], '独立指数来源根哈希不一致。')
    date_set = set(dates)
    source_fields, order = {}, []
    for source in sources:
        require(isinstance(source, dict) and set(source) == {
            'api', 'params', 'fields', 'records', 'sha256', 'classification',
            'notWireBytes', 'historicalRevisionVerified'}, '独立指数来源结构无效。')
        require(source['classification'] == 'PARSED_PROVIDER_RESPONSE'
                and source['notWireBytes'] is True and source['historicalRevisionVerified'] is False,
                '独立指数来源证据分类无效。')
        params = source['params']
        require(isinstance(params, dict) and set(params) == {'ts_code', 'start_date', 'end_date'},
                '独立指数来源请求字段无效。')
        api, code = source['api'], params['ts_code']
        require(isinstance(api, str) and isinstance(code, str), '独立指数来源身份无效。')
        registered = {r['field']: r for r in FIELDS.values() if r['api'] == api and r['ts_code'] == code}
        require(bool(registered) and (api, code) not in order, '独立指数来源未登记或重复。')
        order.append((api, code))
        parse_date(params['start_date']); parse_date(params['end_date'])
        require(params['start_date'] <= start <= end <= params['end_date'], '独立指数来源请求未覆盖研究区间。')
        fields, records = source['fields'], source['records']
        require(isinstance(fields, list) and all(isinstance(f, str) for f in fields)
                and len(fields) >= 3 and fields[:2] == ['ts_code', 'trade_date']
                and fields[2:] == sorted(set(fields[2:])) and set(fields[2:]).issubset(registered),
                '独立指数来源字段与登记身份不一致。')
        require(isinstance(records, list) and 1 <= len(records) <= 4000,
                '独立指数来源记录数量无效。')
        require(source['sha256'] == canonical_hash(records), '独立指数来源记录哈希不一致。')
        previous, by_date = None, {}
        for row in records:
            require(isinstance(row, dict) and set(row) == set(fields) and row.get('ts_code') == code,
                    '独立指数来源记录字段或身份无效。')
            date = row['trade_date']; parse_date(date)
            require((previous is None or previous < date)
                    and params['start_date'] <= date <= params['end_date']
                    and (not start <= date <= end or date in date_set),
                    '独立指数来源日期重复、越界或不在研究交易日历内。')
            previous = date
            for field in fields[2:]:
                value = row[field]
                require(value is None or (type(value) in {int, float} and math.isfinite(value)),
                        '独立指数来源字段须为有限数值或缺失。')
                require(value is None or (field != 'close' or value > 0)
                        and (field not in {'vol', 'amount', 'total_mv', 'float_mv'} or value >= 0),
                        '独立指数来源字段单位或取值无效。')
            by_date[date] = row
        for field in fields[2:]:
            source_fields[registered[field]['id']] = (registered[field], by_date)
    require(order == sorted(order), '独立指数来源顺序无效。')
    require(set(selected).issubset(source_fields), '所选指数字段缺少独立来源。')
    mappings = provenance.get('externalFields')
    require(isinstance(mappings, dict), '独立指数来源缺少外部字段登记。')
    # Check every archived context field that is actually present, even when it
    # is not selected by the current model. Never validate just the first stock.
    for alias in sorted(set(source_fields).intersection(frame.columns) | set(selected)):
        spec, by_date = source_fields[alias]
        companion = alias + '__available_date'
        expected_meta = {'source': 'TUSHARE_PRO', 'path': spec['api']+'/'+spec['ts_code']+'/'+spec['field'],
                         'dataType': 'number', 'unit': spec['unit'],
                         'availabilityPolicy': 'point_in_time_asof', 'availableDateColumn': companion}
        require(alias in frame and companion in frame and mappings.get(alias) == expected_meta,
                '独立指数来源与已声明外部字段不一致。')
        for date, actual, available in frame[['trade_date', alias, companion]].itertuples(index=False, name=None):
            expected = by_date.get(date, {}).get(spec['field'])
            require((pd.isna(actual) and expected is None)
                    or (type(actual) in {int, float} and not isinstance(actual, bool)
                        and math.isfinite(actual) and actual == expected),
                    '独立指数来源与现有股票行广播数值不一致。')
            require((expected is None and (pd.isna(available) or available == ''))
                    or (expected is not None and available == date),
                    '独立指数来源与现有股票行可用日期不一致。')
    return copy.deepcopy({**{key: provenance[key] for key in keys}, 'contextSources': sources})


def restore_context_fields(panel, frame, provenance, aliases, dates, start, end):
    """Restore only registered global fields; never fill stock prices or volume."""
    evidence = validate_context_sources(frame, provenance, aliases, dates, start, end)
    if not evidence:
        return {}
    sources = {(s['api'], s['params']['ts_code']): s for s in evidence['contextSources']}
    restored, observations = {}, {}
    grid_dates = panel.index.get_level_values('trade_date')
    for alias in sorted(set(aliases).intersection(FIELDS)):
        spec = FIELDS[alias]
        source = sources[(spec['api'], spec['ts_code'])]
        daily = {row['trade_date']: row[spec['field']] for row in source['records']}
        values = pd.Series(grid_dates.map(daily), index=panel.index, dtype=float)
        restored[alias] = int((panel[alias].isna() & values.notna()).sum())
        observations[alias] = sum(daily.get(date) is not None for date in dates)
        panel[alias] = values
    return {'contextSourceRoot': evidence['contextSourceRoot'],
            'contextGridRestoredValues': restored, 'contextObservedDates': observations,
            'contextSourceAuthentication': 'not_established_by_content_hash'}


def load_context_fields(client, frame, aliases, dates, start, end):
    """Fetch each named index once, validate its identity and join exact dates.

    Parsed source rows are preserved separately from broadcast observations.
    No forward fill, current-constituent proxy, name guessing or synthetic fallback.
    """
    from .provider import ProviderError, canonical_hash, _exact_records, parse_date
    aliases = sorted(set(aliases))
    if not aliases or any(alias not in FIELDS for alias in aliases):
        raise ProviderError('CONTEXT_FIELD_UNKNOWN', '指数因子需使用已登记的数据来源。')
    sources = {(FIELDS[a]['api'], FIELDS[a]['ts_code']) for a in aliases}
    if len(sources) > 16:
        raise ProviderError('CONTEXT_SOURCE_LIMIT', '一次研究最多使用16个独立指数来源。')
    result, mappings, snapshots = frame.copy(), {}, []
    date_set = set(dates)
    for api, code in sorted(sources):
        requested = [a for a in aliases if FIELDS[a]['api'] == api and FIELDS[a]['ts_code'] == code]
        params = {'ts_code': code, 'start_date': start, 'end_date': end}
        raw = client.call(api, params)
        needed = ['ts_code', 'trade_date'] + sorted({FIELDS[a]['field'] for a in requested})
        if raw.empty or not set(needed).issubset(raw):
            raise ProviderError('CONTEXT_DATA_MISSING', '所选指数未返回所需字段：'+code)
        raw = raw[needed].copy()
        if set(raw.ts_code) != {code} or raw.trade_date.duplicated().any():
            raise ProviderError('CONTEXT_IDENTITY', '指数行情身份重复或与请求不一致。')
        for date in raw.trade_date:
            parse_date(date)
            if date not in date_set or not start <= date <= end:
                raise ProviderError('CONTEXT_DATE', '指数行情日期不在研究交易日历内。')
        for field in needed[2:]:
            values = raw[field]
            if values.map(lambda x: not pd.isna(x) and (isinstance(x, (bool, str)) or not isinstance(x, (float, int, np.number)))).any():
                raise ProviderError('CONTEXT_NUMERIC', '指数行情字段须为有限数值或缺失。')
            raw[field] = pd.to_numeric(values, errors='raise').astype(float)
            if np.isinf(raw[field]).any() or (field == 'close' and (raw[field].dropna() <= 0).any()) or (field in {'vol', 'amount', 'total_mv', 'float_mv'} and (raw[field].dropna() < 0).any()):
                raise ProviderError('CONTEXT_NUMERIC', '指数行情字段单位或取值无效。')
        raw = raw.sort_values('trade_date')
        records = _exact_records(raw)
        # Match snapshot/bundle JSON integer normalization without rounding any
        # nonintegral binary64 observation. This also canonicalizes signed zero.
        records = _canonical_source(records)
        source_hash = canonical_hash(records)
        snapshots.append({'api': api, 'params': params, 'fields': needed, 'records': records,
                          'sha256': source_hash, 'classification': 'PARSED_PROVIDER_RESPONSE',
                          'notWireBytes': True, 'historicalRevisionVerified': False})
        by_date = raw.set_index('trade_date')
        for alias in requested:
            spec = FIELDS[alias]
            result[alias] = result.trade_date.map(by_date[spec['field']])
            companion = alias+'__available_date'
            result[companion] = result.trade_date.where(result[alias].notna(), None)
            mappings[alias] = {'source': 'TUSHARE_PRO', 'path': api+'/'+code+'/'+spec['field'],
                               'dataType': 'number', 'unit': spec['unit'],
                               'availabilityPolicy': 'point_in_time_asof', 'availableDateColumn': companion}
    return result, {'externalFields': mappings, 'contextSources': snapshots,
                    'contextSourceRoot': canonical_hash(snapshots),
                    'contextScope': 'named_index_series_broadcast_by_date',
                    'contextObservationClock': 'after_daily_publication_before_next_open'}
