"""Full asset/session panel and causal single-output close-to-close responses."""
from __future__ import annotations
import copy
import numpy as np
import pandas as pd
from ...factors import evaluate_expression
from ..targets import Samples
from ..schema import MAX_SAMPLES, digest, fail
from ..preprocessing import _context_panel, transform_values
from .contract import input_descriptors, outputs


def evaluate_inputs(panel, dates, symbols, strategy):
    descriptors = input_descriptors(strategy)
    # Apply temporal DSL on the full declared session grid, never a compressed
    # sequence that silently bridges a missing market row.
    attributes = panel.attrs
    panel = panel.reindex(pd.MultiIndex.from_product([dates, symbols], names=["trade_date", "ts_code"]))
    panel.attrs = attributes
    evaluated, global_panel = _context_panel(panel, dates, symbols, strategy['factors'])
    values = {}
    for factor, item in zip(strategy['factors'], descriptors):
        if item['scope'] == 'global':
            if item['clock'] == 'observed_source_sessions_asof':
                from ...context_factor_clock import evaluate_source_clock_factor
                result = evaluate_source_clock_factor(panel, dates, factor, item['transform'])
            else:
                raw = evaluate_expression(factor['expression'], global_panel, mask_asset_availability=False).unstack('ts_code').reindex(index=dates)
                result = transform_values(raw, item['transform']).iloc[:, 0]*factor['direction']
            values[item['feature']] = pd.DataFrame({symbol: result for symbol in symbols}, index=dates)
        else:
            raw = evaluate_expression(factor['expression'], evaluated).unstack('ts_code').reindex(index=dates, columns=symbols)
            values[item['feature']] = transform_values(raw, item['transform'])*factor['direction']
    return values, descriptors


def build_samples(panel, dates, strategy):
    dates, symbols = list(dates), sorted(strategy['universe']['symbols'])
    if not dates or len(dates)*len(symbols) > MAX_SAMPLES:
        fail('FORECAST_BUDGET', '完整日期与证券面板超过资源上限；请缩短窗口或筛选集合')
    close = panel.close.unstack('ts_code').reindex(index=dates, columns=symbols)
    close = close.where(np.isfinite(close) & (close > 0))
    values, descriptors = evaluate_inputs(panel, dates, symbols, strategy)
    h = strategy['target']['horizonSessions']
    norm, mode = strategy['target']['normalization'], strategy['research']['returnStudy']['mode']
    sigma = None
    if norm['kind'] == 'trailing_volatility':
        daily = close/close.shift(1)-1
        sigma = daily.rolling(norm['windowSessions'], min_periods=norm['windowSessions']).std(ddof=1)
    definitions = {}
    identities = {}
    for symbol in symbols:
        definition = {'kind': 'asset_return', 'symbols': [symbol], 'unit': outputs(strategy)[0],
                      'construction': 'independent_asset_close_to_close', 'studyMode': mode,
                      'horizonSessions': h, 'normalization': copy.deepcopy(norm)}
        target_id = 'target_'+digest(definition)[:24]
        definitions[target_id] = {'id': target_id, **definition}
        identities[symbol] = target_id
    meta, features, labels = [], [], []
    financial = [f['id'] for f in strategy['factors'] if f.get('role') == 'predictor' and
                 any(prefix in f['expression'] for prefix in ('fd_', 'model_fin_'))]
    events = [f['id'] for f in strategy['factors'] if f.get('role') == 'event']
    for t, date in enumerate(dates):
        start, end = (t, t+h) if mode == 'forecast' else (t-h, t)
        start_date = dates[start] if 0 <= start < len(dates) else None
        end_date = dates[end] if 0 <= end < len(dates) else None
        for symbol in symbols:
            feature = {name: float(table.loc[date, symbol]) for name, table in values.items()}
            p0 = float(close.loc[start_date, symbol]) if start_date else np.nan
            p1 = float(close.loc[end_date, symbol]) if end_date else np.nan
            scale = 1. if norm['kind'] == 'none' else (float(sigma.loc[start_date, symbol])*np.sqrt(h) if start_date else np.nan)
            if norm['kind'] != 'none' and (not np.isfinite(scale) or scale <= norm['minimum']*np.sqrt(h)):
                scale = np.nan
            reason = None
            if start_date is None:
                reason = 'response_window_warmup'
            elif not np.isfinite(p0):
                reason = 'missing_origin_price'
            elif not np.isfinite(scale):
                reason = 'response_normalizer_unavailable'
            elif not any(np.isfinite(v) for v in feature.values()):
                reason = 'no_observed_factor_input'
            elif strategy['model']['family'] == 'event' and not any(np.isfinite(feature.get('factor:'+f, np.nan)) and abs(feature['factor:'+f]) > 1e-12 for f in events):
                reason = 'no_observed_event'
            elif strategy['model']['family'] == 'fundamental' and not any(np.isfinite(feature.get('factor:'+f, np.nan)) for f in financial):
                reason = 'no_observed_fundamental_predictor'
            observed = p1/p0-1 if np.isfinite([p0, p1]).all() else np.nan
            response = observed/scale if np.isfinite([observed, scale]).all() else np.nan
            meta.append({'date': date, 'dateIndex': t, 'targetId': identities[symbol], 'assetSymbol': symbol,
                'featureDate': date, 'responseStartDate': start_date, 'responseEndDate': end_date,
                'targetDate': end_date, 'originPrice': p0, 'responseScale': scale,
                'observedReturn': observed, 'observedResponse': response,
                'inputValid': reason is None, 'invalidReason': reason,
                'isObservation': t % strategy['research']['observationDays'] == 0})
            features.append(feature); labels.append([response])
    automatic = {'schema': 'asset-return-feature-processing/1', 'factors': descriptors}
    return Samples(pd.DataFrame(features).astype(float), pd.DataFrame(labels, columns=['response']),
                   pd.DataFrame(meta), definitions, 0, dates, [], automatic)


def panel_document(samples, symbols):
    rows = []
    for index, row in samples.meta.iterrows():
        rows.append({'schema': 'asset-return-panel-row/1',
            **{name: row[name] for name in ('date', 'targetId', 'assetSymbol', 'inputValid', 'invalidReason',
                'featureDate', 'responseStartDate', 'responseEndDate', 'originPrice', 'responseScale', 'observedReturn', 'observedResponse')},
            'features': samples.X.loc[index].to_dict()})
    return {'schema': 'asset-return-panel/1', 'rowCount': len(rows), 'complete': True,
            'dates': list(samples.dates), 'symbols': sorted(symbols), 'rows': rows}
