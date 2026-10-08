"""Causal AST DAG: cross-sectional nodes always see the complete date's pool."""

from __future__ import annotations
import ast
import copy
import os
from pathlib import Path
import shutil
import uuid
import numpy as np
import pandas as pd

from ..factors import _parse, _number, WINDOW_FUNCTIONS
from ..statistical_quant.schema import digest, fail
from .panel_store import (
    private_dir,
    write_array,
    write_json,
    read_json,
    open_array,
    sync_directory,
    file_hash,
)

IR_VERSION = "global_factor_dag_float64_v1"


class FeatureGraph:
    def __init__(self, factors):
        self.factors = copy.deepcopy(factors)
        self.numerical_runtime = {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "implementationSha256": file_hash(Path(__file__)),
        }
        self.nodes, self.roots, self.metadata = {}, {}, {}

        def add(node):
            key = digest(
                {
                    "irVersion": IR_VERSION,
                    "ast": ast.dump(node, include_attributes=False),
                }
            )
            if key in self.nodes:
                return key
            if isinstance(node, ast.Call):
                children = [add(node.args[0])]
                if node.func.id in {"min", "max"}:
                    children.append(add(node.args[1]))
                kind = (
                    "temporal"
                    if node.func.id in WINDOW_FUNCTIONS
                    else (
                        "cross_section"
                        if node.func.id in {"rank", "zscore"}
                        else "pointwise"
                    )
                )
            elif isinstance(node, ast.BinOp):
                children, kind = [add(node.left), add(node.right)], "pointwise"
            elif isinstance(node, ast.UnaryOp):
                children, kind = [add(node.operand)], "pointwise"
            else:
                children, kind = [], (
                    "field" if isinstance(node, ast.Name) else "constant"
                )
            self.nodes[key] = {"ast": node, "children": children, "kind": kind}
            return key

        for factor in self.factors:
            tree, meta = _parse(factor["expression"])
            self.roots[factor["id"]] = add(tree.body)
            self.metadata[factor["id"]] = meta
        self.identity = digest(
            {
                "irVersion": IR_VERSION,
                "roots": self.roots,
                "directions": {f["id"]: f["direction"] for f in self.factors},
                "numericalRuntime": self.numerical_runtime,
            }
        )

    @classmethod
    def compile(cls, factors):
        return cls(factors)

    def evaluate(
        self,
        store,
        cache_dir,
        *,
        symbol_block=32,
        date_block=128,
        max_bytes=400 * 1024 * 1024,
    ):
        if (
            type(symbol_block) is not int
            or type(date_block) is not int
            or min(symbol_block, date_block) < 1
        ):
            fail("CAPACITY_BLOCK", "Block dimensions must be positive integers")
        if type(max_bytes) is not int or max_bytes < 1:
            fail("CAPACITY_DISK", "Feature cache budget must be a positive integer")
        parent = private_dir(cache_dir)
        key = digest(
            {"store": store.identity, "graph": self.identity, "irVersion": IR_VERSION}
        )
        target = parent / key
        expected = {
            "identity": key,
            "panelRoot": store.identity,
            "graphRoot": self.identity,
            "irVersion": IR_VERSION,
        }
        if target.exists():
            manifest = read_json(target / "manifest.json")
            if any(manifest.get(k) != v for k, v in expected.items()):
                fail("CAPACITY_CACHE", "Feature graph cache identity mismatch")
            return FeatureSet(target, manifest, max_bytes=max_bytes)
        temp = private_dir(parent / ("stage_" + uuid.uuid4().hex))
        arrays, descriptors = {}, {}
        shape = (len(store.dates), len(store.symbols))
        try:
            for key, node in self.nodes.items():
                output = np.lib.format.open_memmap(
                    temp / ("node_" + key + ".npy"), mode="w+", dtype="<f8", shape=shape
                )
                os.chmod(temp / ("node_" + key + ".npy"), 0o600)
                inputs = [arrays[c] for c in node["children"]]
                syntax = node["ast"]
                with np.errstate(all="ignore"):
                    if node["kind"] == "temporal":
                        name, n = syntax.func.id, int(_number(syntax.args[1]))
                        for begin in range(0, shape[1], symbol_block):
                            for column in range(
                                begin, min(begin + symbol_block, shape[1])
                            ):
                                x = pd.Series(np.asarray(inputs[0][:, column]))
                                if name == "lag":
                                    value = x.shift(n)
                                elif name == "delta":
                                    value = x - x.shift(n)
                                elif name == "returns":
                                    lag = x.shift(n)
                                    value = x.div(lag.where(lag.abs() > 1e-12)) - 1
                                else:
                                    roll = x.rolling(n, min_periods=n)
                                    value = (
                                        roll.std(ddof=0)
                                        if name == "ts_std"
                                        else (
                                            roll.rank(pct=True)
                                            if name == "ts_rank"
                                            else getattr(roll, name[3:])()
                                        )
                                    )
                                output[:, column] = value.to_numpy()
                    else:
                        for begin in range(0, shape[0], date_block):
                            end = min(begin + date_block, shape[0])
                            block = (slice(begin, end), slice(None))
                            vals = [np.asarray(a[block]) for a in inputs]
                            if node["kind"] == "field":
                                value = np.asarray(store.field(syntax.id)[block])
                                value = np.where(np.isfinite(value), value, np.nan)
                            elif node["kind"] == "constant":
                                value = float(syntax.value)
                            elif isinstance(syntax, ast.UnaryOp):
                                value = (
                                    -vals[0]
                                    if isinstance(syntax.op, ast.USub)
                                    else vals[0]
                                )
                            elif isinstance(syntax, ast.BinOp):
                                a, b = vals
                                value = (
                                    a + b
                                    if isinstance(syntax.op, ast.Add)
                                    else (
                                        a - b
                                        if isinstance(syntax.op, ast.Sub)
                                        else (
                                            a * b
                                            if isinstance(syntax.op, ast.Mult)
                                            else np.divide(
                                                a,
                                                np.where(np.abs(b) > 1e-12, b, np.nan),
                                            )
                                        )
                                    )
                                )
                            else:
                                name = syntax.func.id
                                x = vals[0]
                                if name in {"rank", "zscore"}:
                                    # Exactly the original pandas reduction order, on full dates.
                                    labels = np.repeat(np.arange(end - begin), shape[1])
                                    series = pd.Series(x.reshape(-1), index=labels)
                                    group = series.groupby(level=0)
                                    if name == "rank":
                                        value = (
                                            group.rank(pct=True, method="average")
                                            .to_numpy()
                                            .reshape(x.shape)
                                        )
                                    else:
                                        mean = group.transform("mean")
                                        std = group.transform(lambda s: s.std(ddof=0))
                                        value = (
                                            (series - mean)
                                            .div(std.where(std.abs() > 1e-12))
                                            .to_numpy()
                                            .reshape(x.shape)
                                        )
                                elif name == "log":
                                    value = np.log(np.where(x > 0, x, np.nan))
                                elif name == "sqrt":
                                    value = np.sqrt(np.where(x >= 0, x, np.nan))
                                elif name == "abs":
                                    value = np.abs(x)
                                elif name == "sign":
                                    value = np.sign(x)
                                elif name == "clip":
                                    value = np.clip(
                                        x,
                                        _number(syntax.args[1]),
                                        _number(syntax.args[2]),
                                    )
                                elif name == "min":
                                    value = np.minimum(x, vals[1])
                                else:
                                    value = np.maximum(x, vals[1])
                            output[block] = value
                output.flush()
                del output
                filename = "node_" + key + ".npy"
                with (temp / filename).open("rb") as stream:
                    os.fsync(stream.fileno())
                descriptors[key] = {
                    "file": filename,
                    "sha256": file_hash(temp / filename),
                    "bytes": (temp / filename).stat().st_size,
                    "shape": list(shape),
                    "dtype": "<f8",
                }
                if sum(i["bytes"] for i in descriptors.values()) > max_bytes:
                    fail(
                        "CAPACITY_DISK",
                        "Feature DAG exceeds cache budget; no truncated graph is published",
                    )
                arrays[key] = open_array(temp, descriptors[key])
            outputs = {}
            for index, f in enumerate(self.factors):
                raw = arrays[self.roots[f["id"]]]
                value = np.asarray(raw) * f["direction"]
                value = np.where(
                    np.isfinite(value) & np.isfinite(store.field("close")),
                    value,
                    np.nan,
                )
                outputs[f["id"]] = write_array(temp / f"factor_{index}.npy", value)
            total = sum(i["bytes"] for i in (*descriptors.values(), *outputs.values()))
            if total > max_bytes:
                fail("CAPACITY_DISK", "Feature graph exceeds total cache budget")
            manifest = {
                **expected,
                "nodes": descriptors,
                "outputs": outputs,
                "totalBytes": total,
                "nodeKinds": {k: n["kind"] for k, n in self.nodes.items()},
                "factorMetadata": self.metadata,
                "numericalRuntime": self.numerical_runtime,
            }
            manifest["manifestHash"] = digest(manifest)
            write_json(temp / "manifest.json", manifest)
            sync_directory(temp)
            arrays.clear()
            os.rename(temp, target)
            sync_directory(parent)
            return FeatureSet(target, manifest, max_bytes=max_bytes)
        except BaseException:
            arrays.clear()
            if temp.exists():
                shutil.rmtree(temp)
            raise


class FeatureSet:
    def __init__(self, root, manifest, *, max_bytes=None):
        if manifest.get("manifestHash") != digest(
            {k: v for k, v in manifest.items() if k != "manifestHash"}
        ):
            fail("CAPACITY_CACHE", "Feature cache manifest content changed")
        sizes = [
            item.get("bytes")
            for item in (*manifest["nodes"].values(), *manifest["outputs"].values())
        ]
        if any(type(size) is not int or size < 1 for size in sizes):
            fail("CAPACITY_CACHE", "Feature cache contains an invalid byte count")
        total = sum(sizes)
        if total != manifest.get("totalBytes"):
            fail("CAPACITY_CACHE", "Feature cache total differs from its arrays")
        if max_bytes is not None and total > max_bytes:
            fail("CAPACITY_DISK", "Feature graph exceeds this call's cache budget")
        self.root, self.manifest = root, manifest
        self.identity = manifest["identity"]
        self.outputs = {k: open_array(root, v) for k, v in manifest["outputs"].items()}
        # All intermediate hashes are part of the cache verification, not names.
        for info in manifest["nodes"].values():
            open_array(root, info)

    def __getitem__(self, key):
        return self.outputs[key]
