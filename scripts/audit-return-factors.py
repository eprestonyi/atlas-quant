#!/usr/bin/env python3
"""Independent scalar arithmetic for retained raw-field return-study factors.

No engine, numpy, pandas, provider, or fitting imports. This checks the frozen
panel and its descriptive OLS against manual formulas, not predictive validity.
Compound DSL and foreign source-session clocks require their separate oracle.
"""
import argparse
import hashlib
import json
import math
import re
import statistics
from pathlib import Path


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def transformed(values, i, transform):
    value, kind = values[i], transform['kind']
    if not finite(value):
        return None
    if kind == 'identity':
        return value
    if kind == 'log_positive':
        return math.log(value) if value > 0 else None
    if kind == 'log1p_nonnegative':
        return math.log1p(value) if value >= 0 else None
    if kind == 'reciprocal_nonzero':
        return 1/value if value != 0 else None
    if kind == 'percent_to_fraction':
        return value/100
    if kind == 'signed_log1p':
        return math.copysign(math.log1p(abs(value)/transform['referenceUnit']), value)
    if kind in ('simple_return', 'log_return', 'first_difference'):
        earlier = values[i-transform['lag']] if i >= transform['lag'] else None
        if not finite(earlier):
            return None
        if kind == 'first_difference':
            return value-earlier
        if value <= 0 or earlier <= 0:
            return None
        return value/earlier-1 if kind == 'simple_return' else math.log(value)-math.log(earlier)
    if kind == 'return_over_trailing_volatility':
        if i < 21:
            return None
        history = values[i-21:i+1]
        if any(not finite(x) or x <= 0 for x in history):
            return None
        returns = [b/a-1 for a, b in zip(history, history[1:])]
        sigma = statistics.stdev(returns[:-1])
        return returns[-1]/sigma if sigma > 1e-8 else None
    raise ValueError('Unsupported manual arithmetic transform: '+kind)


def audit(report, snapshot):
    forecast, strategy = report['forecasts'], report['strategy']
    assert forecast['studyProtocol'] == 'asset-return-study/1'
    panel = forecast['factorResearch']['panel']
    dates, symbols = panel['dates'], panel['symbols']
    assert len(panel['rows']) == len(dates)*len(symbols)
    indices = {date: i for i, date in enumerate(dates)}
    originals = {(row['trade_date'], row['ts_code']): row for row in snapshot['rows']}
    fits = [fit for fit in forecast['modelFits'] if fit.get('functionArtifact')]
    assert fits, 'No frozen function input contract'
    descriptors = fits[0]['functionArtifact']['featureConstruction']['inputs']
    assert all(fit['functionArtifact']['featureConstruction']['inputs'] == descriptors for fit in fits)
    values, checks, largest = {}, 0, 0.

    def near(got, expected, label):
        nonlocal checks, largest
        if expected is None:
            assert got is None, label+' must be missing'
        else:
            assert finite(got) and finite(expected), label+' must be finite'
            error = abs(got-expected)
            assert error <= 1e-12+1e-10*max(abs(got), abs(expected)), (label, got, expected)
            largest = max(largest, error)
        checks += 1

    for descriptor in descriptors:
        field = descriptor['expression']
        assert re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', field), 'Compound DSL needs a separate independent oracle'
        assert descriptor['clock'] == 'research_sessions', 'Foreign source clock needs a separate independent oracle'
        for symbol in symbols:
            raw = []
            for date in dates:
                if descriptor['scope'] == 'global':
                    observed = {originals.get((date, s), {}).get(field) for s in symbols}
                    observed = {x for x in observed if finite(x)}
                    assert len(observed) <= 1, 'Conflicting global broadcast'
                    raw.append(next(iter(observed)) if observed else None)
                else:
                    raw.append(originals.get((date, symbol), {}).get(field))
            values[(symbol, descriptor['feature'])] = [
                None if (x := transformed(raw, i, descriptor['transform'])) is None else x*descriptor['direction']
                for i in range(len(dates))]
    for row in panel['rows']:
        assert row['assetSymbol'] in symbols and row['date'] in indices
        for descriptor in descriptors:
            name = descriptor['feature']
            near(row['features'][name], values[(row['assetSymbol'], name)][indices[row['date']]], name)

    regressions = []
    holdout = forecast['diagnostics']['holdoutStart']
    for target in forecast['diagnostics']['perTarget']:
        selected = [r for r in panel['rows'] if r['targetId'] == target['targetId'] and r['date'] >= holdout
                    and indices[r['date']] % strategy['research']['observationDays'] == 0
                    and r['inputValid'] and finite(r['observedResponse'])]
        for stat in target['factorDiagnostics']['features']:
            pairs = [(r['features'][stat['name']], r['observedResponse']) for r in selected if finite(r['features'][stat['name']])]
            assert stat['descriptiveFit']['n'] == len(pairs)
            if len(pairs) < 3:
                continue
            xs, ys = zip(*pairs)
            xm, ym = statistics.mean(xs), statistics.mean(ys)
            xx = sum((x-xm)**2 for x in xs)
            yy = sum((y-ym)**2 for y in ys)
            xy = sum((x-xm)*(y-ym) for x, y in pairs)
            if xx <= 1e-20 or yy <= 1e-20:
                continue
            slope, intercept = xy/xx, ym-xy/xx*xm
            residual = sum((y-intercept-slope*x)**2 for x, y in pairs)
            r2 = 1-residual/yy
            for key, expected in [('slope', slope), ('intercept', intercept), ('rSquared', r2)]:
                near(stat['descriptiveFit'][key], expected, stat['name']+' descriptive '+key)
            near(stat['temporalAssociation']['pearson'], xy/math.sqrt(xx*yy), stat['name']+' temporal Pearson')
            regressions.append({'asset': target['targetSymbol'], 'factor': stat['name'], 'rows': len(pairs),
                                'slope': slope, 'intercept': intercept, 'rSquared': r2})
    return {'status': 'passed', 'engineImports': False, 'providerCalls': 0, 'fitsRun': 0,
            'panelRows': len(panel['rows']), 'factorCount': len(descriptors), 'checks': checks,
            'maxAbsoluteError': largest, 'regressions': regressions,
            'meaning': 'frozen raw-field transformations and descriptive OLS arithmetic; not prospective alpha'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path); parser.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    result = audit(json.loads(args.report.read_bytes()), json.loads(args.snapshot.read_bytes()))
    result['reportSha256'] = hashlib.sha256(args.report.read_bytes()).hexdigest()
    result['snapshotSha256'] = hashlib.sha256(args.snapshot.read_bytes()).hexdigest()
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
