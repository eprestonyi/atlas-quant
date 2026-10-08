"""Coverage regenerated from admitted raw sources, independently of saved plans.

This computes numerical inputs/targets but never fits a model or executes trades.
"""
from itertools import zip_longest
from threadpoolctl import threadpool_limits
from ...engine import _prepare_data
from ...statistical_quant.targets import build_samples
from ...statistical_quant.validation import forecast_origins
from ..codec import encode,require
from .snapshot import validate_research_profile


def source_plan(strategy,result,scope,*,research_profile):
    normalized=validate_research_profile(strategy,scope,research_profile=research_profile)
    panel,dates,_=_prepare_data(result.data,normalized,result.provenance)
    with threadpool_limits(limits=1):samples=build_samples(panel,dates,normalized)
    holdout,indices=forecast_origins(samples,normalized)
    rows=[{'date':r.date,'targetId':r.targetId,'entryDate':r.entryDate,'targetDate':r.targetDate,'inputValid':bool(r.inputValid)}
          for r in samples.meta.loc[indices].itertuples()]
    return {'holdoutStart':holdout,'holdoutEnd':dates[-1],'origins':rows,'targetDefinitions':list(samples.definitions.values())}


def verify_source_coverage(reader,strategy,result,scope,*,research_profile):
    expected=source_plan(strategy,result,scope,research_profile=research_profile)
    from ...bundle import document_skeleton,encode as forecast_encode
    forecast=document_skeleton(reader.manifest,'forecast')
    report=document_skeleton(reader.manifest,'report')
    coverage=document_skeleton(reader.manifest,'coverage')
    require(coverage.get('holdoutStart')==forecast.get('diagnostics',{}).get('holdoutStart')==expected['holdoutStart'],
            'GRAPH_SOURCE_COVERAGE','Frozen source calendar and declared holdout differ')
    for diagnostics in (forecast.get('diagnostics',{}),report.get('validation',{}),
                        forecast.get('diagnostics',{}).get('factorIncrement',{}).get('baselineValidation',{})):
        require(diagnostics.get('holdoutStart')==expected['holdoutStart'] and diagnostics.get('holdoutEnd')==expected['holdoutEnd'],
                'GRAPH_SOURCE_COVERAGE','Source-derived report/baseline interval differs')
    expected_targets={x['id']:x for x in expected['targetDefinitions']}
    actual_targets=list(reader.rows('targets'))
    require(len(actual_targets)==len(expected_targets) and {x.get('id') for x in actual_targets}==set(expected_targets) and
            all(forecast_encode(x)==forecast_encode(expected_targets[x['id']]) for x in actual_targets),
            'GRAPH_SOURCE_COVERAGE','Target definitions do not cover the exact frozen scope')
    require('baselineRows' in reader.collections,'GRAPH_SOURCE_COVERAGE','Graph auto requires a complete factor-free baseline')
    n=0
    for wanted,plan,main,baseline in zip_longest(expected['origins'],reader.rows('plannedOrigins'),
                                               reader.rows('forecasts'),reader.rows('baselineRows')):
        require(all(x is not None for x in (wanted,plan,main,baseline)),
                'GRAPH_SOURCE_COVERAGE','Source-derived terminal origin count differs')
        require(encode(wanted)==encode(plan),'GRAPH_SOURCE_COVERAGE','Saved origin/input validity differs from exact source-derived plan')
        for row in (main,baseline):
            require(all(row.get(k)==wanted[k] for k in ('date','targetId','entryDate','targetDate')),
                    'GRAPH_SOURCE_COVERAGE','Saved prediction omits or changes a frozen source origin')
        n+=1
    return {'sourceForecastDomainVerified':True,'inputValidityRecomputed':True,'modelFitted':False,
            'symbols':len(scope['symbols']),'origins':n,'holdoutStart':expected['holdoutStart']}
