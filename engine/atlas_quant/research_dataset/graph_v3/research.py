"""Explicit local same-child source restoration and automatic F acceptance path."""
from copy import deepcopy
import resource,shutil,sys,time
from pathlib import Path
from threadpoolctl import threadpool_limits

from ...engine import _prepare_data
from ...capacity.core import FitRuntime
from ...statistical_quant.targets import build_samples
from ...statistical_quant.validation import forecast_origins
from ...statistical_quant.models import candidates
from ...statistical_quant.core import _research_from_samples
from ..codec import require
from .snapshot import (validate_research_profile,restore_graph_for_research,freeze_graph_input)


def run_graph_research(strategy,reader,authorized_registry,dataset_ref,*,research_profile,work_dir,
                       plan_sink=None,progress=None):
    """No providers/queues/execution; supervisor must also enforce live RSS/wall.

    Snapshot and full source bounds are checked before any model fit. The exact
    same nested/terminal F code is used for primary and factor-free branches.
    """
    strategy=validate_research_profile(strategy,reader.manifest['scope'],research_profile=research_profile)
    work_dir=Path(work_dir);started=time.monotonic()
    def check():
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
        require(peak<=3*1024**3,'GRAPH_RESEARCH_RSS','Local graph profile exceeded 3 GiB RSS')
        require(time.monotonic()-started<=900,'GRAPH_RESEARCH_WALL','Local graph profile exceeded 900 seconds')
        require(shutil.disk_usage(work_dir).free>=500*1024**2,'GRAPH_RESEARCH_DISK','Local graph profile requires 500 MiB free reserve')
    check()
    if progress:progress({'phase':'source_recomposition'})
    result=restore_graph_for_research(strategy,reader,authorized_registry,research_profile=research_profile)
    check()
    snapshot=freeze_graph_input(strategy,result,dataset_ref,manifest_bytes=reader.manifest_bytes,
                                research_profile=research_profile,profile=reader.profile)
    check()
    panel,dates,audit=_prepare_data(result.data,strategy,result.provenance)
    with threadpool_limits(limits=1):samples=build_samples(panel,dates,strategy)
    _,origins=forecast_origins(samples,strategy)
    dates_count=int(samples.meta.loc[origins,'date'].nunique())
    candidate_set=candidates('auto');branches=2 if any(c.startswith('factor:') for c in samples.X) else 1
    maximum=branches*((strategy['validation']['outerFolds']+1)*strategy['validation']['innerFolds']*len(candidate_set)
                      +strategy['validation']['outerFolds']+dates_count)
    require(branches==2 and len(candidate_set)==8,'GRAPH_RESEARCH_PROFILE','Complete eight-candidate plus factor-free selection required')
    if progress:progress({'phase':'selection_predeclared','candidates':candidate_set,'factorFreeBaselineRequired':True,
        'maximumFitAttempts':maximum,'terminalOriginDates':dates_count,'terminalOrigins':len(origins),
        'jointPairBudget':256,'perFitWallSeconds':300,'logicalJoined':result.logical_joined})
    runtime=FitRuntime(check,maximum,progress)
    report=_research_from_samples(strategy,panel,dates,audit,deepcopy(result.provenance),samples,plan_sink=plan_sink,runtime=runtime)
    check()
    if progress:progress({'phase':'research_complete','fitCount':len(runtime.events)})
    return report,snapshot,tuple(runtime.events)
