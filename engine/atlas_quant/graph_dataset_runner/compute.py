"""Graph composition has independent parent wall/RSS/disk supervision."""
import resource
import shutil
import sys
import time
from ..dataset_runner.compute import execute_bounded as bounded
from ..dataset_runner.protocol import require
from ..graph_research_runner.limits import GraphProcessBudget
from .spool import GraphDatasetSpool
from .publication import compute_publication


def guarded_publication(job,inputs,write_part):
    # Parent samples while raw financial formulas/graph codecs are running.
    result=compute_publication(job,inputs,write_part)
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
    require(peak<=3*1024**3,'CAPACITY_MEMORY')
    require(shutil.disk_usage(write_part.__self__.root).free>=500*1024**2,'CAPACITY_DISK')
    return result


def execute_bounded(spool,job,inputs,monitor,*,slot_path=None):
    require(isinstance(spool,GraphDatasetSpool),'DATASET_SPOOL_PATH')
    require(isinstance(slot_path,str) and bool(slot_path),'COMPUTE_SLOT_CONFIG')
    require(shutil.disk_usage(spool.root).free>=500*1024**2,'CAPACITY_DISK')
    return bounded(spool,job,inputs,monitor,computer=guarded_publication,slot_path=slot_path,
        spool_type=GraphDatasetSpool,process_budget=GraphProcessBudget.for_composer(spool))
