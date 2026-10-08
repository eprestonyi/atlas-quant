"""Bounded provider-free child; complete encrypted manifest is its commit marker."""

import multiprocessing
import re
import time

from ..compute_slot import compute_slot
from ..runner import RunnerError
from .protocol import require
from .spool import DatasetSpool
from .source_spool import read_sources, store_sources


def safe_error(error):
    code = str(getattr(error, "code", "DATASET_COMPUTE_FAILED"))
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code):
        code = "DATASET_COMPUTE_FAILED"
    return {"code": code, "message": "数据集任务未完成；请按错误代码检查冻结来源。"}


def _child(connection, context, computer, slot_path, deadline, spool_type=DatasetSpool):
    try:
        if computer is None:
            from .publication import compute_publication

            computer = compute_publication
        spool = spool_type.from_context(context)
        publication = spool.publication(context["job"])

        def check():
            require(time.monotonic() < deadline, "DATASET_DEADLINE")

        # The parent continues heartbeats and kills this child on cancellation.
        # Waiting for the host slot consumes this job's original fixed budget.
        with compute_slot(slot_path, deadline=deadline, check=check):
            check()
            inputs = read_sources(spool, context["job"], check)
            manifest = computer(context["job"], inputs, publication.write_chunk)
            check()
            publication.finalize(manifest)
        connection.send({"complete": True})
    except BaseException as error:
        connection.send({"error": safe_error(error)})
    finally:
        connection.close()


def execute_bounded(spool, job, inputs, monitor, *, computer=None, slot_path=None, spool_type=DatasetSpool, process_budget=None):
    # A child that fails before unpickling must not block Process.start while a
    # parent writes tens of MiB to its bootstrap pipe. Pass only a small context.
    store_sources(spool, job, inputs, monitor.check)
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_child,
        args=(child, spool.context(job), computer, slot_path, monitor.deadline, spool_type),
        daemon=True,
    )
    try:
        monitor.check()
        process.start()
        child.close()
        while True:
            monitor.check()
            if process_budget is not None and process.is_alive():
                try:
                    process_budget.check(process.pid)
                except RunnerError as error:
                    if error.code != "CAPACITY_MONITOR" or process.is_alive():
                        raise
                    # Only process disappearance may race a committed final reply.
            if parent.poll(min(0.1, max(0, monitor.deadline - time.monotonic()))):
                try:
                    answer = parent.recv()
                except EOFError:
                    answer = None
                if isinstance(answer, dict) and answer.get("error"):
                    raise RunnerError(
                        answer["error"]["code"], answer["error"]["message"]
                    )
                require(answer in (None, {"complete": True}), "DATASET_CHILD_EXIT")
                require(
                    spool.publication(job).manifest() is not None, "DATASET_CHILD_EXIT"
                )
                monitor.check()
                return
            if not process.is_alive():
                require(
                    spool.publication(job).manifest() is not None, "DATASET_CHILD_EXIT"
                )
                monitor.check()
                return
    finally:
        child.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        parent.close()
