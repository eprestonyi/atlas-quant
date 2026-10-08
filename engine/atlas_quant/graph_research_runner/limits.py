"""Independent parent enforcement while graph restoration or a fit is running."""
import math
import shutil
import subprocess
import time
from ..dataset_runner.protocol import decode,require
from .spool import GraphResearchSpool


class GraphProcessBudget:
    def __init__(self, context):
        self.store=GraphResearchSpool(context)
        self.next_check=0.0
        self.progress_enabled=True

    @classmethod
    def for_composer(cls,store):
        budget=cls.__new__(cls)
        budget.store=store
        budget.next_check=0.0
        budget.progress_enabled=False
        return budget

    def check(self,pid):
        now=time.monotonic()
        if now<self.next_check:return
        self.next_check=now+.2
        try:
            state=subprocess.run(['ps','-o','rss=','-p',str(pid)],capture_output=True,text=True,timeout=2)
            require(state.returncode==0 and bool(state.stdout.strip()),'CAPACITY_MONITOR')
            rss=int(state.stdout.strip())*1024
            require(rss>=0,'CAPACITY_MONITOR')
        except (OSError,ValueError,subprocess.TimeoutExpired):
            require(False,'CAPACITY_MONITOR')
        require(rss<=3*1024**3,'CAPACITY_MEMORY')
        try:free=shutil.disk_usage(self.store.root).free
        except OSError:require(False,'CAPACITY_MONITOR')
        require(free>=500*1024**2,'CAPACITY_DISK')
        path=self.store.root/'progress.enc'
        if self.progress_enabled and (path.exists() or path.is_symlink()):
            event=decode(self.store.read('progress'),limit=262144)
            require(isinstance(event,dict),'CAPACITY_MONITOR')
            if event.get('phase')=='fit_started':
                started=event.get('startedMonotonic')
                # ps/disk IO may overlap a new child fit. Compare the decoded
                # event with a fresh observation, never the pre-IO poll clock.
                observed=time.monotonic()
                require(type(started) in (int,float) and math.isfinite(started) and 0<=started<=observed,'CAPACITY_MONITOR')
                require(observed-started<=300,'CAPACITY_FIT_TIMEOUT')
