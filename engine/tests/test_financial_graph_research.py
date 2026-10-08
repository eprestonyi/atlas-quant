"""The new bounded local path runs the same F after exact same-child source restoration."""
from atlas_quant import bundle
from atlas_quant.engine import run_research
from atlas_quant.research_dataset.graph_v3.research import run_graph_research
from atlas_quant.research_dataset.graph_v3.snapshot import RESEARCH_PROFILE
from test_research_dataset_components import sources,long_sources
from test_snapshot_market_view import legacy_source
from test_financial_graph_snapshot import snapshots


def test_same_child_source_snapshot_then_full_nested_F_and_runtime_budget(snapshots,long_sources,tmp_path):
    config,reader,snapshot,_,_,admitted=snapshots;events=[];plans=[]
    report,thin,fits=run_graph_research(config,reader,long_sources['registry'],snapshot['datasetRef'],
        research_profile=RESEARCH_PROFILE,work_dir=tmp_path,plan_sink=plans.append,progress=events.append)
    original=run_research(config,admitted.data,admitted.provenance)
    assert bundle.encode(report['forecasts'])==bundle.encode(original['forecasts'])
    assert thin==snapshot
    declared=next(e for e in events if e['phase']=='selection_predeclared')
    assert declared['factorFreeBaselineRequired'] and len(declared['candidates'])==8
    assert len(fits)<=declared['maximumFitAttempts']
    assert events[0]['phase']=='source_recomposition' and events[-1]['phase']=='research_complete'
    assert report['trades']==[] and report['metrics'] is None
    assert len(plans)==1 and len(plans[0]['origins'])==len(report['forecasts']['rows'])
