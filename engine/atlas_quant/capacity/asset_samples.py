"""Array-based asset targets; same pooled samples/order as the reference builder."""

import os
from pathlib import Path
import shutil
import uuid
import numpy as np
import pandas as pd
from ..factors import validate_expression
from ..statistical_quant.schema import fail
from ..statistical_quant.targets import Samples, _definition
from .panel_store import private_dir, write_json, file_hash, sync_directory


def build_asset_samples(store, features, strategy, workspace, *, max_samples):
    if strategy["target"]["kind"] != "asset_price":
        fail(
            "CAPACITY_PROFILE",
            "Array builder supports only explicit asset_price targets",
        )
    dates, symbols = store.dates, store.symbols
    warmup = max(
        (validate_expression(f["expression"])["lookback"] for f in strategy["factors"]),
        default=0,
    )
    start = max(61, warmup + 1)
    times = list(range(start, len(dates), strategy["research"]["observationDays"]))
    rows = len(times) * len(symbols)
    if not rows or rows > max_samples:
        fail(
            "FORECAST_BUDGET",
            "Complete asset origin plan exceeds profile sample budget",
        )
    target = Path(workspace)
    if target.exists():
        fail("CAPACITY_CACHE", "Samples workspace must be new")
    parent = private_dir(target.parent)
    root = private_dir(parent / ("stage_samples_" + uuid.uuid4().hex))
    try:
        return _write_asset_samples(
            store, features, strategy, root, target, parent, times, rows, start
        )
    finally:
        # root remains the unique staging path, even after the writer publishes
        # target. Never remove the completed target or another call's staging.
        if root.exists():
            shutil.rmtree(root)


def _write_asset_samples(
    store, features, strategy, root, target, parent, times, rows, start
):
    dates, symbols = store.dates, store.symbols
    family = strategy["model"]["family"]
    names = ["volatility20"]
    names += (
        ["state_deviation20", "state_deviation60", "change1"]
        if family in {"mean_reversion", "pair_reversion"}
        else (
            ["trend1", "trend5", "trend20", "trend60"]
            if family == "trend"
            else ["change1", "change5"]
        )
    )
    predictors = [f for f in strategy["factors"] if f["role"] != "hedge"]
    names += ["factor:" + f["id"] for f in predictors]
    for name in ("X.npy", "y.npy"):
        if (root / name).exists():
            fail("CAPACITY_CACHE", "Samples workspace must be new")
    # Reference pandas builds columns contiguously. Preserve that memory order
    # too: downstream reductions/BLAS can otherwise differ by a few ulps.
    X = np.lib.format.open_memmap(
        root / "X.npy",
        mode="w+",
        dtype="<f8",
        shape=(rows, len(names)),
        fortran_order=True,
    )
    y = np.lib.format.open_memmap(
        root / "y.npy", mode="w+", dtype="<f8", shape=(rows, 2), fortran_order=True
    )
    os.chmod(root / "X.npy", 0o600)
    os.chmod(root / "y.npy", 0o600)
    X[:] = np.nan
    y[:] = np.nan
    definitions = [
        _definition("asset_price", [s], [1.0], "single_asset") for s in symbols
    ]
    target_ids = [d["id"] for d in definitions]
    meta = {
        k: []
        for k in (
            "date",
            "dateIndex",
            "targetId",
            "currentState",
            "scale",
            "entryDate",
            "targetDate",
            "realizedEntry",
            "realizedFuture",
            "inputValid",
            "invalidReason",
            "eventObserved",
        )
    }
    close, opens = store.field("close"), store.field("open")
    horizon = strategy["target"]["horizonSessions"]
    financial = {
        "pb",
        "pe",
        "pe_ttm",
        "ps",
        "ps_ttm",
        "dv_ratio",
        "dv_ttm",
        "total_mv",
        "circ_mv",
    }
    fundamental = [
        f["id"]
        for f in predictors
        if f["role"] == "predictor"
        and any(
            x.startswith(("fd_", "pcd_")) or x in financial
            for x in validate_expression(f["expression"])["fields"]
        )
    ]
    fits, last_fit = [], -100000
    for position, t in enumerate(times):
        if t - last_fit >= strategy["model"]["refitDays"]:
            fits.append(
                {
                    "date": dates[t],
                    "informationCutoff": dates[t - 1],
                    "targetIds": target_ids,
                    "status": "valid",
                }
            )
            last_fit = t
        history = np.array(close[t - 60 : t + 1, :].T, order="C", copy=True)
        valid = np.isfinite(history).all(axis=1)
        current = np.where(valid, history[:, -1], np.nan)
        scale = np.abs(current)
        valid &= scale > 1e-12
        changes = np.diff(history, axis=1)
        with np.errstate(all="ignore"):
            columns = [np.std(changes[:, -20:], axis=1, ddof=1) / scale]
            if family in {"mean_reversion", "pair_reversion"}:
                columns += [
                    (history[:, -1] - history[:, -20:].mean(axis=1)) / scale,
                    (history[:, -1] - history[:, -60:].mean(axis=1)) / scale,
                    changes[:, -1] / scale,
                ]
            elif family == "trend":
                columns += [
                    (history[:, -1] - history[:, -1 - h]) / scale
                    for h in (1, 5, 20, 60)
                ]
            else:
                columns += [
                    changes[:, -1] / scale,
                    (history[:, -1] - history[:, -6]) / scale,
                ]
        event = np.zeros(len(symbols), dtype=bool)
        for factor in predictors:
            values = np.asarray(features[factor["id"]][t])
            columns.append(values)
            if factor["role"] == "event":
                event |= np.isfinite(values) & (np.abs(values) > 1e-12)
        matrix = np.column_stack(columns)
        matrix[~valid, :] = np.nan
        event &= valid
        reason = np.full(len(symbols), None, dtype=object)
        reason[~valid] = "incomplete_state_or_formation"
        if family == "event":
            reason[valid & ~event] = "no_observed_event"
        if family == "fundamental":
            available = (
                np.logical_or.reduce(
                    [np.isfinite(features[name][t]) for name in fundamental]
                )
                if fundamental
                else np.zeros(len(symbols), dtype=bool)
            )
            reason[valid & ~available] = "no_observed_fundamental_predictor"
        begin = position * len(symbols)
        end = begin + len(symbols)
        X[begin:end] = matrix
        entry_i, exit_i = t + 1, t + 1 + horizon
        entry_date = dates[entry_i] if entry_i < len(dates) else None
        target_date = dates[exit_i] if exit_i < len(dates) else None
        entry = (
            np.asarray(opens[entry_i]).copy()
            if entry_date
            else np.full(len(symbols), np.nan)
        )
        future = (
            np.asarray(opens[exit_i]).copy()
            if target_date
            else np.full(len(symbols), np.nan)
        )
        entry[~valid] = np.nan
        future[~valid] = np.nan
        y[begin:end] = np.column_stack(
            ((entry - current) / scale, (future - current) / scale)
        )
        for key, value in (
            ("date", [dates[t]] * len(symbols)),
            ("dateIndex", [t] * len(symbols)),
            ("targetId", target_ids),
            ("currentState", current),
            ("scale", scale),
            ("entryDate", [entry_date] * len(symbols)),
            ("targetDate", [target_date] * len(symbols)),
            ("realizedEntry", entry),
            ("realizedFuture", future),
            ("inputValid", valid & (reason == None)),
            ("invalidReason", reason),
            ("eventObserved", event),
        ):
            meta[key].extend(value)
    X.flush()
    y.flush()
    del X, y
    for name in ("X.npy", "y.npy"):
        with (root / name).open("rb") as stream:
            os.fsync(stream.fileno())
    manifest = {
        "version": "asset_samples_float64_v1",
        "panelRoot": store.identity,
        "featureRoot": features.identity,
        "rows": rows,
        "featureNames": names,
        "rowOrder": "date_ascending_then_symbol_ascending",
        "arrays": {
            name: {
                "sha256": file_hash(root / name),
                "bytes": (root / name).stat().st_size,
            }
            for name in ("X.npy", "y.npy")
        },
    }
    from ..statistical_quant.schema import digest
    from ..statistical_quant.schema import prediction_config

    manifest["predictionConfigHash"] = digest(prediction_config(strategy))
    manifest["identity"] = digest(manifest)
    write_json(root / "manifest.json", manifest)
    sync_directory(root)
    # Verify before publishing a completed sample matrix.
    for name in ("X.npy", "y.npy"):
        np.load(root / name, mmap_mode="r", allow_pickle=False)
        if file_hash(root / name) != manifest["arrays"][name]["sha256"]:
            fail("CAPACITY_CACHE", "Sample write verification failed")
    os.rename(root, target)
    sync_directory(parent)
    root = target
    X = np.load(root / "X.npy", mmap_mode="r", allow_pickle=False)
    y = np.load(root / "y.npy", mmap_mode="r", allow_pickle=False)
    return Samples(
        pd.DataFrame(X, columns=names, copy=False),
        pd.DataFrame(y, columns=["entry", "exit"], copy=False),
        pd.DataFrame(meta),
        {d["id"]: d for d in definitions},
        start,
        list(dates),
        fits,
    )
