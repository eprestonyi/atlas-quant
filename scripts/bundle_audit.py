"""Independent, standard-library-only verifier for exported Quant bundles.

Only one bounded chunk is decoded at a time. Cross-record references are kept in
a temporary SQLite database, not in an in-memory copy of the complete report.
No imports from the engine, provider, or production transport implementation.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import tempfile


LIMIT_MANIFEST = 512 * 1024
LIMIT_CHUNK = 8 * 1024 * 1024
LIMIT_TOTAL = 256 * 1024 * 1024
PATHS = {
    "forecasts": ("forecast", "/rows"),
    "targets": ("forecast", "/targetDefinitions"),
    "modelFits": ("forecast", "/modelFits"),
    "factorFeatures": ("forecast", "/factorResearch/diagnostics/features"),
    "factorJointDistributions": ("forecast", "/factorResearch/diagnostics/dependence/jointDistributions"),
    "hedgeFits": ("forecast", "/hedgeFits"),
    "perTarget": ("forecast", "/diagnostics/perTarget"),
    "outerFolds": ("forecast", "/diagnostics/outerFolds"),
    "finalTrials": ("forecast", "/diagnostics/finalTrials"),
    "baselineRows": ("forecast", "/diagnostics/factorIncrement/baselineRows"),
    "baselineModelFits": ("forecast", "/diagnostics/factorIncrement/baselineModelFits"),
    "dailyLosses": ("forecast", "/diagnostics/factorIncrement/dailyLosses"),
    "baselinePerTarget": ("forecast", "/diagnostics/factorIncrement/baselineValidation/perTarget"),
    "baselineOuterFolds": ("forecast", "/diagnostics/factorIncrement/baselineValidation/outerFolds"),
    "baselineFinalTrials": ("forecast", "/diagnostics/factorIncrement/baselineValidation/finalTrials"),
    "equity": ("report", "/equity"),
    "trades": ("report", "/trades"),
    "riskLedger": ("report", "/execution/ledger"),
    "decisions": ("report", "/execution/decisions"),
    "snapshotRows": ("snapshot", "/rows"),
    "snapshotContextSources": ("snapshot", "/provenance/contextSources"),
    "plannedOrigins": ("coverage", "/origins"),
}
IDENTITIES = {"forecasts": "forecastId", "baselineRows": "forecastId",
              "targets": "id", "modelFits": "id",
              "baselineModelFits": "id", "riskLedger": "date", "equity": "date"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "Duplicate JSON key: " + key)
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("Non-finite JSON number: " + value)


def decode(raw):
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)


def normalize(value):
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, float):
        require(math.isfinite(value), "Non-finite number")
        return int(value) if value.is_integer() else value
    return value


def canonical(value):
    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def is_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


@dataclass(frozen=True)
class Collection:
    name: str


class BundleAudit:
    def __init__(self, directory, database):
        self.directory = Path(directory).resolve()
        self.db = database
        raw = self.read_file("manifest.json", LIMIT_MANIFEST)
        self.bundle_id = sha(raw)
        self.manifest = m = self.decode_json(raw)
        self.validate_transport_header(raw, m)
        for key in ("forecastArtifactId", "predictionConfigHash", "dataFingerprint"):
            require(is_hash(m.get(key)), "Invalid identity: " + key)
        expected_docs = {"forecast", "report", "coverage"}
        if m["kind"] == "forecast":
            expected_docs.add("snapshot")
        require(set(m["documents"]) == expected_docs, "Incomplete or unexpected documents")
        self.collections = {}
        count = byte_count = 0
        for collection in m["collections"]:
            name = collection["id"]
            require(name in PATHS and name not in self.collections, "Unknown or duplicate collection")
            if name == "snapshotContextSources":
                require(m.get("format") == "atlas.quant.bundle" and m["kind"] == "forecast",
                        "Context sources require ordinary forecast transport")
            require((collection["document"], collection["path"]) == PATHS[name], "Collection path mismatch")
            require(collection["document"] in expected_docs, "Collection has no document")
            self.collections[name] = collection
            start = 0
            for ordinal, chunk in enumerate(collection["chunks"]):
                require(integer(chunk["ordinal"]) and integer(chunk["start"]) and chunk["ordinal"] == ordinal and chunk["start"] == start,
                        "Chunk order/coverage mismatch")
                require(integer(chunk["count"], 1) and integer(chunk["byteLength"], 2), "Invalid chunk size")
                require(chunk["count"] <= 10000, "Chunk row budget exceeded")
                require(chunk["byteLength"] <= LIMIT_CHUNK and is_hash(chunk["sha256"]), "Invalid chunk budget/hash")
                start += chunk["count"]
                byte_count += chunk["byteLength"]
                count += 1
            require(integer(collection["rowCount"]) and start == collection["rowCount"], "Collection count mismatch")
        require(count <= 256 and byte_count + len(raw) <= LIMIT_TOTAL, "Bundle resource budget exceeded")
        require(sum(c["rowCount"] for c in self.collections.values()) <= 1000000, "Bundle row budget exceeded")
        require(m["totals"] == {"chunkCount": count, "chunkBytes": byte_count}, "Bundle totals mismatch")
        self.db.executescript("""
            PRAGMA cache_size=-2048;
            CREATE TABLE records(collection TEXT,position INTEGER,identity TEXT,date TEXT,
                target TEXT,payload TEXT,PRIMARY KEY(collection,position),UNIQUE(collection,identity));
            CREATE INDEX record_date ON records(collection,date,position);
            CREATE UNIQUE INDEX origin_unique ON records(collection,date,target)
                WHERE collection IN ('forecasts','baselineRows','plannedOrigins');
        """)
        self.documents = {}
        self.checks = 0
        self.max_error = 0.0
        self.max_chunk = 0

    def validate_transport_header(self, raw, manifest):
        require(manifest.get("format") == "atlas.quant.bundle"
                and type(manifest.get("version")) is int and manifest["version"] == 1,
                "Unsupported bundle transport")
        require(manifest.get("kind") in ("forecast", "execution"), "Invalid bundle kind")

    def decode_json(self, raw):
        return decode(raw)

    def document_encoder(self, name):
        return canonical

    def collection_encoder(self, name):
        return self.document_encoder(self.collections[name]["document"])

    def validate_snapshot(self):
        snapshot = self.documents["snapshot"]
        provenance = snapshot["provenance"]
        contextual = "contextSourceRoot" in provenance
        version = "research_input_context_v1" if contextual else "research_input_v1"
        require(snapshot["schemaVersion"] == 1 and snapshot["fingerprintVersion"] == version
                and snapshot["dataFingerprint"] == self.manifest["dataFingerprint"]
                and snapshot["sourceDataFingerprint"] == provenance["dataFingerprint"],
                "Snapshot input metadata differs")
        require(contextual == ("snapshotContextSources" in self.collections),
                "Context source archive/version mismatch")
        if contextual:
            self.validate_context_sources(provenance)

    def validate_context_sources(self, provenance):
        """Verify frozen values and broadcast consistency, not provider authenticity."""
        report = self.documents["report"]["provenance"]
        require(provenance.get("contextScope") == "named_index_series_broadcast_by_date"
                and provenance.get("contextObservationClock") == "after_daily_publication_before_next_open",
                "Context source clock/scope mismatch")
        summaries = report.get("contextSources")
        count = self.collections["snapshotContextSources"]["rowCount"]
        require(1 <= count <= 16 and isinstance(summaries, list) and len(summaries) == count,
                "Context source summary count mismatch")
        self.db.execute("CREATE TABLE context_values(alias TEXT,date TEXT,value REAL,PRIMARY KEY(alias,date))")
        mappings = provenance.get("externalFields", {})
        root = hashlib.sha256(b"[")
        previous_source, aliases = None, set()
        def date(value):
            require(isinstance(value, str) and re.fullmatch(r"[0-9]{8}", value), "Invalid context date")
            datetime.strptime(value, "%Y%m%d")
            return value
        for index, source in enumerate(self.rows("snapshotContextSources")):
            require(set(source) == {"api", "params", "fields", "records", "sha256", "classification",
                                    "notWireBytes", "historicalRevisionVerified"}, "Invalid context source envelope")
            require(source["classification"] == "PARSED_PROVIDER_RESPONSE" and source["notWireBytes"] is True
                    and source["historicalRevisionVerified"] is False, "Invalid context evidence classification")
            params, api, fields, records = source["params"], source["api"], source["fields"], source["records"]
            require(isinstance(params, dict) and set(params) == {"ts_code", "start_date", "end_date"}
                    and api in {"index_daily", "sw_daily"}, "Invalid context source request")
            code = params["ts_code"]
            require(isinstance(code, str) and re.fullmatch(r"[0-9]{6}\.(?:SH|SZ|SI)", code)
                    and (code.endswith(".SI") == (api == "sw_daily")), "Invalid context source identity")
            identity = (api, code)
            require(previous_source is None or previous_source < identity, "Duplicate/unsorted context sources")
            previous_source = identity
            start, end = date(params["start_date"]), date(params["end_date"])
            require(start <= end, "Invalid context request interval")
            allowed = {"close", "vol", "amount"} | ({"pe", "pb", "total_mv", "float_mv"} if api == "sw_daily" else set())
            require(isinstance(fields, list) and all(isinstance(f, str) for f in fields)
                    and len(fields) >= 3 and fields[:2] == ["ts_code", "trade_date"]
                    and fields[2:] == sorted(set(fields[2:])) and set(fields[2:]) <= allowed,
                    "Invalid context source fields")
            require(isinstance(records, list) and 1 <= len(records) <= 4000
                    and source["sha256"] == sha(canonical(records)), "Context source records hash/count mismatch")
            summary = {k: source[k] for k in ("api", "params", "fields", "sha256")}
            summary["rowCount"] = len(records)
            require(summary == summaries[index], "Context source summary differs from frozen source")
            if index:
                root.update(b",")
            root.update(canonical(source))
            previous_date = None
            for row in records:
                require(isinstance(row, dict) and set(row) == set(fields) and row["ts_code"] == code,
                        "Context row identity mismatch")
                day = date(row["trade_date"])
                require(start <= day <= end and (previous_date is None or previous_date < day),
                        "Duplicate/unsorted/out-of-range context date")
                previous_date = day
                for field in fields[2:]:
                    value = row[field]
                    require(value is None or (type(value) in (int, float) and math.isfinite(value)
                            and (field != "close" or value > 0)
                            and (field not in {"vol", "amount", "total_mv", "float_mv"} or value >= 0)),
                            "Invalid context observation")
                    alias = "ext_ctx_" + code.lower().replace(".", "_") + "_" + field
                    aliases.add(alias)
                    self.db.execute("INSERT INTO context_values VALUES(?,?,?)", (alias, day, value))
                self.checks += 1
            for field in fields[2:]:
                alias = "ext_ctx_" + code.lower().replace(".", "_") + "_" + field
                unit = {"close": "index_points", "pe": "ratio", "pb": "ratio",
                        "total_mv": "CNY_10000", "float_mv": "CNY_10000"}.get(field)
                unit = unit or ({"vol": "shares_10000", "amount": "CNY_10000"} if api == "sw_daily"
                                else {"vol": "hands", "amount": "CNY_thousands"})[field]
                expected = {"source": "TUSHARE_PRO", "path": api+"/"+code+"/"+field, "dataType": "number",
                            "unit": unit, "availabilityPolicy": "point_in_time_asof", "availableDateColumn": alias+"__available_date"}
                require(mappings.get(alias) == expected, "Context mapping differs from source")
        root.update(b"]")
        require(root.hexdigest() == provenance["contextSourceRoot"] == report.get("contextSourceRoot"),
                "Context source root mismatch")
        for row in self.rows("snapshotRows"):
            for alias in aliases:
                require(alias in row and alias+"__available_date" in row, "Context field missing from snapshot row")
                observed = self.db.execute("SELECT value FROM context_values WHERE alias=? AND date=?",
                                           (alias, row["trade_date"])).fetchone()
                value = observed[0] if observed else None
                actual, available = row[alias], row[alias+"__available_date"]
                require((actual is None if value is None else type(actual) in (int, float) and actual == value)
                        and (available in (None, "") if value is None else available == row["trade_date"]),
                        "Context snapshot broadcast mismatch")
                self.checks += 1

    def read_file(self, relative, maximum):
        candidate = self.directory / relative
        resolved = candidate.resolve()
        require(resolved.is_relative_to(self.directory), "Bundle path escapes export directory")
        require(not candidate.is_symlink(), "Bundle file must not be a symlink")
        with resolved.open("rb") as file:
            raw = file.read(maximum + 1)
        require(len(raw) <= maximum, "File exceeds declared resource budget")
        return raw

    def chunk_bytes(self, name, descriptor):
        raw = self.read_file(f"chunks/{name}/{descriptor['ordinal']}.json", LIMIT_CHUNK)
        require(len(raw) == descriptor["byteLength"] and sha(raw) == descriptor["sha256"],
                "Chunk length/hash mismatch")
        require(raw.startswith(b"[") and raw.endswith(b"]"), "Chunk is not a canonical JSON array")
        self.max_chunk = max(self.max_chunk, len(raw))
        return raw

    def index_chunks(self):
        for name, collection in self.collections.items():
            for descriptor in collection["chunks"]:
                raw = self.chunk_bytes(name, descriptor)
                rows = self.decode_json(raw)
                encode = self.collection_encoder(name)
                require(isinstance(rows, list) and len(rows) == descriptor["count"], "Chunk row count mismatch")
                require(encode(rows) == raw, "Chunk encoding is not canonical v1 JSON")
                for offset, row in enumerate(rows):
                    require(isinstance(row, dict), "Collection record must be an object")
                    key = IDENTITIES.get(name)
                    identity = row.get(key) if key else None
                    if key:
                        require(isinstance(identity, str) and identity, "Missing record identity: " + name)
                    self.db.execute("INSERT INTO records VALUES(?,?,?,?,?,?)", (
                        name, descriptor["start"] + offset, identity, row.get("date"),
                        row.get("targetId"), encode(row).decode("utf-8")))
                self.checks += len(rows) + 1
                del raw, rows
        self.db.commit()

    def rows(self, name, date=None):
        sql = "SELECT payload FROM records WHERE collection=?"
        args = [name]
        if date is not None:
            sql += " AND date=?"
            args.append(date)
        for (payload,) in self.db.execute(sql + " ORDER BY position", args):
            yield decode(payload)

    def lookup(self, name, identity):
        row = self.db.execute("SELECT payload FROM records WHERE collection=? AND identity=?",
                              (name, identity)).fetchone()
        require(row is not None, f"Missing {name} reference: {identity}")
        return decode(row[0])

    def near(self, left, right, label):
        require(type(left) in (int, float) and type(right) in (int, float), label + " non-number")
        require(math.isfinite(left) and math.isfinite(right), label + " non-finite")
        difference = abs(left - right)
        require(difference <= 1e-7 + 2e-11 * max(abs(left), abs(right)), label + " mismatch")
        self.max_error = max(self.max_error, difference)
        self.checks += 1

    def collection_bytes(self, name):
        yield b"["
        for index, descriptor in enumerate(self.collections[name]["chunks"]):
            if index:
                yield b","
            yield self.chunk_bytes(name, descriptor)[1:-1]
        yield b"]"

    def document_bytes(self, name):
        for part in self.manifest["documents"][name]["parts"]:
            if "literal" in part:
                yield part["literal"].encode("utf-8")
            elif "collection" in part:
                yield from self.collection_bytes(part["collection"])
            else:
                yield b'{"artifactId":' + canonical(part["wrapArtifactId"]) + b","
                pending = None
                first = True
                for raw in self.document_bytes("forecast"):
                    if first:
                        require(raw.startswith(b"{"), "Forecast must start with an object")
                        raw = raw[1:]
                        first = False
                    if pending is not None:
                        yield pending
                    pending = raw
                require(pending is not None and pending.endswith(b"}"), "Forecast object is incomplete")
                yield pending[:-1]
                yield b"}"

    def canonical_stream(self, value, encode=canonical):
        if isinstance(value, Collection):
            yield from self.collection_bytes(value.name)
        elif isinstance(value, dict):
            yield b"{"
            for index, key in enumerate(sorted(value)):
                if index:
                    yield b","
                yield encode(key) + b":"
                yield from self.canonical_stream(value[key], encode)
            yield b"}"
        elif isinstance(value, list):
            yield b"["
            for index, item in enumerate(value):
                if index:
                    yield b","
                yield from self.canonical_stream(item, encode)
            yield b"]"
        else:
            yield encode(value)

    @staticmethod
    def stream_hash(chunks):
        digest = hashlib.sha256()
        length = 0
        for chunk in chunks:
            digest.update(chunk)
            length += len(chunk)
        return digest.hexdigest(), length

    def validate_documents(self):
        markers = {"\0bundle:" + self.bundle_id + ":" + name: name for name in self.collections}
        doc_marker = "\0bundle:" + self.bundle_id + ":document"
        used = set()
        for doc in ("forecast", "coverage", "snapshot", "report"):
            if doc not in self.manifest["documents"]:
                continue
            descriptor = self.manifest["documents"][doc]
            require(is_hash(descriptor["sha256"]) and integer(descriptor["byteLength"], 2), "Invalid document identity")
            skeleton_parts = []
            references = set()
            document_references = 0
            for part in descriptor["parts"]:
                require(isinstance(part, dict), "Invalid recipe instruction")
                if set(part) == {"literal"}:
                    require(isinstance(part["literal"], str), "Literal must be text")
                    skeleton_parts.append(part["literal"].encode("utf-8"))
                elif set(part) == {"collection"}:
                    name = part["collection"]
                    require(name in self.collections and name not in used, "Unknown/repeated collection reference")
                    require(self.collections[name]["document"] == doc, "Collection in wrong document")
                    used.add(name)
                    references.add(name)
                    skeleton_parts.append(canonical("\0bundle:" + self.bundle_id + ":" + name))
                else:
                    require(set(part) == {"document", "wrapArtifactId"} and doc == "report"
                            and part["document"] == "forecast"
                            and part["wrapArtifactId"] == self.manifest["forecastArtifactId"],
                            "Illegal document reference")
                    document_references += 1
                    require(document_references == 1, "Repeated forecast reference")
                    skeleton_parts.append(canonical(doc_marker))
            skeleton_bytes = b"".join(skeleton_parts)
            require(len(skeleton_bytes) <= LIMIT_MANIFEST, "Skeleton exceeds metadata budget")
            skeleton = self.decode_json(skeleton_bytes)
            require(isinstance(skeleton, dict), "Document root must be an object")
            found = set()
            found_doc = False

            def replace(value, path=""):
                nonlocal found_doc
                if isinstance(value, str) and value in markers:
                    name = markers[value]
                    require(name in references and name not in found and PATHS[name] == (doc, path),
                            "Actual collection layout differs from declared path")
                    found.add(name)
                    return Collection(name)
                if value == doc_marker:
                    require(doc == "report" and path == "/forecasts" and not found_doc,
                            "Forecast document at incorrect path")
                    found_doc = True
                    return {"artifactId": self.manifest["forecastArtifactId"], **self.documents["forecast"]}
                if isinstance(value, dict):
                    return {k: replace(v, path + "/" + k.replace("~", "~0").replace("/", "~1"))
                            for k, v in value.items()}
                if isinstance(value, list):
                    return [replace(v, path + "/" + str(i)) for i, v in enumerate(value)]
                return value

            result = replace(skeleton)
            require(found == references and found_doc == (doc == "report"), "Missing recipe reference")
            for name, (expected_doc, pointer) in PATHS.items():
                if expected_doc != doc:
                    continue
                value = result
                for key in pointer[1:].split("/"):
                    value = value.get(key) if isinstance(value, dict) else None
                if value is not None:
                    require(value == Collection(name), "Required array was embedded instead of chunked: " + name)
            self.documents[doc] = result
            expected = (descriptor["sha256"], descriptor["byteLength"])
            require(self.stream_hash(self.document_bytes(doc)) == expected, "Recipe document hash/length mismatch")
            require(self.stream_hash(self.canonical_stream(result, self.document_encoder(doc))) == expected, "Noncanonical document layout")
            self.checks += 2
        require(used == set(self.collections), "Unreachable chunk descriptor")
        require(self.manifest["documents"]["forecast"]["sha256"] == self.manifest["forecastArtifactId"],
                "Transport changed logical forecast identity")

    def validate_predictions(self):
        m, f, r, coverage = (self.manifest, self.documents["forecast"],
                             self.documents["report"], self.documents["coverage"])
        require(r["schemaVersion"] == 2 and f["schemaVersion"] == 1, "Unsupported research schema")
        require(f["predictionConfigHash"] == m["predictionConfigHash"] and
                f["dataFingerprint"] == m["dataFingerprint"], "Manifest differs from forecast metadata")
        require(r["provenance"]["dataSha256"] == m["dataFingerprint"], "Report input fingerprint differs")
        if "snapshot" in self.documents:
            self.validate_snapshot()
        require(f["truncated"] is False and f["totalRows"] == self.collections["forecasts"]["rowCount"],
                "Incomplete forecast artifact")
        require(coverage["schemaVersion"] == 1 and coverage["source"] in
                ("samples_before_model_fitting", "legacy_artifact_derived"), "Unknown coverage source")
        require(type(coverage["baselineRequired"]) is bool, "Invalid baseline declaration")
        require(self.collections["plannedOrigins"]["rowCount"] == f["totalRows"], "Plan/forecast count mismatch")
        if coverage["baselineRequired"]:
            require(self.collections.get("baselineRows", {}).get("rowCount") == f["totalRows"],
                    "Missing baseline predictions")
        else:
            require(self.collections.get("baselineRows", {}).get("rowCount", 0) == 0, "Unexpected baseline predictions")
        keys = ("date", "targetId", "entryDate", "targetDate")
        for collection, fits in (("forecasts", "modelFits"), ("baselineRows", "baselineModelFits")):
            if collection not in self.collections:
                continue
            previous = None
            for plan, row in zip(self.rows("plannedOrigins"), self.rows(collection), strict=True):
                require(all(plan[k] == row[k] for k in keys), "Prediction differs from independent origin plan")
                require(type(plan["inputValid"]) is bool, "Invalid planned input status")
                order = row["date"]
                require(previous is None or order >= previous, "Prediction order is unstable")
                previous = order
                require(row["date"] >= coverage["holdoutStart"], "Prediction before declared reporting period")
                require(row["status"] in ("valid", "invalid"), "Unknown prediction status")
                if row["targetId"] == "unavailable":
                    require(not plan["inputValid"] and row["status"] == "invalid", "Undeclared unavailable target")
                else:
                    target = self.lookup("targets", row["targetId"])
                    formed = target["formationEnd"]
                    if formed is not None:
                        require(formed < row["date"], "Target uses future formation data")
                    else:
                        require(target["construction"] in ("single_asset", "fixed"),
                                "Estimated target lacks formation cutoff")
                if row["modelFitId"] is not None:
                    fit = self.lookup(fits, row["modelFitId"])
                    require(fit["fitDate"] <= row["date"] and
                            (fit["labelEndMax"] is None or fit["labelEndMax"] < fit["fitDate"]),
                            "Prediction uses future model fit/labels")
                if row["status"] != "valid":
                    continue
                require(plan["inputValid"] and row["modelFitId"], "Valid prediction lacks inputs/model")
                p, v = row["currentState"], row["expectedFuture"]
                self.near(row["edgeGap"], p-v, "e = P - V")
                self.near(row["expectedChange"], v-p, "expected change = -e")
                self.near(row["expectedGrossPnl"], v-row["expectedEntry"], "remaining change")
                self.near(row["expectedGrossBps"], row["expectedGrossPnl"]/row["scale"]*10000, "remaining bps")
                require(row["date"] < row["entryDate"] < row["targetDate"], "Invalid forecast chronology")
                if row["realizedFuture"] is not None:
                    self.near(row["forecastError"], row["realizedFuture"]-v, "forecast error")
                    self.near(row["realizedFuture"]-p, -row["edgeGap"]+row["forecastError"], "actual decomposition")
        require(r["execution"]["forecastArtifactId"] == m["forecastArtifactId"], "Execution reference mismatch")

    def count(self, name):
        return self.collections.get(name, {}).get("rowCount", 0)

    def validate_accounting(self):
        report = self.documents["report"]
        execution = report["execution"]
        if not execution["enabled"]:
            require(report["metrics"] is None and not self.count("trades") and not self.count("equity")
                    and not self.count("riskLedger"), "Disabled execution contains a performance result")
            return
        require(self.count("riskLedger") == self.count("equity") > 0, "Incomplete ledger/equity")
        cash = initial = report["strategy"]["portfolio"]["initialCapital"]
        costs, metrics = report["strategy"]["costs"], report["metrics"]
        positions, lots = defaultdict(float), defaultdict(list)
        totals = dict.fromkeys(("commission", "slippage", "tax", "transfer", "borrow"), 0.0)
        peak, max_drawdown, seen_trades = initial, 0.0, 0
        calendar = report["provenance"].get("tradingDates")
        require(isinstance(calendar, list) and calendar == sorted(set(calendar)), "Missing/invalid official calendar")
        first_origin = next(self.rows("forecasts"))["date"]
        expected_dates = [d for d in calendar if first_origin <= d <= report["strategy"]["universe"]["end"]]
        if metrics.get("bankrupt"):
            expected_dates = expected_dates[:self.count("riskLedger")]
        require(len(expected_dates) == self.count("riskLedger"), "Ledger misses a declared trading session")
        for date, point, chart in zip(expected_dates, self.rows("riskLedger"), self.rows("equity"), strict=True):
            require(date == point["date"] == chart["date"], "Ledger/equity calendar differs")
            for key in ("equity", "cash", "positionsValue", "drawdown", "benchmark", "dailyCosts", "borrowCost", "grossExposure", "netExposure"):
                if point[key] is None:
                    require(chart[key] is None, "Chart invents unavailable value")
                else:
                    self.near(chart[key], point[key], "chart/ledger " + key)
            day_fees = 0.0
            for trade in self.rows("trades", date):
                seen_trades += 1
                forecast = self.lookup("forecasts", trade["forecastId"])
                require(trade["targetId"] == forecast["targetId"] and trade["signalDate"] == forecast["date"],
                        "Trade forecast reference differs")
                require(forecast["status"] == "valid" and date > forecast["date"], "Trade without valid prior prediction")
                if trade["exitReason"] is None:
                    require(date == forecast["entryDate"], "Entry shifted from declared date")
                symbol, quantity, price = trade["symbol"], trade["signedQuantity"], trade["price"]
                target = self.lookup("targets", forecast["targetId"])
                require(symbol in target["symbols"], "Trade is not a target leg")
                require((quantity > 0 and trade["side"] == "BUY") or (quantity < 0 and trade["side"] == "SELL"), "Trade side differs")
                self.near(trade["quantity"], abs(quantity), "absolute quantity")
                notional = abs(quantity) * price
                self.near(trade["notional"], notional, "notional")
                fees = {"commission": max(costs["minCommission"], notional*costs["commissionBps"]/10000),
                        "slippage": notional*costs["slippageBps"]/10000,
                        "tax": notional*costs["sellTaxBps"]/10000 if quantity < 0 else 0,
                        "transfer": notional*costs["transferBps"]/10000}
                for key, amount in fees.items():
                    self.near(trade[key], amount, "trade " + key)
                    totals[key] += amount
                fee = sum(fees.values())
                self.near(trade["cost"], fee, "total trade fee")
                before = positions[symbol]
                reduction = min(max(before, 0), max(-quantity, 0))
                require(reduction <= sum(q for d, q in lots[symbol] if d < date) + 1e-7,
                        "Long inventory sold before T+1")
                for lot in lots[symbol]:
                    if lot[0] < date and reduction > 0:
                        used = min(lot[1], reduction)
                        lot[1] -= used
                        reduction -= used
                added = max(before+quantity, 0)-max(before, 0)
                lots[symbol] = [lot for lot in lots[symbol] if lot[1] > 1e-12]
                if added > 0:
                    lots[symbol].append([date, added])
                positions[symbol] += quantity
                cash -= quantity*price + fee
                day_fees += fee
                self.near(trade["cashAfter"], cash, "cash after trade")
                self.near(trade["positionAfter"], positions[symbol], "position after trade")
            holdings = {p["symbol"]: p for p in point["positions"]}
            require(len(holdings) == len(point["positions"]), "Duplicate daily holding")
            for symbol, quantity in positions.items():
                self.near(holdings.get(symbol, {}).get("quantity", 0), quantity, "daily position")
            value = gross = short_value = 0.0
            for symbol, holding in holdings.items():
                self.near(holding["quantity"], positions[symbol], "no unexplained position")
                marked = holding["quantity"]*holding["mark"]
                self.near(holding["value"], marked, "holding valuation")
                self.near(holding["sellableQuantity"], sum(q for d, q in lots[symbol] if d < date), "settled inventory")
                value += marked
                gross += abs(marked)
                short_value += max(-marked, 0)
            borrowing = short_value*costs["borrowAnnualBps"]/10000/252
            self.near(point["borrowCost"], borrowing, "borrow accrual")
            cash -= borrowing
            totals["borrow"] += borrowing
            self.near(point["dailyCosts"], day_fees+borrowing, "daily fees")
            self.near(point["cash"], cash, "daily cash")
            self.near(point["positionsValue"], value, "holdings value")
            self.near(point["equity"], cash+value, "NAV")
            if point["equity"] > 0:
                self.near(point["netExposure"], value/point["equity"], "net exposure")
                self.near(point["grossExposure"], gross/point["equity"], "gross exposure")
            peak = max(peak, point["equity"])
            self.near(point["drawdown"], point["equity"]/peak-1, "drawdown")
            max_drawdown = min(max_drawdown, point["drawdown"])
        require(seen_trades == self.count("trades"), "Trade outside declared ledger")
        require(not metrics.get("bankrupt") or point["equity"] <= 0, "False bankruptcy termination")
        for key, amount in totals.items():
            self.near(metrics["costBreakdown"][key], amount, "total " + key)
        self.near(metrics["totalCosts"], sum(totals.values()), "all fees")
        self.near(metrics["totalReturn"], point["equity"]/initial-1, "net return")
        self.near(metrics["maxDrawdown"], max_drawdown, "maximum drawdown")
        require(metrics["tradeCount"] == seen_trades, "Trade count differs")

    def run(self):
        self.validate_documents()
        self.index_chunks()
        self.validate_predictions()
        self.validate_accounting()
        return {"status": "passed", "bundleId": self.bundle_id,
                "forecastArtifactId": self.manifest["forecastArtifactId"],
                "coverageSource": self.documents["coverage"]["source"],
                "checks": self.checks, "forecastRows": self.count("forecasts"),
                "baselineRows": self.count("baselineRows"), "trades": self.count("trades"),
                "ledgerDates": self.count("riskLedger"), "maxChunkBytes": self.max_chunk,
                "maxAbsoluteError": self.max_error, "engineImports": False}


def audit_bundle(directory, source_directory=None):
    with tempfile.TemporaryDirectory(prefix="atlas-bundle-audit-") as scratch:
        with sqlite3.connect(str(Path(scratch) / "audit.sqlite")) as database:
            audit = BundleAudit(directory, database)
            result = audit.run()
            if source_directory:
                with sqlite3.connect(str(Path(scratch) / "source.sqlite")) as source_database:
                    source = BundleAudit(source_directory, source_database)
                    source.run()
                    require(audit.manifest["forecastArtifactId"] == source.manifest["forecastArtifactId"],
                            "Execution changed frozen forecast identity")
                    require(audit.manifest["documents"]["coverage"]["sha256"] == source.manifest["documents"]["coverage"]["sha256"],
                            "Execution changed origin plan")
                    require(audit.documents["report"]["research"]["predictionRefitPerformed"] is False,
                            "Execution refitted predictions")
                    result["sourceForecastAndPlanIdentical"] = True
            return result
