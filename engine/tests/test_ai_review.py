import json
from pathlib import Path
import pytest
from atlas_quant.statistical_quant.ai_review import development_packet, validate_decision, verify_events, ReviewRuntime


def packet():
    return {'schema':'factor-model-review-input/1','developmentStart':'20200101','developmentEnd':'20201230','candidateSetHash':'a'*64,'admissibleCandidateIds':['no_change:0'],'defaultCandidateId':'no_change:0','target':'equal_date_joint_normalized_mse','outerOrTerminalDataIncluded':False,'trials':[{'id':'no_change:0','status':'valid','estimator':'no_change','params':{},'score':.1,'foldScoreHeuristicSE':.01,'folds':[{'testStart':'20201001','testEnd':'20201230','trainStart':'20200101','trainEnd':'20200920','labelEndMax':'20200925','trainRows':150,'trainDates':150}]}],'reserveCandidates':[{'id':'ridge:2','estimator':'ridge','params':{'alpha':100}}]}


def decision():
    return {'candidateId':'no_change:0','needsRevision':True,'refinementCandidateIds':['ridge:2'],'reason':'No inner-validation advantage','issues':[],'nextResearch':[]}


def test_packet_does_not_leak_other_report_fields():
    data=packet();data.update(outerFolds=[{'mse':999}],terminalRows=[{'price':999}],token='secret')
    clean=development_packet(data)
    assert not any(k in clean for k in ('outerFolds','terminalRows','token'))
    data['trials'][0]['folds'][0]['testEnd']='20210101'
    with pytest.raises(ValueError,match='TIME_BOUNDARY'):development_packet(data)


def test_ai_cannot_propose_arbitrary_refinements():
    good=decision();assert validate_decision(good,['no_change:0'],['ridge:2'])==good
    good['refinementCandidateIds']=['run_arbitrary_code']
    with pytest.raises(ValueError,match='REFINEMENT_SCOPE'):validate_decision(good,['no_change:0'],['ridge:2'])


def test_no_configuration_is_explicit_and_never_spawns(tmp_path):
    runtime=ReviewRuntime(root=tmp_path,enabled=False,executable='/never/execute')
    value=runtime.review_candidates(packet())
    assert value['receipt']['status']=='not_configured' and runtime.calls==0
    assert list(tmp_path.iterdir())==[]


def test_event_stream_rejects_tool_use_and_missing_completion(tmp_path):
    file=tmp_path/'events.jsonl'
    file.write_text(json.dumps({'type':'turn.completed'})+'\n')
    assert verify_events(file)['completedTextOnlyTurn']
    file.write_text(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'true'}})+'\n'+json.dumps({'type':'turn.completed'}))
    with pytest.raises(ValueError,match='TOOL_USE'):verify_events(file)
    file.write_text(json.dumps({'type':'turn.started'})+'\n')
    with pytest.raises(ValueError,match='COMPLETION'):verify_events(file)


def test_unknown_outcome_is_not_retried_at_max(tmp_path,monkeypatch):
    runtime=ReviewRuntime(root=tmp_path,enabled=True,executable='codex')
    efforts=[]
    def unknown(*args):efforts.append(args[1]);raise TimeoutError()
    monkeypatch.setattr(runtime,'_invoke',unknown)
    assert runtime.review_candidates(packet())['receipt']['status']=='failed_or_unknown_no_retry'
    assert efforts==['high']


def test_high_then_max_shares_fixed_call_budget(tmp_path,monkeypatch):
    runtime=ReviewRuntime(root=tmp_path,enabled=True,executable='codex')
    def invoke(*args):runtime.calls+=1;return decision(),{'effort':args[1],'status':'completed'}
    monkeypatch.setattr(runtime,'_invoke',invoke)
    output=runtime.review_candidates(packet());assert [x['effort'] for x in output['receipt']['calls']]==['high','max']
    assert output['refinementCandidateIds']==['ridge:2']
    assert runtime.review_candidates(packet())['receipt']['status']=='call_budget_exhausted'


def test_invoke_uses_isolated_transport_after_durable_intent(tmp_path, monkeypatch):
    from atlas_quant.statistical_quant import ai_review
    root = tmp_path / 'review'
    observed = []

    def transport(executable, directory, prompt, schema, **options):
        assert (directory / 'intent.json').is_file()
        assert directory.stat().st_mode & 0o077 == 0
        assert options == {'model': 'gpt-6.1-sol', 'effort': 'high', 'timeout': 150}
        assert 'outerFolds' not in prompt and 'PRIVATE_OUTER_VALUE' not in prompt
        assert 'untrusted data' in prompt
        observed.append(options)
        value = decision()
        value['needsRevision'] = False
        return json.dumps(value), {'schema':'factor-model-app-server-transport/1',
            'toolCallsObserved':0,'completedTextOnlyTurn':True,
            'boundary':{'executionEnvironments':[], 'completeToolInventoryVerified':False}}

    monkeypatch.setattr(ai_review, 'run_isolated_review', transport)
    data = packet()
    data['outerFolds'] = ['PRIVATE_OUTER_VALUE']
    result = ReviewRuntime(root=root, enabled=True, executable='codex').review_candidates(data)
    assert len(observed) == 1
    record = result['receipt']['calls'][0]
    assert record['transport']['boundary']['executionEnvironments'] == []
    assert record['transport']['boundary']['completeToolInventoryVerified'] is False
    directory = next(root.iterdir())
    assert json.loads((directory / 'decision.json').read_text())['candidateId'] == 'no_change:0'
    assert (directory / 'confirmed.json').is_file()
