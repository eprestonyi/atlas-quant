"""Sixth service: graph queue/claims/recovery are independent of legacy compose."""
from functools import partial
from ..dataset_runner import service as workflow,delivery
from ..dataset_runner.lease import LeaseMonitor
from .client import GraphDatasetClient
from .spool import GraphDatasetSpool
from .compute import execute_bounded
from .protocol import CAPABILITY,NAMESPACE,JOB_KIND


def validate_claim(response,state,base):
    return workflow.validate_claim(response,state,base,namespace=NAMESPACE,job_kind=JOB_KIND)


def run_once(config,spool,client,heartbeat_client,*,stop_requested=None,bounded_compute=execute_bounded):
    return workflow.run_once(config,spool,client,heartbeat_client,stop_requested=stop_requested,
        bounded_compute=bounded_compute,capability=CAPABILITY,claim_validator=validate_claim,
        terminal_reader=partial(delivery.terminal_status,dataset_version=3),
        failure_settler=partial(delivery.settle_failure,dataset_version=3),
        delivery=partial(delivery.deliver,dataset_version=3),
        monitor_class=partial(LeaseMonitor,capability=CAPABILITY),
        rejection_handler=lambda store,state,error:store.preserve_rejection(state,error))


def serve(config,*,once=False,stop_requested=None,client_factory=GraphDatasetClient):
    return workflow.serve(config,once=once,stop_requested=stop_requested,client_factory=client_factory,
        enabled_key='graph_dataset_enabled',spool_type=GraphDatasetSpool,capability=CAPABILITY,iteration=run_once)
