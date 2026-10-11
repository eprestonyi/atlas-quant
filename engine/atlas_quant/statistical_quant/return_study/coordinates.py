"""Per-input time coordinates and conservative daily availability boundaries.

These dates are *policies*, not fabricated provider reception timestamps.
Declared financial/external alignment does not establish historical vintages.
"""
from __future__ import annotations
from bisect import bisect_left
import copy
import ast
import numpy as np
import pandas as pd

from ...factors import validate_expression
from ...context_sources import context_field
from ..schema import fail

MAX_INPUT_CELLS = 500_000


def target_relationships(strategy, descriptors):
    """Recognize explicit own-target reexpressions, not arbitrary algebra.

Absence of a match is not proof that an expression is free of the target.
Other securities and composite nonlinear DSL remain outside this detector.
"""
    h = strategy['target']['horizonSessions']
    key = lambda expression: ast.dump(ast.parse(expression, mode='eval'), include_attributes=False)
    direct = {key(expression) for expression in (f'returns(close,{h})',
        f'close/lag(close,{h})-1', f'delta(close,{h})/lag(close,{h})')}
    logged = {key(expression) for expression in (f'delta(log(close),{h})',
        f'log(close/lag(close,{h}))', f'log(close)-log(lag(close,{h}))')}
    result = {}
    for factor, desc in zip(strategy['factors'], descriptors):
        match = None
        if desc['timing']['offsetSessions'] == h:
            expression, transform = key(factor['expression']), desc['transform']
            if transform['kind'] == 'identity':
                match = 'same_asset_response_simple_return' if expression in direct else 'same_asset_response_log_return' if expression in logged else None
            elif factor['expression'].strip() == 'close' and transform.get('lag') == h:
                match = {'simple_return':'same_asset_response_simple_return', 'log_return':'same_asset_response_log_return'}.get(transform['kind'])
            elif expression == key('log(close)') and transform.get('kind') == 'first_difference' and transform.get('lag') == h:
                match = 'same_asset_response_log_return'
        result[desc['feature']] = {'classification': 'target_derived' if match else 'not_identified',
            'recognizedForm': match, 'detector': 'explicit_own_adjusted_close_interval_forms_only',
            'absenceProvesTargetIndependence': False}
    return result


def shift_inputs(values, descriptors):
    return {item['feature']: values[item['feature']].shift(-item['timing']['offsetSessions'])
            for item in descriptors}


def _source_dates(panel, factor, descriptor):
    if descriptor['clock'] != 'observed_source_sessions_asof':
        return None
    retained = panel.attrs.get('atlas_context_source_series')
    fields = validate_expression(factor['expression'])['fields']
    rows = {row[0]: row for row in getattr(retained, 'series', ())}
    if not fields or any(field not in rows for field in fields):
        fail('MISSING_CONTEXT_SOURCE_CLOCK', '海外因子的可用性证据缺少原始来源日期')
    return rows[fields[0]][3]


def build_evidence(panel, dates, strategy, descriptors):
    """Freeze coordinate and source-clock policy; never infer a revision vintage."""
    if len(dates)*len(strategy['universe']['symbols'])*len(descriptors) > MAX_INPUT_CELLS:
        fail('RETURN_INPUT_EVIDENCE_BUDGET', '完整逐因子时点面板超过500000个单元，请缩短日期或减少输入')
    result = {}
    for factor, desc in zip(strategy['factors'], descriptors):
        fields = validate_expression(factor['expression'])['fields']
        unknown = any(field.startswith(('fd_', 'model_fin_')) or
                      field.startswith('ext_') and context_field(field) is None for field in fields)
        native = _source_dates(panel, factor, desc)
        items = []
        for t, origin in enumerate(dates):
            j = t+desc['timing']['offsetSessions']
            evaluation = dates[j] if 0 <= j < len(dates) else None
            source = None if unknown else evaluation
            if native is not None and evaluation is not None:
                i = bisect_left(native, evaluation)-1
                source = native[i] if i >= 0 else None
            items.append({'valueOrigin': 'observed_actual', 'evaluationDate': evaluation,
                # Upper bound from the snapshot's admitted daily alignment.
                # Not the provider's timestamp, nor proof of vintage history.
                'availableAt': evaluation, 'sourceDate': source,
                'availabilityBasis': 'declared_data_alignment' if unknown else
                    'native_source_session_policy' if native is not None else 'research_session_policy',
                'knownAtOrigin': None if evaluation is None or unknown else evaluation <= origin,
                'status': 'outside_available_calendar' if evaluation is None else 'observed'})
        result[desc['feature']] = items
    return result


def row_evidence(all_evidence, features, t, origin, endpoint, descriptors):
    evidence = {name: copy.deepcopy(items[t]) for name, items in all_evidence.items()}
    reason = None
    for desc in descriptors:
        name = desc['feature']
        item = evidence[name]
        lead = desc['timing']['offsetSessions'] > 0
        if item['evaluationDate'] is None:
            reason = reason or ('future_input_outside_available_calendar' if lead else 'factor_window_outside_available_calendar')
        elif not np.isfinite(features[name]):
            item['status'] = 'missing_observation'
            if lead:
                reason = reason or 'future_input_unavailable'
    dates = [e['availableAt'] for e in evidence.values()]
    available = max(dates) if dates and all(d is not None for d in dates) else None
    ready = max(available, endpoint) if available is not None and endpoint is not None else None
    if reason:
        usage = 'unresolved_input'
    elif any(e['availableAt'] is not None and e['availableAt'] > origin for e in evidence.values()):
        usage = 'conditional_realized'
    elif any(e['knownAtOrigin'] is None for e in evidence.values()):
        usage = 'availability_unverified'
    else:
        usage = 'origin_known_by_policy'
    return {'inputEvidence': evidence, 'inputAvailableAt': available, 'sampleReadyAt': ready,
            'availabilityClass': usage}, reason


def attach_coordinates(samples, panel, dates, strategy, descriptors):
    all_evidence = build_evidence(panel, dates, strategy, descriptors)
    rows = []
    for i, row in samples.meta.iterrows():
        additions, reason = row_evidence(all_evidence, samples.X.loc[i], int(row.dateIndex), row.date,
                                        row.responseEndDate, descriptors)
        rows.append(additions)
        if reason:
            samples.meta.at[i, 'inputValid'] = False
            samples.meta.at[i, 'invalidReason'] = reason
    for key in ('inputEvidence', 'inputAvailableAt', 'sampleReadyAt', 'availabilityClass'):
        samples.meta[key] = pd.Series([row[key] for row in rows], index=samples.meta.index, dtype=object)
    return samples
