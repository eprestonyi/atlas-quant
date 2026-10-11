"""F5 inference provenance is caller-declared, never source-verified evidence."""
from __future__ import annotations
import math
from ..model_function import _bad, _date, _vector


def evidence_classes(artifact, rows, origin_dates, input_evidence):
    names = [entry['name'] for entry in artifact['inputSchema']]
    offsets = {entry['feature']: entry['timing']['offsetSessions'] for entry in artifact['featureConstruction']['inputs']}
    if origin_dates is not None:
        if not isinstance(origin_dates, list) or len(origin_dates) != len(rows):
            _bad('originDates must align with rows')
        for date in origin_dates:
            _date(date)
    if input_evidence is not None and (not isinstance(input_evidence, list) or len(input_evidence) != len(rows)):
        _bad('inputEvidence must align with rows')
    classes = []
    for index, row in enumerate(rows):
        # An unavailable future input is not an ordinary missing observation.
        if any(offsets[name] > 0 and row[name] is None for name in names):
            _bad('future input requires a finite actual or explicit scenario value')
        if input_evidence is None:
            classes.append('availability_unverified')
            continue
        evidence = input_evidence[index]
        if not isinstance(evidence, dict) or set(evidence) != set(names):
            _bad('input evidence must exactly match the retained feature schema')
        origin = origin_dates[index] if origin_dates is not None else None
        scenario, unknown, future = False, origin is None, False
        for name, item in evidence.items():
            if not isinstance(item, dict) or set(item) != {'valueOrigin', 'evaluationDate', 'availableAt'} or item['valueOrigin'] not in {'observed_actual', 'scenario_assumption'}:
                _bad('invalid input evidence declaration')
            evaluation, available = item['evaluationDate'], item['availableAt']
            for date in (evaluation, available):
                if date is not None:
                    _date(date)
            if origin is not None and evaluation is not None:
                offset = offsets[name]
                if ((offset == 0 and evaluation != origin) or (offset > 0 and evaluation <= origin)
                        or (offset < 0 and evaluation >= origin)):
                    _bad('evaluation date disagrees with factor time-coordinate direction')
            if item['valueOrigin'] == 'scenario_assumption':
                if row[name] is None:
                    _bad('a scenario assumption must supply a finite value')
                scenario = True
            else:
                if evaluation is not None and available is not None and available < evaluation:
                    _bad('observed input availability precedes its evaluation boundary')
                unknown |= evaluation is None or available is None
                future |= origin is not None and available is not None and available > origin
        classes.append('conditional_scenario' if scenario else 'conditional_realized' if future else
                       'availability_unverified' if unknown else 'origin_known_declared')
    return classes


def response_document(artifact, values, origin_price, origin_volatility, classes, evidence_supplied):
    norm = artifact['featureConstruction']['targetSpecification']['normalization']
    normalized = norm['kind'] == 'trailing_volatility'
    if not normalized and origin_volatility is not None:
        _bad('simple returns do not accept volatility conversion')
    if normalized and origin_price is not None and origin_volatility is None:
        _bad('restoring a conditional price requires origin-known volatility')
    if origin_price is not None:
        _vector(origin_price, len(values))
        if any(value <= 0 for value in origin_price):
            _bad('origin prices must be positive')
    if origin_volatility is not None:
        _vector(origin_volatility, len(values))
        if any(value <= norm['minimum'] for value in origin_volatility):
            _bad('origin volatility is insufficient')
    result = {'artifactId': artifact['artifactId'], 'predictedResponse': values,
        'outputUnit': artifact['outputs'][0], 'evidenceStatus': artifact['lineage']['status'],
        'inputEvidenceStatus': 'caller_declared' if evidence_supplied else 'not_supplied',
        'availabilityClasses': classes}
    if not normalized or origin_volatility is not None:
        result['simpleReturns'] = [value*(origin_volatility[i]*math.sqrt(artifact['scope']['horizonSessions']) if normalized else 1)
                                   for i, value in enumerate(values)]
        _vector(result['simpleReturns'], len(values))
        if origin_price is not None:
            result['conditionalPrices'] = [p*(1+r) for p, r in zip(origin_price, result['simpleReturns'])]
            _vector(result['conditionalPrices'], len(values))
    return result
