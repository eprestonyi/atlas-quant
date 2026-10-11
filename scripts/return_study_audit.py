"""Independent scalar-return identities. Standard library only; never fits F."""
import math
import ast
import json
import hashlib
import struct
from bisect import bisect_left
from pathlib import Path

COORDINATE_KEYS = {'inputEvidence', 'inputAvailableAt', 'sampleReadyAt', 'availabilityClass'}
EVIDENCE_KEYS = {'valueOrigin', 'evaluationDate', 'availableAt', 'sourceDate',
                 'availabilityBasis', 'knownAtOrigin', 'status'}
QUANTITIES = json.loads((Path(__file__).resolve().parents[1]/'engine/atlas_quant/factor_quantity_fields.json').read_text())['fields']


def coordinate_contract(strategy, dates, snapshot=None):
    """Independent declaration/source lookup, with no engine transformation helpers."""
    sources = {(s['api'], s['params']['ts_code']): s for s in (snapshot or {}).get('provenance', {}).get('contextSources', [])}
    inputs = []
    for factor in strategy['factors']:
        tree = ast.parse(factor['expression'], mode='eval')
        functions = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        fields = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in functions}
        unknown = any(f.startswith(('fd_', 'model_fin_')) or f.startswith('ext_') and
                      not QUANTITIES.get(f, {}).get('sourceIdentity') for f in fields)
        native = any(QUANTITIES.get(f, {}).get('foreign') for f in fields)
        source_dates = None
        if native and snapshot is not None:
            identities = {QUANTITIES[f]['sourceIdentity'] for f in fields if f in QUANTITIES and QUANTITIES[f].get('foreign')}
            if len(identities) != 1:
                raise ValueError('Native input source identity is not unique')
            api, symbol = next(iter(identities)).split(':')
            source = sources.get((api, symbol))
            if source is None:
                raise ValueError('Native input missing independently frozen source')
            source_dates = sorted({r['trade_date'] for r in source['records']})
        timing = factor.get('timing', {})
        if set(timing) != {'anchor', 'offsetSessions'} or timing['anchor'] != 'target_origin' or type(timing['offsetSessions']) is not int or abs(timing['offsetSessions']) > 252:
            raise ValueError('Invalid instance time coordinate')
        if not isinstance(factor.get('baseFactorId'), str) or not factor['baseFactorId']:
            raise ValueError('Missing base factor identity')
        inputs.append({'name': 'factor:'+factor['id'], 'offset': timing['offsetSessions'],
            'basis': 'declared_data_alignment' if unknown else 'native_source_session_policy' if native else 'research_session_policy',
            'native': native, 'sourceDates': source_dates})
    return {'dates': dates, 'inputs': inputs, 'horizon': strategy['target']['horizonSessions']}


def check_return_coordinates(row, contract, require):
    """Reject internally consistent but false origin-known/availability claims."""
    dates, inputs = contract['dates'], contract['inputs']
    require(row['date'] in dates, 'Input origin outside frozen calendar')
    t = dates.index(row['date'])
    endpoint = dates[t+contract['horizon']] if t+contract['horizon'] < len(dates) else None
    require(row['responseStartDate'] == row['date'] and row['responseEndDate'] == endpoint,
            'Declared h does not match source calendar')
    evidence = row.get('inputEvidence')
    require(isinstance(evidence, dict) and set(evidence) == {x['name'] for x in inputs}, 'Input evidence instance mismatch')
    unresolved, conditional, unknown, available = False, False, False, []
    for item in inputs:
        e, j = evidence[item['name']], t+item['offset']
        evaluation = dates[j] if 0 <= j < len(dates) else None
        require(isinstance(e, dict) and set(e) == EVIDENCE_KEYS, 'Input evidence fields mismatch')
        require(e['valueOrigin'] == 'observed_actual' and e['evaluationDate'] == evaluation and
                e['availableAt'] == evaluation and e['availabilityBasis'] == item['basis'],
                'Input offset/availability policy mismatch')
        known = None if evaluation is None or item['basis'] == 'declared_data_alignment' else evaluation <= row['date']
        require(e['knownAtOrigin'] is known, 'Conditional or unverified input falsely declared origin-known')
        if evaluation is None:
            require(e['status'] == 'outside_available_calendar' and e['sourceDate'] is None,
                    'Out-of-calendar input has invented observation')
            unresolved = True
        else:
            require(e['status'] in {'observed', 'missing_observation'}, 'Invalid input observation status')
            if item['basis'] == 'declared_data_alignment':
                require(e['sourceDate'] is None, 'Unverified alignment invented a source observation date')
            elif not item['native']:
                require(e['sourceDate'] == evaluation, 'Research-session source date mismatch')
            elif item['sourceDates'] is not None:
                i = bisect_left(item['sourceDates'], evaluation)-1
                require(e['sourceDate'] == (item['sourceDates'][i] if i >= 0 else None), 'Frozen native source date mismatch')
            else:
                require(e['sourceDate'] is None or isinstance(e['sourceDate'], str) and e['sourceDate'] < evaluation,
                        'Native source is not prior to research-session evaluation')
            if e['sourceDate'] is None and item['basis'] != 'declared_data_alignment':
                require(e['status'] == 'missing_observation', 'Missing source invented observed input')
            unresolved |= item['offset'] > 0 and e['status'] == 'missing_observation'
        if 'features' in row:
            value = row['features'][item['name']]
            finite = type(value) in (int, float) and math.isfinite(value)
            require((e['status'] == 'observed') == finite, 'Input evidence and factor value differ')
        available.append(e['availableAt'])
        conditional |= e['availableAt'] is not None and e['availableAt'] > row['date']
        unknown |= e['knownAtOrigin'] is None
    latest = max(available) if available and all(x is not None for x in available) else None
    ready = max(latest, endpoint) if latest is not None and endpoint is not None else None
    usage = 'unresolved_input' if unresolved else 'conditional_realized' if conditional else 'availability_unverified' if unknown else 'origin_known_by_policy'
    require(row['inputAvailableAt'] == latest and row['sampleReadyAt'] == ready and row['availabilityClass'] == usage,
            'Input maturity or conditional classification mismatch')
    require(not unresolved or row['inputValid'] is False and row.get('status') != 'valid',
            'Unresolved shifted input was admitted')


def check_return_fit(fit, require, strategy=None):
    """Training must wait for both labels and all shifted inputs."""
    if fit.get('status') != 'valid':
        return
    require(all(isinstance(fit.get(k), str) for k in ('inputAvailableAtMax', 'sampleReadyAtMax', 'labelEndMax', 'fitDate')),
            'Timed fit missing maturity boundaries')
    require(fit['sampleReadyAtMax'] == max(fit['inputAvailableAtMax'], fit['labelEndMax']) < fit['fitDate'],
            'Fit used immature shifted inputs or labels')
    require(fit.get('informationCutoff') == fit['fitDate'], 'Fit date and information cutoff differ')
    artifact = fit.get('functionArtifact')
    require(isinstance(artifact, dict) and artifact.get('schema') == 'atlas-model-function/5', 'Timed fit missing F5')
    require(artifact.get('scope', {}).get('studyProtocol') == 'asset-return-study/2' and
            'studyMode' not in artifact['scope'], 'F5 has incompatible study scope')
    # Independently implement the published binary64 hash encoding, not the
    # engine's function digest. Transport hashes alone cannot pin the inner F.
    def canonical(value):
        if value is None or isinstance(value, (str,bool)):
            return value
        if type(value) in (int,float):
            require(math.isfinite(value) and (type(value) is not int or int(float(value)) == value), 'Invalid F5 number')
            return {'$f64':struct.pack('>d',float(value) if value else 0.).hex()}
        if isinstance(value,list):
            return [canonical(x) for x in value]
        require(isinstance(value,dict) and all(isinstance(k,str) and k.isascii() and k != '$f64' for k in value), 'Invalid F5 hash object')
        return {k:canonical(v) for k,v in value.items()}
    payload={k:v for k,v in artifact.items() if k != 'artifactId'}
    digest=hashlib.sha256(json.dumps(canonical(payload),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    require(artifact.get('hashAlgorithm') == 'sha256-canonical-f64-json/1' and artifact.get('artifactId') == digest, 'F5 inner content hash mismatch')
    require(artifact.get('lineage') == {'parentArtifactId':None,'status':'fitted'}, 'Archive fit cannot claim edited-function evidence')
    for key in ('inputAvailableAtMax', 'sampleReadyAtMax', 'labelEndMax', 'informationCutoff', 'trainStart', 'trainEnd', 'trainRows', 'trainDates'):
        require(artifact.get('training', {}).get(key) == fit.get(key), 'F5 training differs from enclosing fit')
    require(artifact['scope'].get('symbols') == [fit.get('targetSymbol')], 'F5 uses another asset scope')
    construction=artifact.get('featureConstruction',{})
    require(construction.get('inputs') == fit.get('returnInputDescriptors'), 'F5 inputs differ from enclosing fit')
    if strategy is not None:
        require(construction.get('factors') == strategy['factors'] and construction.get('preprocess') == strategy['preprocess'] and
                construction.get('targetSpecification') == strategy['target'], 'F5 construction differs from research declaration')


def check_return_row(row, strategy, near, require):
    study = strategy['research']['returnStudy']
    timed = study.get('schema') == 'asset-return-study/2'
    require(study == {'schema': 'asset-return-study/2'} if timed else study.get('schema') == 'asset-return-study/1', 'Invalid return study protocol')
    require(row.get('schema') == ('asset-return-observation/2' if timed else 'asset-return-observation/1'), 'Missing scalar return record version')
    require(timed or not COORDINATE_KEYS.intersection(row), 'Legacy return record contains new evidence')
    require(not any(k in row for k in ('entryDate', 'expectedEntry', 'expectedFuture', 'expectedGrossPnl')),
            'Scalar research contains fabricated execution output')
    require(row['featureDate'] == row['date'], 'Feature date mismatch')
    cutoff = ((max(row['date'],row['inputAvailableAt'])+'_AFTER_CLOSE') if row.get('inputAvailableAt') is not None else None) if timed else row['date']+'_AFTER_CLOSE'
    require(row.get('informationCutoff') == cutoff, 'Return information cutoff differs from consumed input availability')
    mode = 'forecast' if timed else study['mode']
    start, end = row['responseStartDate'], row['responseEndDate']
    require((mode == 'forecast' and start == row['date']) or
            (mode == 'association' and end == row['date']), 'Return interval/mode mismatch')
    if start is not None and end is not None:
        require(start < end, 'Return interval must increase')
    if row['labelMaturedAt'] is not None:
        require(row['labelMaturedAt'] == end, 'Return maturity is not interval end')
    if row['status'] != 'valid':
        return
    require(row['originPrice'] > 0 and row['responseScale'] > 0, 'Invalid return origin or scale')
    near(row['predictedReturn'], row['predictedResponse'] * row['responseScale'], 'Return inverse scale')
    near(row['conditionalPrice'], row['originPrice'] * (1 + row['predictedReturn']), 'Conditional price inverse')
    if row['observedResponse'] is not None:
        near(row['observedReturn'], row['observedResponse'] * row['responseScale'], 'Observed return scale')
        near(row['responseResidual'], row['observedResponse'] - row['predictedResponse'], 'Response residual')


def check_return_source(rows, strategy, snapshot, near, require):
    """Reconstruct targets/scales using independent price arithmetic from raw frozen rows."""
    prices = {(r['trade_date'], r['ts_code']): r['close'] for r in snapshot['rows']}
    supplied = snapshot.get('provenance', {}).get('tradingDates')
    dates = sorted(set(supplied if supplied else (r['trade_date'] for r in snapshot['rows'])))
    dates = [d for d in dates if strategy['universe']['start'] <= d <= strategy['universe']['end']]
    positions = {d: i for i, d in enumerate(dates)}
    h = strategy['target']['horizonSessions']
    timed = strategy['research']['returnStudy']['schema'] == 'asset-return-study/2'
    mode = 'forecast' if timed else strategy['research']['returnStudy']['mode']
    coordinates = coordinate_contract(strategy, dates, snapshot) if timed else None
    normalization = strategy['target']['normalization']
    for row in rows:
        if coordinates:
            check_return_coordinates(row, coordinates, require)
        i = positions[row['date']]
        start_i, end_i = (i, i + h) if mode == 'forecast' else (i - h, i)
        start = dates[start_i] if start_i >= 0 else None
        end = dates[end_i] if end_i < len(dates) else None
        require(row['responseStartDate'] == start and row['responseEndDate'] == end,
                'Declared h does not match source calendar')
        symbol = row['assetSymbol']
        origin = prices.get((start, symbol))
        future = prices.get((end, symbol))
        if origin is not None and row.get('originPrice') is not None:
            near(row['originPrice'], origin, 'Source origin close')
        if origin is not None and future is not None and row.get('observedReturn') is not None:
            near(row['observedReturn'], future / origin - 1, 'Source close-close return')
        scale = 1.0
        if normalization['kind'] == 'trailing_volatility':
            window = normalization['windowSessions']
            scale = None
            if start_i >= window:
                history = [prices.get((dates[j], symbol)) for j in range(start_i - window, start_i + 1)]
                if all(isinstance(x, (int, float)) and x > 0 and math.isfinite(x) for x in history):
                    returns = [right / left - 1 for left, right in zip(history, history[1:])]
                    mean = sum(returns) / window
                    sigma = math.sqrt(sum((r - mean) ** 2 for r in returns) / (window - 1))
                    if sigma > normalization['minimum']:
                        scale = sigma * math.sqrt(h)
        if row.get('responseScale') is not None:
            require(scale is not None, 'Unavailable volatility was invented')
            near(row['responseScale'], scale, 'Source origin-known volatility scale')
