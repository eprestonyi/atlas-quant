"""Compute foreign-series factors before projecting them onto the China clock.

Only the new factor protocol uses this path. An observed US session is one
observation even when its last known value is broadcast to several CN dates.
The retained provider records are not a verified complete exchange calendar.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from .context_sources import FIELDS, FOREIGN_APIS
from .factors import evaluate_expression, validate_expression


@dataclass(frozen=True)
class NativeContextSeries:
    # alias, api, security, source dates, numeric values; all children immutable.
    series: tuple
    root: str

    def __deepcopy__(self, memo):
        # Pandas propagates attrs to every slice. Copying thousands of frozen
        # source observations for each intermediate Series is unnecessary.
        return self


def attach_native_context(panel, evidence, aliases):
    """Called only after full source/hash/broadcast validation in restoration."""
    sources = {(s['api'], s['params']['ts_code']): s for s in evidence['contextSources']}
    series = []
    for alias in sorted(set(aliases).intersection(FIELDS)):
        spec = FIELDS[alias]
        if spec['api'] not in FOREIGN_APIS:
            continue
        records = sources[(spec['api'], spec['ts_code'])]['records']
        field = 'adj_close' if spec['api'] == 'yfinance_history' and spec['field'] == 'close' else spec['field']
        values = []
        for row in records:
            value = row.get(field)
            if spec['api'] == 'us_daily_adj' and field == 'close':
                adj = row.get('adj_factor')
                value = value * adj if value is not None and adj is not None else None
            values.append(value)
        series.append((alias, spec['api'], spec['ts_code'],
                       tuple(row['trade_date'] for row in records), tuple(values)))
    if series:
        panel.attrs['atlas_context_source_series'] = NativeContextSeries(tuple(series), evidence['contextSourceRoot'])


def evaluate_source_clock_factor(panel, dates, factor, transform):
    """Expression -> economic transform -> strict prior-date as-of broadcast.

The caller must declare observed_source_sessions_asof in the frozen contract.
Mixed source clocks use the explicit research-grid path instead.
"""
    from .statistical_quant.preprocessing import transform_values
    from .statistical_quant.schema import fail

    fields = validate_expression(factor['expression'])['fields']
    retained = panel.attrs.get('atlas_context_source_series')
    if not isinstance(retained, NativeContextSeries):
        fail('MISSING_CONTEXT_SOURCE_CLOCK', '海外因子需要已核对的独立原始时间序列')
    series = {row[0]: row for row in retained.series}
    if not fields or any(field not in series for field in fields):
        fail('MISSING_CONTEXT_SOURCE_CLOCK', '海外因子缺少原始观察时钟')
    rows = [series[field] for field in fields]
    source_dates = rows[0][3]
    if (len({(row[1], row[2]) for row in rows}) != 1
            or any(row[3] != source_dates for row in rows)):
        fail('MIXED_CONTEXT_SOURCE_CLOCK', '原始时钟因子必须来自同一证券与来源')
    index = pd.MultiIndex.from_arrays([source_dates, ['GLOBAL'] * len(source_dates)], names=['trade_date', 'ts_code'])
    native = pd.DataFrame({row[0]: row[4] for row in rows}, index=index, dtype=float)
    raw = evaluate_expression(factor['expression'], native, mask_asset_availability=False).unstack('ts_code')
    values = transform_values(raw, transform).iloc[:, 0].to_numpy() * factor['direction']
    output = []
    for date in dates:
        offset = bisect_left(source_dates, date) - 1
        if offset < 0 or (datetime.strptime(date, '%Y%m%d') - datetime.strptime(source_dates[offset], '%Y%m%d')).days > 7:
            output.append(np.nan)
        else:
            output.append(values[offset])
    return pd.Series(output, index=dates, dtype=float)
