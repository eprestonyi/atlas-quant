"""A matching saved plan cannot conceal missing source-derived origins."""
from copy import deepcopy
import pytest
from atlas_quant.research_dataset.graph_v3.coverage import source_plan,verify_source_coverage
from atlas_quant.research_dataset.graph_v3.snapshot import RESEARCH_PROFILE
from atlas_quant.research_dataset.codec import encode
from test_research_dataset_components import sources,long_sources
from test_snapshot_market_view import legacy_source
from test_financial_graph_snapshot import snapshots


class SavedRows:
    def __init__(self,plan):
        self.plan=deepcopy(plan);self.collections={'baselineRows':{}}
        self.data={'targets':deepcopy(plan['targetDefinitions']),'plannedOrigins':deepcopy(plan['origins']),
                   'forecasts':deepcopy(plan['origins']),'baselineRows':deepcopy(plan['origins'])}
        self.manifest={'documents':{key:{'parts':[{'literal':encode(value).decode()}]} for key,value in {
            'forecast':{'diagnostics':{'holdoutStart':plan['holdoutStart'],'holdoutEnd':plan['holdoutEnd'],
                'factorIncrement':{'baselineValidation':{'holdoutStart':plan['holdoutStart'],'holdoutEnd':plan['holdoutEnd']}}}},
            'report':{'validation':{'holdoutStart':plan['holdoutStart'],'holdoutEnd':plan['holdoutEnd']}},
            'coverage':{'holdoutStart':plan['holdoutStart']}}.items()}}
    def rows(self,name):yield from self.data[name]


def test_independent_source_domain_clock_and_tail_are_not_saved_plan_inputs(snapshots):
    config,reader,_,_,_,admitted=snapshots
    p=source_plan(config,admitted,reader.manifest['scope'],research_profile=RESEARCH_PROFILE)
    assert len(p['origins'])==41 and p['holdoutStart']=='20241105'
    assert p['origins'][-1]['date']=='20241231' and p['origins'][-1]['entryDate'] is None
    assert sum(x['targetDate'] is None for x in p['origins'])==6
    saved=SavedRows(p)
    assert verify_source_coverage(saved,config,admitted,reader.manifest['scope'],research_profile=RESEARCH_PROFILE)['origins']==41
    # Research sample cadence stays anchored to warmup, not the holdout boundary.
    altered=deepcopy(config);altered['research']['observationDays']=3
    q=source_plan(altered,admitted,reader.manifest['scope'],research_profile=RESEARCH_PROFILE)
    assert q['origins'][0]['date']=='20241107'


@pytest.mark.parametrize('attack',['drop_origin','duplicate_origin','skip_tail','clock','validity','target','baseline'])
def test_coordinated_self_consistent_saved_arrays_still_fail_source_domain(snapshots,attack):
    config,reader,_,_,_,admitted=snapshots
    expected=source_plan(config,admitted,reader.manifest['scope'],research_profile=RESEARCH_PROFILE);saved=SavedRows(expected)
    if attack in ('drop_origin','skip_tail'):
        for name in ('plannedOrigins','forecasts','baselineRows'):saved.data[name].pop(0 if attack=='drop_origin' else -1)
    if attack=='duplicate_origin':
        for name in ('plannedOrigins','forecasts','baselineRows'):saved.data[name][1]=deepcopy(saved.data[name][0])
    if attack=='clock':
        for name in ('plannedOrigins','forecasts','baselineRows'):saved.data[name][0]['targetDate']='20241114'
    if attack=='validity':saved.data['plannedOrigins'][0]['inputValid']=not saved.data['plannedOrigins'][0]['inputValid']
    if attack=='target':saved.data['targets'][0]['quantities']=[2.0]
    if attack=='baseline':saved.collections={}
    with pytest.raises(ValueError,match='[Ss]ource|[Ss]aved|[Tt]arget|baseline'):
        verify_source_coverage(saved,config,admitted,reader.manifest['scope'],research_profile=RESEARCH_PROFILE)
