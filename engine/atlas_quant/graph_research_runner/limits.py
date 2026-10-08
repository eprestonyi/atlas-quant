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
        if path.exists() or path.is_symlink():
            event=decode(self.store.read('progress'),limit=262144)
            require(isinstance(event,dict),'CAPACITY_MONITOR')
            if event.get('phase')=='fit_started':
                started=event.get('startedMonotonic')
                require(type(started) in (int,float) and math.isfinite(started) and 0<=started<=now,'CAPACITY_MONITOR')
                require(now-started<=300,'CAPACITY_FIT_TIMEOUT')
