"""Local financial bundle/2: exact bounded column records, separate source/3.

This is not registered with hosted delivery. All numerical source cells travel
inside the snapshot, while original raw evidence remains in the source archive.
"""
from copy import deepcopy
from pathlib import Path
import hashlib

from . import bundle as legacy
from .financial_bundle import (FinancialBundleReader as PreviousReader,decode_exact,require,canonical_parts)
from .research_dataset.codec import encode as financial_encode
from .research_dataset.graph_v3.snapshot import (SNAPSHOT_KEYS,RESEARCH_PROFILE,dataset_reference,
    validate_snapshot,validate_research_profile,restore_graph_input)
from .research_dataset.graph_v3.manifest import GRAPH_PROFILE_ID
from .statistical_quant.schema import prediction_config,validate as validate_strategy

FORMAT='atlas.quant.financial_bundle'
VERSION=2
CAPABILITY=FORMAT+'/2'
SNAPSHOT_LIMIT=24*1024**2
CODECS={"forecast":"forecast_json_v1","report":"forecast_json_v1","coverage":"forecast_json_v1",
        "snapshot":"financial_column_snapshot_v1"}
COLLECTIONS={k:v for k,v in legacy.COLLECTIONS.items() if k!='snapshotRows'} | {
    'snapshotColumns':('snapshot','/numericInput/columns')}
# Layout validation uses the same bounded primitives, not the old format's
# registry or identity. The new collection is explicitly declared here.
MANIFEST_LIMIT=legacy.MANIFEST_LIMIT;CHUNK_LIMIT=legacy.CHUNK_LIMIT;CHUNK_ROW_LIMIT=legacy.CHUNK_ROW_LIMIT
CHUNK_COUNT_LIMIT=legacy.CHUNK_COUNT_LIMIT;BUNDLE_LIMIT=legacy.BUNDLE_LIMIT;TOTAL_ROW_LIMIT=legacy.TOTAL_ROW_LIMIT
HASH=legacy.HASH;fail=legacy.fail;sha=legacy.sha;decode=legacy.decode;encode=legacy.encode
_integer=legacy._integer;_path=legacy._path;_markers=legacy._markers;document_skeleton=legacy.document_skeleton


def validate_source_evidence(value):
    require(type(value) is dict and set(value)=={'datasetRef','admissionProfile'},'Exact graph source evidence required')
    dataset_reference(value['datasetRef'])
    require(value['admissionProfile']==RESEARCH_PROFILE,'Only explicit local graph-auto/1 admission is supported')
    return deepcopy(value)


def _validate_layout(raw, expected_id=None):
    if not isinstance(raw, bytes) or len(raw) > MANIFEST_LIMIT:
        fail("BUNDLE_MANIFEST_SIZE", "传输清单超过大小限制。")
    if expected_id is not None and sha(raw) != expected_id:
        fail("BUNDLE_INTEGRITY", "传输清单身份不匹配。")
    try:
        manifest = decode(raw)
        if encode(manifest) != raw:
            raise ValueError()
        if set(manifest) != {"format", "version", "kind", "forecastArtifactId", "predictionConfigHash", "dataFingerprint", "documents", "collections", "totals", "sourceEvidence"}:
            raise ValueError()
        if manifest["format"] != FORMAT or type(manifest["version"]) is not int or manifest["version"] != VERSION or manifest["kind"] != "forecast":
            raise ValueError()
        if not all(isinstance(manifest[k], str) and HASH.fullmatch(manifest[k]) for k in ("forecastArtifactId", "predictionConfigHash", "dataFingerprint")):
            raise ValueError()
        names = {"forecast", "report", "coverage"} | ({"snapshot"} if manifest["kind"] == "forecast" else set())
        if set(manifest["documents"]) != names or not isinstance(manifest["collections"], list):
            raise ValueError()
        collections, chunk_count, chunk_bytes, total_rows = {}, 0, 0, 0
        for info in manifest["collections"]:
            if set(info) != {"id", "document", "path", "rowCount", "chunks"} or info["id"] in collections:
                raise ValueError()
            if COLLECTIONS.get(info["id"]) != (info["document"], info["path"]) or not _integer(info["rowCount"]) or not isinstance(info["chunks"], list):
                raise ValueError()
            collections[info["id"]] = info
            total_rows += info["rowCount"]
            count = 0
            for ordinal, descriptor in enumerate(info["chunks"]):
                if set(descriptor) != {"ordinal", "start", "count", "sha256", "byteLength"}:
                    raise ValueError()
                if (not _integer(descriptor["ordinal"]) or not _integer(descriptor["start"])
                        or descriptor["ordinal"] != ordinal or descriptor["start"] != count
                        or not _integer(descriptor["count"], 1) or descriptor["count"] > CHUNK_ROW_LIMIT):
                    raise ValueError()
                if not _integer(descriptor["byteLength"], 2) or descriptor["byteLength"] > CHUNK_LIMIT or not HASH.fullmatch(descriptor["sha256"]):
                    raise ValueError()
                count += descriptor["count"]
                chunk_count += 1
                chunk_bytes += descriptor["byteLength"]
            if count != info["rowCount"]:
                raise ValueError()
        if (manifest["totals"] != {"chunkCount": chunk_count, "chunkBytes": chunk_bytes}
                or not all(_integer(value) for value in manifest["totals"].values())):
            raise ValueError()
        if chunk_count > CHUNK_COUNT_LIMIT or chunk_bytes + len(raw) > BUNDLE_LIMIT or total_rows > TOTAL_ROW_LIMIT:
            fail("BUNDLE_SIZE", "传输集合超过总资源预算。")
        referenced = []
        for name, document in manifest["documents"].items():
            if set(document) != {"codec", "parts", "sha256", "byteLength"} or document.get("codec") != CODECS[name] or not isinstance(document["parts"], list) or not HASH.fullmatch(document["sha256"]) or not _integer(document["byteLength"], 2):
                raise ValueError()
            document_refs = 0
            for part in document["parts"]:
                if set(part) == {"literal"} and isinstance(part["literal"], str):
                    pass
                elif set(part) == {"collection"} and part["collection"] in collections and collections[part["collection"]]["document"] == name:
                    referenced.append(part["collection"])
                elif name == "report" and part == {"document": "forecast", "wrapArtifactId": manifest["forecastArtifactId"]}:
                    document_refs += 1
                else:
                    raise ValueError()
            skeleton = document_skeleton(manifest, name)
            for info in collections.values():
                if info["document"] == name and _path(skeleton, info["path"]) != {"__bundle_collection__": info["id"]}:
                    raise ValueError()
            if name == "report" and skeleton.get("forecasts") != {"__bundle_document__": "forecast"}:
                raise ValueError()
            if document_refs != (1 if name == "report" else 0):
                raise ValueError()
            expected_markers = [(info["path"], "__bundle_collection__", info["id"])
                                for info in collections.values() if info["document"] == name]
            if name == "report":
                expected_markers.append(("/forecasts", "__bundle_document__", "forecast"))
            if sorted(_markers(skeleton)) != sorted(expected_markers):
                raise ValueError()
        if sorted(referenced) != sorted(collections):
            raise ValueError()
        required = {"forecasts", "targets", "modelFits", "hedgeFits", "equity", "trades", "riskLedger", "decisions", "plannedOrigins"}
        if manifest["kind"] == "forecast":
            required.add("snapshotColumns")
        if not required <= set(collections) or manifest["documents"]["forecast"]["sha256"] != manifest["forecastArtifactId"]:
            raise ValueError()
        return manifest
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        if getattr(exc, "code", None):
            raise
        fail("BUNDLE_FORMAT", "分片清单格式或布局无效。")


def validate_manifest(raw,expected_id=None):
    manifest=_validate_layout(raw,expected_id)
    validate_source_evidence(manifest['sourceEvidence'])
    require(type(manifest['documents']['snapshot']['byteLength']) is int and
            manifest['documents']['snapshot']['byteLength']<=SNAPSHOT_LIMIT,'Column snapshot exceeds unchanged byte limit')
    return manifest


def validate_metadata(manifest):
    report=legacy.document_skeleton(manifest,'report');forecast=legacy.document_skeleton(manifest,'forecast')
    snapshot=legacy.document_skeleton(manifest,'snapshot')
    require(set(snapshot)==SNAPSHOT_KEYS and type(snapshot['schemaVersion']) is int and snapshot['schemaVersion']==3
            and snapshot['fingerprintVersion']=='research_input_financial_column_v1'
            and snapshot['sourceEvidenceClosure']=='separate_research_dataset_v3','Column snapshot discriminator differs')
    require(snapshot['datasetRef']==manifest['sourceEvidence']['datasetRef']
            and snapshot['dataFingerprint']==manifest['dataFingerprint']
            and snapshot['sourceDataFingerprint']==snapshot['provenance'].get('dataFingerprint')
            and report['provenance'].get('dataSha256')==manifest['dataFingerprint']
            and report['provenance'].get('financialSourceCommitment')==snapshot['financialSourceCommitment'],
            'Financial source roots or commitment differ')
    for config in (report.get('strategy'),forecast.get('sourceStrategy')):
        require(type(config) is dict and config.get('execution',{}).get('enabled') is False,'Explicit forecast-only research required')
        normalized=validate_research_profile(config,{k:config['universe'][k] for k in ('symbols','start','end')},
            research_profile=manifest['sourceEvidence']['admissionProfile'])
        require(legacy.sha(legacy.encode(prediction_config(normalized)))==manifest['predictionConfigHash'],'Prediction configuration differs')
    require(legacy.encode(report['strategy'])==legacy.encode(forecast['sourceStrategy']) and report.get('schemaVersion')==2
            and report.get('research',{}).get('executionOnly') is False and report.get('metrics') is None
            and report.get('execution',{}).get('forecastArtifactId')==manifest['forecastArtifactId'],
            'Financial report must remain forecast only')
    collections={x['id']:x for x in manifest['collections']}
    require(all(collections[k]['rowCount']==0 for k in ('equity','trades','riskLedger','decisions')),'Financial execution rows forbidden')
    return snapshot


class FinancialGraphBundleReader(PreviousReader):
    def __init__(self,manifest_raw,read_chunk,expected_id=None):
        self._manifest=validate_manifest(manifest_raw,expected_id);self._raw=manifest_raw;self._read=read_chunk
        self._collections={c['id']:c for c in self._manifest['collections']}

    def rows(self,collection):
        for descriptor in self._collections[collection]['chunks']:
            raw=self._read(collection,descriptor['ordinal'])
            require(isinstance(raw,bytes) and len(raw)==descriptor['byteLength'] and legacy.sha(raw)==descriptor['sha256'],
                    'Chunk byte identity differs','FINANCIAL_BUNDLE_INTEGRITY')
            values=decode_exact(raw,legacy.CHUNK_LIMIT,financial=collection=='snapshotColumns')
            require(type(values) is list and len(values)==descriptor['count'] and all(type(v) is dict for v in values),
                    'Chunk rows differ')
            yield from values

    def verify_integrity(self):
        # Base checks only forecast identity, model refs, frozen coverage and
        # full document hashes; all snapshot/source profile checks stay here.
        result=legacy.BundleReader.verify_integrity(self)
        validate_metadata(self._manifest)
        validate_snapshot(self.document('snapshot'))
        return {**result,'transportVerified':True,'sourceEvidenceClosed':False,'registryTrustStatus':'unverified',
                'recomposition':'not_performed','status':'INCOMPLETE_SOURCE','modelAdmissionRegistered':False}

    def restore_sources(self,dataset_reader,authorized_registry):
        self.verify_integrity();evidence=self._manifest['sourceEvidence']
        require(dataset_reader.dataset_root==evidence['datasetRef']['datasetRoot']
                and dataset_reader.manifest['version']==3 and dataset_reader.manifest['profile']==GRAPH_PROFILE_ID,
                'Source graph sidecar differs')
        strategy=legacy.document_skeleton(self._manifest,'forecast')['sourceStrategy']
        result=restore_graph_input(strategy,self.snapshot_bytes(),dataset_reader,authorized_registry,
                                   research_profile=evidence['admissionProfile'])
        from .research_dataset.graph_v3.coverage import verify_source_coverage
        verify_source_coverage(self,strategy,result,dataset_reader.manifest['scope'],research_profile=evidence['admissionProfile'])
        return result


def build_financial_graph_bundle(
    report,
    snapshot_raw,
    coverage,
    source_evidence,
    write_chunk,
    read_chunk,
    *,
    chunk_target=legacy.CHUNK_TARGET,
):
    """Write bounded chunks; only return a manifest after full integrity checks."""
    source_evidence = validate_source_evidence(source_evidence)
    snapshot = decode_exact(snapshot_raw, SNAPSHOT_LIMIT, financial=True)
    validate_snapshot(snapshot)
    if not isinstance(report, dict) or not isinstance(report.get("forecasts"), dict):
        legacy.fail("BUNDLE_FORMAT", "需要完整预测研究报告。")
    if not legacy._integer(chunk_target, 2) or chunk_target > legacy.CHUNK_LIMIT:
        legacy.fail("BUNDLE_BUDGET", "分片目标大小无效。")
    artifact = report["forecasts"]
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "sourceEvidence": source_evidence,
        "kind": "forecast",
        "forecastArtifactId": artifact["artifactId"],
        "predictionConfigHash": artifact["predictionConfigHash"],
        "dataFingerprint": artifact["dataFingerprint"],
        "documents": {},
        "collections": [],
        "totals": {"chunkCount": 0, "chunkBytes": 0},
    }
    documents = {
        "forecast": {k: v for k, v in artifact.items() if k != "artifactId"},
        "report": report,
        "coverage": coverage,
    }
    if snapshot is not None:
        documents["snapshot"] = snapshot
    elif manifest["kind"] == "forecast":
        legacy.fail("BUNDLE_FORMAT", "新预测分片缺少冻结输入。")
    lookup = {pair: name for name, pair in COLLECTIONS.items()}

    def collection(name, document, pointer, values):
        if not isinstance(values, list):
            legacy.fail("BUNDLE_FORMAT", "分片集合必须为数组。")
        info = {
            "id": name,
            "document": document,
            "path": pointer,
            "rowCount": len(values),
            "chunks": [],
        }
        pending, size, start = [], 2, 0

        def flush():
            nonlocal pending, size, start
            if not pending:
                return
            raw = b"[" + b",".join(pending) + b"]"
            totals = manifest["totals"]
            totals["chunkCount"] += 1
            totals["chunkBytes"] += len(raw)
            if (
                totals["chunkCount"] > legacy.CHUNK_COUNT_LIMIT
                or totals["chunkBytes"] > legacy.BUNDLE_LIMIT
            ):
                legacy.fail("BUNDLE_SIZE", "完整分片超过总资源预算；没有截断输出。")
            descriptor = {
                "ordinal": len(info["chunks"]),
                "start": start,
                "count": len(pending),
                "sha256": legacy.sha(raw),
                "byteLength": len(raw),
            }
            write_chunk(name, descriptor["ordinal"], raw)
            info["chunks"].append(descriptor)
            start += len(pending)
            pending, size = [], 2

        for value in values:
            if not isinstance(value, dict):
                legacy.fail("BUNDLE_FORMAT", "研究集合的每项必须为对象。")
            raw = encoder(value)
            if len(raw) + 2 > legacy.CHUNK_LIMIT:
                legacy.fail(
                    "BUNDLE_ITEM_SIZE", "单条研究记录超过分片硬上限；没有切断记录。"
                )
            if pending and (
                size + 1 + len(raw) > chunk_target
                or len(pending) >= legacy.CHUNK_ROW_LIMIT
            ):
                flush()
            size += len(raw) + bool(pending)
            pending.append(raw)
        flush()
        manifest["collections"].append(info)

    for document, root in documents.items():
        encoder = financial_encode if document == "snapshot" else legacy.encode
        parts = []

        def literal(raw):
            value = raw.decode() if isinstance(raw, bytes) else raw
            if parts and "literal" in parts[-1]:
                parts[-1]["literal"] += value
            else:
                parts.append({"literal": value})

        def walk(value, pointer=""):
            if document == "report" and pointer == "/forecasts":
                parts.append(
                    {"document": "forecast", "wrapArtifactId": artifact["artifactId"]}
                )
            elif (document, pointer) in lookup:
                name = lookup[(document, pointer)]
                collection(name, document, pointer, value)
                parts.append({"collection": name})
            elif isinstance(value, dict):
                literal("{")
                for i, key in enumerate(sorted(value)):
                    if i:
                        literal(",")
                    literal(encoder(key) + b":")
                    walk(
                        value[key],
                        pointer + "/" + key.replace("~", "~0").replace("/", "~1"),
                    )
                literal("}")
            else:
                literal(encoder(value))

        walk(root)
        manifest["documents"][document] = {"codec": CODECS[document], "parts": parts}
    for name, definition in manifest["documents"].items():
        h, length = hashlib.sha256(), 0
        for raw in legacy.iter_document_bytes(manifest, name, read_chunk):
            h.update(raw)
            length += len(raw)
        definition.update(sha256=h.hexdigest(), byteLength=length)
    raw = legacy.encode(manifest)
    validate_manifest(raw)
    if manifest["documents"]["forecast"]["sha256"] != artifact["artifactId"]:
        legacy.fail("BUNDLE_INTEGRITY", "分片重建未保留原预测身份。")
    reader = FinancialGraphBundleReader(raw, read_chunk)
    reader.verify_integrity()
    require(
        reader.snapshot_bytes() == snapshot_raw,
        "Snapshot bytes changed during transport",
        "FINANCIAL_BUNDLE_INTEGRITY",
    )
    return raw


def financial_graph_directory_reader(directory):
    from .research_dataset.reader import _read_file
    root=Path(directory).absolute()
    require(root.is_dir() and not root.is_symlink(),'Explicit local graph bundle directory required')
    raw=_read_file(root/'manifest.json',legacy.MANIFEST_LIMIT)
    reader=FinancialGraphBundleReader(raw,lambda c,n:_read_file(root/'chunks'/c/f'{n}.json',legacy.CHUNK_LIMIT))
    require({p.name for p in root.iterdir()}=={'manifest.json','chunks'},'Unregistered local bundle entries')
    chunks=root/'chunks';require(chunks.is_dir() and not chunks.is_symlink(),'Chunk directory cannot be symlink')
    require({p.name for p in chunks.iterdir()}==set(reader.collections),'Unregistered chunk collections')
    for name,item in reader.collections.items():
        folder=chunks/name
        require(folder.is_dir() and not folder.is_symlink() and {p.name for p in folder.iterdir()}=={
            f"{i}.json" for i in range(len(item['chunks']))},'Missing or unregistered chunk files')
    return reader


def export_financial_graph_bundle(reader,directory):
    require(type(reader) is FinancialGraphBundleReader,'Financial graph reader required')
    reader.verify_integrity();legacy.export_bundle(reader,directory)


def export_financial_graph_archive(reader,destination):
    """Exact local USTAR, atomically published without replacing any evidence."""
    import os,tempfile
    from .research_dataset.archive import BLOCK,header,_publish,_sync
    require(type(reader) is FinancialGraphBundleReader,'Financial graph reader required')
    reader.verify_integrity();destination=Path(destination).absolute()
    require(not os.path.lexists(destination),'Archive output must be new')
    descriptor,name=tempfile.mkstemp(prefix='.atlas-financial2-',dir=destination.parent)
    temporary=Path(name);published=False
    try:
        with os.fdopen(descriptor,'wb') as stream:
            def member(name,raw):
                stream.write(header(name,len(raw)));stream.write(raw);stream.write(bytes((-len(raw))%BLOCK))
            member('manifest.json',reader.manifest_raw)
            for collection in reader.manifest['collections']:
                for part in collection['chunks']:
                    raw=reader.read_chunk(collection['id'],part['ordinal'])
                    require(len(raw)==part['byteLength'] and legacy.sha(raw)==part['sha256'],'Chunk changed while exporting archive')
                    member(f"chunks/{collection['id']}/{part['ordinal']}.json",raw)
            stream.write(bytes(2*BLOCK));stream.flush();os.fsync(stream.fileno())
        size=temporary.stat().st_size;_publish(temporary,destination);published=True
        result={'bundleId':reader.bundle_id,'archiveBytes':size,'format':'atlas-financial-graph-ustar-v1',
                'transportVerified':True,'sourceEvidenceClosed':False}
        try:_sync(destination.parent)
        except OSError:result['warnings']=['PARENT_DIRECTORY_FSYNC_UNAVAILABLE']
        return result
    finally:
        if not published:temporary.unlink(missing_ok=True)
