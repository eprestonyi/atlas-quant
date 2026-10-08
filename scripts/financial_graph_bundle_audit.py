"""Independent stdlib financial bundle/2 and dataset/3 paired closure audit.

The unchanged legacy independent auditors supply forecast/USTAR primitives.
New format/layout validation is isolated here; no engine or graph codec import.
"""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import re
import sqlite3
import tempfile

from bundle_audit import (BundleAudit, Collection, LIMIT_MANIFEST, LIMIT_CHUNK,
    LIMIT_TOTAL, PATHS, canonical, decode, is_hash, integer, require, sha)
from bundle_archive import _open_archive, _header, _write_body
from financial_bundle_audit import bounded_decode, financial_json
import graph_dataset_audit as graph_audit
from dataset_audit import Checks, read_file

FORMAT = 'atlas.quant.financial_bundle'
RESEARCH_PROFILE = 'financial_fundamental_graph_auto_50_v1'
CODECS = {'forecast':'forecast_json_v1', 'report':'forecast_json_v1',
          'coverage':'forecast_json_v1', 'snapshot':'financial_column_snapshot_v1'}
GRAPH_PATHS = {k:v for k,v in PATHS.items() if k != 'snapshotRows'} | {
    'snapshotColumns':('snapshot','/numericInput/columns')}
SNAPSHOT_KEYS = set('schemaVersion fingerprintVersion datasetRef sourceEvidenceClosure provenance dataFingerprint sourceDataFingerprint financialSourceCommitment numericInput'.split())


class FinancialGraphBundleAudit(BundleAudit):
    def __init__(self, directory, database, *, planning=False):
        directory = Path(directory)
        require(not directory.is_symlink() and directory.is_dir(), 'Bundle directory must be a regular local directory')
        self.directory = directory.resolve()
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
            require(name in GRAPH_PATHS and name not in self.collections, "Unknown or duplicate collection")
            require((collection["document"], collection["path"]) == GRAPH_PATHS[name], "Collection path mismatch")
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
        require({'forecasts','targets','modelFits','hedgeFits','equity','trades','riskLedger','decisions','plannedOrigins','snapshotColumns'} <= set(self.collections), 'Required financial collections missing')
        if not planning:
            require(set(p.name for p in self.directory.iterdir()) == {'manifest.json','chunks'}, 'Unregistered bundle entries')
            chunks = self.directory/'chunks'
            require(chunks.is_dir() and not chunks.is_symlink() and set(p.name for p in chunks.iterdir()) == set(self.collections), 'Missing/extra/symlink chunk directories')
            for name, collection in self.collections.items():
                folder = chunks/name
                require(folder.is_dir() and not folder.is_symlink() and set(p.name for p in folder.iterdir()) == {str(d['ordinal'])+'.json' for d in collection['chunks']}, 'Missing/extra/symlink chunk files')
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
        require(
            isinstance(manifest, dict)
            and set(manifest)
            == {
                "format",
                "version",
                "kind",
                "forecastArtifactId",
                "predictionConfigHash",
                "dataFingerprint",
                "sourceEvidence",
                "documents",
                "collections",
                "totals",
            },
            "Financial manifest fields differ",
        )
        require(
            manifest["format"] == FORMAT
            and type(manifest["version"]) is int
            and manifest["version"] == 2
            and manifest["kind"] == "forecast",
            "Unsupported financial transport; execution is disabled",
        )
        require(canonical(manifest) == raw, "Manifest is not canonical")
        evidence = manifest["sourceEvidence"]
        require(
            isinstance(evidence, dict)
            and set(evidence) == {"datasetRef", "admissionProfile"},
            "Source evidence fields differ",
        )
        ref = evidence["datasetRef"]
        require(
            isinstance(ref, dict)
            and set(ref) == {"datasetId", "datasetRoot", "format", "version"},
            "Dataset reference fields differ",
        )
        require(
            isinstance(ref["datasetId"], str)
            and re.fullmatch(
                r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", ref["datasetId"]
            )
            and is_hash(ref["datasetRoot"])
            and ref["format"] == "atlas.quant.research_dataset"
            and type(ref["version"]) is int
            and ref["version"] == 3
            and evidence["admissionProfile"] == RESEARCH_PROFILE,
            "Unregistered dataset reference",
        )
        require(set(manifest["documents"]) == set(CODECS), "Required documents differ")
        for name, descriptor in manifest["documents"].items():
            require(
                set(descriptor) == {"parts", "sha256", "byteLength", "codec"}
                and descriptor["codec"] == CODECS[name],
                "Document codec differs",
            )
            require(
                isinstance(descriptor["parts"], list)
                and 0 < len(descriptor["parts"]) <= 2048,
                "Document instruction budget exceeded",
            )
        require(
            type(manifest["documents"]["snapshot"]["byteLength"]) is int
            and 0 < manifest["documents"]["snapshot"]["byteLength"] <= 24 * 1024 * 1024,
            "Financial snapshot exceeds budget",
        )
        for collection in manifest["collections"]:
            require(
                set(collection) == {"id", "document", "path", "rowCount", "chunks"},
                "Collection fields differ",
            )
            for chunk in collection["chunks"]:
                require(
                    set(chunk) == {"ordinal", "start", "count", "sha256", "byteLength"},
                    "Chunk descriptor fields differ",
                )

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
                    require(name in references and name not in found and GRAPH_PATHS[name] == (doc, path),
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
            for name, (expected_doc, pointer) in GRAPH_PATHS.items():
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

    def decode_json(self, raw):
        return bounded_decode(raw)

    def document_encoder(self, name):
        return financial_json if name == 'snapshot' else canonical

    def read_file(self, relative, maximum):
        path = self.directory/relative
        require(path.resolve().is_relative_to(self.directory), 'Bundle path escapes directory')
        return read_file(path, maximum)

    def validate_snapshot(self):
        snapshot = self.documents['snapshot']
        report = self.documents['report']
        reference = self.manifest['sourceEvidence']['datasetRef']
        require(set(snapshot) == SNAPSHOT_KEYS and type(snapshot['schemaVersion']) is int and snapshot['schemaVersion'] == 3
                and snapshot['fingerprintVersion'] == 'research_input_financial_column_v1'
                and snapshot['sourceEvidenceClosure'] == 'separate_research_dataset_v3', 'Unknown column snapshot discriminator')
        require(snapshot['datasetRef'] == reference and snapshot['dataFingerprint'] == self.manifest['dataFingerprint']
                and is_hash(snapshot['sourceDataFingerprint'])
                and snapshot['sourceDataFingerprint'] == snapshot['provenance']['dataFingerprint'], 'Column snapshot roots differ')
        require(snapshot['numericInput']['columns'] == Collection('snapshotColumns'), 'Snapshot columns must be chunked')
        table = {**snapshot['numericInput'], 'columns':list(self.rows('snapshotColumns'))}
        check = Checks()
        graph_audit.verify_table(table, check)
        commitment = {k:snapshot['provenance'][k] for k in ('financialCompositionVersion','marketRoot','financialDatasetRoot','financialInputs')}
        require(financial_json(commitment) == financial_json(snapshot['financialSourceCommitment'])
                == financial_json(report['provenance']['financialSourceCommitment']), 'Financial source commitment differs')
        fields = {k:graph_audit.literal(v) for k,v in snapshot.items() if k != 'numericInput'}
        fields['rows'] = lambda:graph_audit.array_stream(graph_audit.table_rows(table), check)
        logical = graph_audit.digest_stream(graph_audit.object_stream(fields), graph_audit.LOGICAL, check)
        self.numeric_table, self.logical_snapshot = table, logical
        for strategy in (report['strategy'], self.documents['forecast']['sourceStrategy']):
            require(type(strategy['schemaVersion']) is int and strategy['schemaVersion'] == 2
                    and strategy['research']['mode'] == 'statistical_quant'
                    and strategy['target']['kind'] == 'asset_price'
                    and strategy['model']['family'] == 'fundamental'
                    and strategy['model']['estimator'] == 'auto'
                    and strategy['execution']['enabled'] is False, 'Financial graph forecast-only profile differs')
            u = strategy['universe']
            graph_audit.symbols(u['symbols'], check)
            start,end = graph_audit.date(u['start'],check),graph_audit.date(u['end'],check)
            span = (datetime.strptime(end,'%Y%m%d')-datetime.strptime(start,'%Y%m%d')).days+1
            require(not u.get('selection') and 1 <= span <= 366
                    and type(strategy['model']['refitDays']) is int and strategy['model']['refitDays'] >= 20
                    and type(strategy['validation']['innerFolds']) is int and strategy['validation']['innerFolds'] == 2
                    and type(strategy['validation']['outerFolds']) is int and strategy['validation']['outerFolds'] == 2
                    and 1 <= len(strategy['factors']) <= 16 and all(f['role'] == 'predictor' for f in strategy['factors'])
                    and not any(strategy.get('dataBindings',{}).values()), 'Financial graph automatic resource rules differ')
            prediction = {k:v for k,v in strategy.items() if k not in {'execution','portfolio','costs','name','graph'}}
            require(sha(canonical(prediction)) == self.manifest['predictionConfigHash'], 'Prediction configuration identity differs')
        require(canonical(report['strategy']) == canonical(self.documents['forecast']['sourceStrategy'])
                and report['research']['executionOnly'] is False and report['metrics'] is None
                and report['execution']['enabled'] is False, 'Financial research cannot perform execution')
        require(all(self.count(name) == 0 for name in ('equity','trades','riskLedger','decisions')), 'Financial execution records forbidden')
        self.checks += check.count

    def verify_dataset(self, source, registry_pins, source_pins, expected_dataset_root):
        check = Checks()
        reference = self.manifest['sourceEvidence']['datasetRef']
        if expected_dataset_root is not None:
            require(reference['datasetRoot'] == expected_dataset_root, 'Caller-pinned source dataset root differs')
        raw, manifest, payloads = graph_audit.load_closure(source, check, reference['datasetRoot'])
        details = graph_audit.audit_semantics(manifest, payloads, check, registry_pins, source_pins)
        envelope = graph_audit.decode(payloads['researchColumns'], graph_audit.LOGICAL, check)
        snapshot = self.documents['snapshot']
        require(financial_json(snapshot['provenance']) == financial_json(envelope['provenance']), 'Snapshot provenance differs from source closure')
        table, frozen = envelope['numericInput'], self.numeric_table
        require(financial_json({k:v for k,v in table.items() if k != 'columns'})
                == financial_json({k:v for k,v in frozen.items() if k != 'columns'}), 'Snapshot logical table metadata differs')
        require(len(table['columns']) == len(frozen['columns']), 'Snapshot column count differs')
        for actual, expected in zip(frozen['columns'],table['columns'],strict=True):
            require(financial_json(actual) == financial_json(expected), 'Snapshot changed numerical source column/order/token')
        commitment = {key:envelope['provenance'][key] for key in ('financialCompositionVersion','marketRoot','financialDatasetRoot','financialInputs')}
        require(financial_json(snapshot['financialSourceCommitment']) == financial_json(commitment), 'Snapshot commitment differs from dataset')
        strategy = self.documents['forecast']['sourceStrategy']
        require({k:strategy['universe'][k] for k in ('symbols','start','end')} == manifest['scope'], 'Forecast scope differs from source dataset')
        return {'sourceEvidenceClosed':True, 'sourceViewProjectionVerified':True, 'datasetRoot':sha(raw),
                'datasetRootPinned':expected_dataset_root is not None, 'datasetChecks':check.count, 'datasetDetails':details,
                'registryTrustStatus':'external_registry_bytes_matched' if registry_pins is not None else 'unverified',
                'externalRegistryBytesMatched':registry_pins is not None, 'externalSourceBytesMatched':source_pins is not None}


@contextmanager
def financial_graph_directory(path):
    source = Path(path)
    require(not source.is_symlink(), 'Bundle input cannot be a symlink')
    if source.is_dir():
        yield source
        return
    with tempfile.TemporaryDirectory(prefix='atlas-financial-graph-audit-') as temporary:
        directory = Path(temporary)
        with _open_archive(source) as reader:
            size = _header(reader, 'manifest.json')
            _write_body(reader, directory/'manifest.json', size)
            with sqlite3.connect(':memory:') as database:
                planned = FinancialGraphBundleAudit(directory, database, planning=True).manifest
            for collection in planned['collections']:
                folder = directory/'chunks'/collection['id']
                folder.mkdir(parents=True,mode=0o700)
                for chunk in collection['chunks']:
                    name = f"chunks/{collection['id']}/{chunk['ordinal']}.json"
                    _header(reader,name,chunk['byteLength'])
                    _write_body(reader,directory/name,chunk['byteLength'],chunk['sha256'])
            reader.finish()
        yield directory


def audit_financial_graph_bundle(path, *, source_dataset=None, registry_pins=None, source_pins=None,
                                 expected_bundle_id=None, expected_dataset_root=None):
    with financial_graph_directory(path) as directory, tempfile.TemporaryDirectory(prefix='atlas-financial-graph-index-') as temporary:
        with sqlite3.connect(str(Path(temporary)/'index.sqlite')) as database:
            verifier = FinancialGraphBundleAudit(directory,database)
            if expected_bundle_id is not None:
                require(verifier.bundle_id == expected_bundle_id, 'Caller-pinned financial bundle id differs')
            result = verifier.run()
            source = {'sourceEvidenceClosed':False, 'registryTrustStatus':'unverified',
                      'datasetRootPinned':False, 'externalRegistryBytesMatched':False, 'externalSourceBytesMatched':False}
            if source_dataset is not None:
                source = verifier.verify_dataset(source_dataset,registry_pins,source_pins,expected_dataset_root)
            return {**result, **source, 'status':'PASS' if source['sourceEvidenceClosed'] else 'INCOMPLETE_SOURCE',
                'auditor':'atlas.financial_graph_bundle.stdlib_audit/1', 'bundleVersion':2, 'bundleIdPinned':expected_bundle_id is not None,
                'transportVerified':True, 'exactSnapshotTokensVerified':True, 'snapshotRows':verifier.numeric_table['rowCount'],
                'logicalExpandedSnapshot':verifier.logical_snapshot, 'snapshotPhysicalBytes':verifier.manifest['documents']['snapshot']['byteLength'],
                'recomposition':'not_performed', 'sourceResearchFingerprintRecomputed':False,
                'sourceAuthorityVerified':False, 'financialFormulasRecomputed':False,
                'pdfAuthenticityVerified':False, 'providerAuthenticityVerified':False,
                'modelAdmissionRegistered':False, 'modelFitted':False, 'providerCalls':0,
                'modelFunctionsReevaluated':False, 'researchStatisticsRecomputed':False,
                'forecastNumericalIdentitiesVerified':True,
                'limitations':[
                    'Result/source hashes and external pins must be independently authorized; self-consistent archives do not grant authority.',
                    'Exact column snapshot, paired source closure and numerical forecast identities are checked; financial formulas and F are not recomputed.',
                    'No supplier, PDF, original publication/revision authenticity, model admission or hosted acceptance is established.',
                    'Research/provider pandas fingerprints are not rederived; complete frozen source rows, provenance and commitments are checked.',
                    'Full callable-F evaluation and research statistics validation remain separate checks; this audit preserves and hashes all supplied forecast/model/statistics collections.'
                ]}
