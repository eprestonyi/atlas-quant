"""Rejected originals are retained without ever becoming automatic pending jobs."""
import json
import shutil
import sys
import types
import pytest

from atlas_quant import runner
from atlas_quant.delivery_quarantine import preserve,read
from atlas_quant.financial_bundle_spool import FinancialBundleSpool
from atlas_quant.research_dataset_runner.spool import ResearchDatasetSpool
from atlas_quant.runner_claims import ClaimIntent


def config(path):return {'delivery_dir':str(path/'delivery'),'runner_secret':'q'*48,'api_base':'https://queue.test'}


def test_rejection_after_upload_preserves_exact_result_source_and_only_safe_code(tmp_path,monkeypatch):
    from atlas_quant import financial_bundle_spool
    spool=runner.CompletionSpool(config(tmp_path));claims=ClaimIntent(spool);intent=claims.current_or_create()
    identity={'id':'job','leaseToken':'lease'};claims.executing(intent,identity)
    output=FinancialBundleSpool.from_spool(spool,identity);source=ResearchDatasetSpool.from_spool(spool,identity)
    output.write('manifest',b'exact-original-result');source.write('manifest',b'exact-original-source')
    original={**identity,'bundleId':'a'*64,'_claimRequestId':intent['requestId'],'_bundleFormat':'atlas.quant.financial_bundle/1',
              '_bundleKey':output.key,'_datasetKey':source.key}
    spool.write(original);uploads=[]
    def rejected(*args,**kwargs):
        uploads.append(1)
        raise runner.RunnerError('QUEUE_HTTP','secret arbitrary server message',http_status=413,remote_code='BUNDLE_INDEX_LIMIT')
    monkeypatch.setattr(financial_bundle_spool,'deliver_financial_bundle',rejected)
    class Queue:
        calls=[]
        def post(self,route,payload,**kwargs):
            self.calls.append((route,payload))
            if route=='complete':assert set(payload)=={'id','leaseToken','error'};return {'ok':True,'status':'failed'}
            if route=='claim':return {'job':None,'claim':{'requestId':intent['requestId'],'jobId':'job','status':'failed'}}
            pytest.fail('Cannot replay rejected delivery')
    queue=Queue();runner.flush_completions(queue,spool)
    assert uploads==[1] and claims.read() is None and not list(spool.pending())
    saved=read(spool,original)
    assert saved['original']==original and saved['rejection']['serverCode']=='BUNDLE_INDEX_LIMIT'
    assert saved['rejection']['httpStatus']==413 and 'secret arbitrary' not in json.dumps(saved)
    assert output.read('manifest')==b'exact-original-result' and source.read('manifest')==b'exact-original-source'
    assert b'exact-original' not in next((spool.root/'quarantine').glob('*/record.enc')).read_bytes()
    runner.flush_completions(queue,spool);assert uploads==[1]


def test_crash_after_quarantine_before_pending_error_does_not_repost_original(tmp_path):
    spool=runner.CompletionSpool(config(tmp_path));payload={'id':'j','leaseToken':'l','result':{'private':'retained'}}
    spool.write(payload);preserve(spool,payload,runner.RunnerError('QUEUE_HTTP','ignored',http_status=400))
    class Queue:
        calls=[]
        def post(self,route,body):self.calls.append(body);assert 'result' not in body and body['error']['code']=='RESULT_REJECTED';return {'ok':True}
    queue=Queue();runner.flush_completions(queue,spool)
    assert len(queue.calls)==1 and not list(spool.pending()) and read(spool,payload)['original']==payload


@pytest.mark.parametrize('attack',['corruption','permissions','symlink','identity'])
def test_quarantine_tampering_stops_before_network_or_cleanup(tmp_path,attack):
    spool=runner.CompletionSpool(config(tmp_path));payload={'id':'j','leaseToken':'l','result':{'private':'retained'}}
    spool.write(payload);preserve(spool,payload,runner.RunnerError('QUEUE_HTTP','ignored',http_status=400))
    path=next((spool.root/'quarantine').glob('*/record.enc'))
    if attack=='corruption':path.write_bytes(path.read_bytes()[:-1]+b'x')
    elif attack=='permissions':path.chmod(0o644)
    elif attack=='symlink':
        old=path.with_suffix('.old');path.rename(old);path.symlink_to(old)
    else:
        other={**payload,'leaseToken':'other'};preserve(spool,other,runner.RunnerError('QUEUE_HTTP','ignored',http_status=400))
        otherpath=next(p for p in (spool.root/'quarantine').glob('*/record.enc') if p!=path);shutil.copyfile(otherpath,path)
    class Queue:
        def post(self,*args,**kwargs):pytest.fail('Tampered evidence reached queue')
    with pytest.raises(runner.RunnerError,match='隔离'):runner.flush_completions(Queue(),spool)
    assert list(spool.pending()) and path.exists()


@pytest.mark.parametrize('body,expected',[(b'{"error":{"code":"BUNDLE_INDEX_LIMIT","message":"secret"}}','BUNDLE_INDEX_LIMIT'),
    (b'{"error":{"code":"secret lowercase"}}',None),(b'x'*4097,None),(b'not json',None)])
def test_http_rejection_only_keeps_bounded_machine_code(body,expected):
    class Response:
        status_code=413
        def iter_content(self,size):yield body
    error=runner._http_rejection(Response())
    assert error.http_status==413 and error.remote_code==expected and 'secret' not in str(error)


@pytest.mark.parametrize('code,exited,accepted',[('CAPACITY_MEMORY',True,False),('CAPACITY_FIT_TIMEOUT',True,False),
    ('CAPACITY_MONITOR',False,False),('CAPACITY_MONITOR',True,True)])
def test_resource_rejection_never_loses_to_an_already_queued_success(monkeypatch,code,exited,accepted):
    class Budget:
        def __init__(self,*args):pass
        def check(self,*args):raise runner.RunnerError(code,'budget')
    package=types.ModuleType('atlas_quant.market_research_runner');package.__path__=[]
    module=types.ModuleType('atlas_quant.market_research_runner.limits');module.MarketProcessBudget=Budget
    monkeypatch.setitem(sys.modules,'atlas_quant.market_research_runner',package)
    monkeypatch.setitem(sys.modules,'atlas_quant.market_research_runner.limits',module)
    class Pipe:
        calls=0
        def poll(self,*args):self.calls+=1;return self.calls>1
        def recv(self):return {'result':'ready'}
        def close(self):pass
    pipe=Pipe()
    class Process:
        pid=123
        calls=0
        def start(self):pass
        def is_alive(self):self.calls+=1;return self.calls==1 or not exited
        def terminate(self):pass
        def kill(self):pass
        def join(self,**kw):pass
    process=Process()
    class Context:
        def Pipe(self,**kw):return pipe,Pipe()
        def Process(self,**kw):return process
    monkeypatch.setattr(runner.multiprocessing,'get_context',lambda *_:Context())
    result=runner.execute_bounded({'dataSource':'ready_market'},bundle_context={})
    assert result==({'result':'ready'} if accepted else {'error':{'code':code,'message':'budget'}})
