"""Offline graph-source publication/recomposition; intentionally no F admission."""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ..codec import decode,digest,encode,keys,require,sha,uuid
from ..compose import FinancialSource
from ..profile import DEFAULT_PROFILE,FORMAT,check_profile
from ..reader import DatasetReader,_freeze,_read_file
from ..snapshot_view import SnapshotMarketView,validate_snapshot_scope_origin
from .manifest import GRAPH_PROFILE_ID,GRAPH_VERSION,component_root,validate_manifest
from .source import GraphSourceResult,prepare_source
from .prepared import decode_graph
from .columns import verify_table,iter_rows
from .streams import array_chunks,object_chunks,literal,stream_digest,ROW_LIMIT,canonical_chunks


@dataclass(frozen=True)
class GraphPublication:
    manifest_bytes: bytes
    result: GraphSourceResult

    @property
    def dataset_root(self):return sha(self.manifest_bytes)


def compose_graph_dataset_components(origin_view,financial_sources,authorized_registry,write_part,
                                     *,market_calendar_ref,profile=DEFAULT_PROFILE):
    check_profile(profile)
    require(type(origin_view) is SnapshotMarketView,"DATASET_VIEW_ORIGIN","An exact frozen market view is required")
    checked=validate_snapshot_scope_origin(origin_view.origin_bytes,profile=profile)
    require(checked.market_bytes==origin_view.market_bytes,"DATASET_VIEW_ORIGIN","Market view differs from exact original projection")
    scope_value=checked.receipt["targetScope"];market_bytes=checked.market_bytes
    closure=prepare_source(scope_value,market_bytes,financial_sources,authorized_registry,market_calendar_ref,
                           profile=profile,retained_origin_bytes=len(checked.origin_bytes))
    sources=closure.sources;registry_raw=closure.registry_bytes;result=closure.result
    payloads, components = [], []
    total, count = 0, 0

    def add(name, kind, raw, roots, dependencies):
        nonlocal total, count
        require(
            isinstance(raw, bytes) and raw, "DATASET_BYTES", "Component bytes missing"
        )
        needed_parts = (len(raw) + profile.part_bytes - 1) // profile.part_bytes
        require(
            total + len(raw) + profile.manifest_bytes <= profile.total_bytes
            and count + needed_parts <= profile.max_parts,
            "DATASET_BUDGET",
            "Known payload exceeds remaining bytes/parts before descriptor allocation",
        )
        parts = [
            {
                "ordinal": i,
                "byteLength": len(raw[offset : offset + profile.part_bytes]),
                "sha256": sha(raw[offset : offset + profile.part_bytes]),
            }
            for i, offset in enumerate(range(0, len(raw), profile.part_bytes))
        ]
        total += len(raw)
        count += len(parts)
        require(
            total + profile.manifest_bytes <= profile.total_bytes
            and count <= profile.max_parts,
            "DATASET_BUDGET",
            "Complete source/derived closure exceeds profile",
        )
        item = {
            "componentId": name,
            "type": kind,
            "version": 1,
            "semanticRoots": roots,
            "encoding": "raw_bytes",
            "payloadSha256": sha(raw),
            "byteLength": len(raw),
            "dependencies": sorted(dependencies),
            "parts": parts,
        }
        item["componentRoot"] = component_root(item)
        components.append(item)
        payloads.append((name, raw))
        return item["componentRoot"]

    registry_root = add("registryEvidence", "registry_evidence", registry_raw, {}, [])
    market_dependencies = [registry_root]
    if origin_view is not None:
        receipt = origin_view.receipt
        require(
            receipt["marketRoot"] == result.provenance["marketRoot"],
            "DATASET_ROOT",
            "Origin market bytes disagree with composition",
        )
        origin = decode(origin_view.origin_bytes, profile.total_bytes)
        market_dependencies.append(
            add(
                "marketOrigin",
                "snapshot_scope_origin",
                origin_view.origin_bytes,
                {
                    "sourceBundleId": origin["source"]["bundleId"],
                    "sourceSnapshotSha256": origin["source"]["snapshotSha256"],
                    "marketRoot": receipt["marketRoot"],
                },
                [],
            )
        )
    market_root = add(
        "marketDataset",
        "market_dataset",
        market_bytes,
        {"marketRoot": result.provenance["marketRoot"]},
        market_dependencies,
    )
    prepared_roots, source_refs = [], []
    for i, (source, graph_raw, summary) in enumerate(zip(sources, closure.graph_payloads, result.financial_summaries)):
        package = decode(source.package_bytes, profile.package_bytes)
        graph = decode(graph_raw, profile.total_bytes)
        input_root = add(
            f"financialInput{i}",
            "financial_input",
            source.package_bytes,
            {
                "inputRoot": package["inputRoot"],
                "packRoot": package["packRoot"],
            },
            [registry_root],
        )
        prepared_roots.append(
            add(
                f"financialGraph{i}",
                "financial_prepared_graph",
                graph_raw,
                {
                    "packRoot": package["packRoot"],
                    "preparedRoot": source.prepared_root,
                    "calendarRoot": summary["calendarRoot"],
                    "preparedPayloadSha256":graph["logicalPrepared"]["sha256"],
                },
                [input_root],
            )
        )
        source_refs.append(
            {
                "componentId": f"financialInput{i}",
                "calendarRef": source.calendar_ref,
                "proofRefs": list(source.proof_refs),
                "preparedRoot": source.prepared_root,
            }
        )
    dependencies = [market_root, *prepared_roots]
    joined = closure.columns_bytes
    require(
        len(joined) <= profile.joined_bytes,
        "DATASET_BUDGET",
        "Joined data exceeds profile",
    )
    add(
        "researchColumns",
        "research_columns",
        joined,
        {"financialDatasetRoot": result.provenance["financialDatasetRoot"],"logicalJoinedSha256":result.logical_joined["sha256"]},
        dependencies,
    )
    add(
        "schema",
        "dataset_schema",
        encode(
            {
                "columns": list(result.data.columns),
                "externalFields": result.provenance["externalFields"],
                "calendarSessions": result.provenance["tradingDates"],
            }
        ),
        {},
        dependencies,
    )
    add(
        "coverage",
        "dataset_coverage",
        encode(
            {
                "marketRows": len(result.data),
                "observedSymbols": sorted(result.data.ts_code.unique().tolist()),
                "financial": list(result.financial_summaries),
            }
        ),
        {},
        dependencies,
    )
    manifest = {
        "format": FORMAT,
        "version": GRAPH_VERSION,
        "profile": GRAPH_PROFILE_ID,
        "scope": deepcopy(scope_value),
        "marketCalendarRef": market_calendar_ref,
        "financialSources": source_refs,
        "components": components,
        "roots": {
            k: result.provenance[k] for k in ("marketRoot", "financialDatasetRoot")
        },
    }
    raw_manifest = encode(manifest)
    validate_manifest(raw_manifest, profile=profile)
    # All allocations, count/byte budgets and identities are checked before the
    # first externally visible staging callback. Failed writes return no manifest.
    for name, raw in payloads:
        for ordinal, offset in enumerate(range(0, len(raw), profile.part_bytes)):
            write_part(name, ordinal, raw[offset : offset + profile.part_bytes])
    return GraphPublication(raw_manifest, result)


class GraphDatasetReader:
    # Reuse only the immutable byte access implementation, never the old
    # version-dispatch constructor/validator or the old admission path.
    manifest_bytes=DatasetReader.manifest_bytes
    dataset_root=DatasetReader.dataset_root
    profile=DatasetReader.profile
    manifest=DatasetReader.manifest
    components=DatasetReader.components
    part=DatasetReader.part
    payload=DatasetReader.payload

    def __init__(self,manifest_bytes,read_part,*,expected_root=None,profile=DEFAULT_PROFILE):
        value=validate_manifest(manifest_bytes,expected_root=expected_root,profile=profile)
        self._profile=profile;self._manifest_bytes=manifest_bytes;self._manifest=_freeze(value)
        self._dataset_root=sha(manifest_bytes);self._read_part=read_part
        self._components=MappingProxyType({c["componentId"]:c for c in self._manifest["components"]})

    def verify_integrity(self):
        for name,item in self._components.items():
            raw=self.payload(name)
            if item["type"]=="financial_prepared_graph":
                roots=item["semanticRoots"]
                graph=decode_graph(raw,expected_prepared_root=roots["preparedRoot"],
                                   expected_payload_sha256=roots["preparedPayloadSha256"])
                require(graph["provenance"]["calendar"]["root"]==roots["calendarRoot"],
                        "DATASET_ROOT","Graph calendar root differs from descriptor")
            elif name=="researchColumns":
                envelope=decode(raw,self.profile.joined_bytes)
                verify_numeric_envelope(envelope,self.profile)
                require(envelope["provenance"]["financialDatasetRoot"]==item["semanticRoots"]["financialDatasetRoot"]
                        and envelope["logicalJoined"]["sha256"]==item["semanticRoots"]["logicalJoinedSha256"],
                        "DATASET_ROOT","Column descriptor semantic roots differ")
            else:decode(raw,self.profile.total_bytes)
        view=validate_snapshot_scope_origin(self.payload("marketOrigin"),profile=self.profile)
        origin=decode(view.origin_bytes,self.profile.total_bytes)
        require(dict(self._components["marketOrigin"]["semanticRoots"])=={
            "sourceBundleId":origin["source"]["bundleId"],"sourceSnapshotSha256":origin["source"]["snapshotSha256"],
            "marketRoot":view.receipt["marketRoot"]},"DATASET_ROOT","Original market semantic roots differ")
        require(view.market_bytes==self.payload("marketDataset"),"DATASET_VIEW_ORIGIN","Market is not the frozen source projection")
        return {"datasetRoot":self.dataset_root,"transportVerified":True,"sourceAuthorityVerified":False,
                "financialRecomputed":False,"modelAdmissionRegistered":False,
                "components":len(self._components),"parts":sum(len(c["parts"]) for c in self._components.values())}


def verify_numeric_envelope(value,profile=DEFAULT_PROFILE):
    check_profile(profile);keys(value,{"schemaVersion","numericInput","provenance","logicalJoined"})
    require(type(value["schemaVersion"]) is int and value["schemaVersion"]==1,"DATASET_FORMAT","Unsupported numeric envelope")
    require(isinstance(value["provenance"],dict),"DATASET_SHAPE","Exact provenance object required")
    keys(value["logicalJoined"],{"sha256","byteLength"});digest(value["logicalJoined"]["sha256"])
    require(type(value["logicalJoined"]["byteLength"]) is int and 1<=value["logicalJoined"]["byteLength"]<=profile.joined_bytes,
            "DATASET_BUDGET","Logical joined descriptor exceeds original limit")
    stream_digest(canonical_chunks(value),profile.joined_bytes)
    verify_table(value["numericInput"])
    logical=stream_digest(object_chunks({"schemaVersion":lambda:literal(1),
        "rows":lambda:array_chunks(literal(row,ROW_LIMIT) for row in iter_rows(value["numericInput"])),
        "provenance":lambda:literal(value["provenance"],profile.joined_bytes)}),profile.joined_bytes)
    require(logical==value["logicalJoined"],"DATASET_ROOT","Entire joined rows and provenance differ from their logical identity")
    return logical


class DirectoryGraphDatasetReader(GraphDatasetReader):
    def __init__(self,directory,*,expected_root=None,profile=DEFAULT_PROFILE):
        check_profile(profile);self.directory=Path(directory).absolute()
        require(self.directory.is_dir() and not self.directory.is_symlink(),"DATASET_PATH","Dataset directory cannot be a symlink")
        raw=_read_file(self.directory/"manifest.json",profile.manifest_bytes)
        super().__init__(raw,self._part_file,expected_root=expected_root,profile=profile)
        require({p.name for p in self.directory.iterdir()}=={"manifest.json","parts"},"DATASET_PATH","Unregistered dataset entries")
        parts=self.directory/"parts"
        require(parts.is_dir() and not parts.is_symlink() and {p.name for p in parts.iterdir()}==set(self._components),
                "DATASET_PATH","Missing or unregistered component directories")
        for name,item in self._components.items():
            folder=parts/name
            require(folder.is_dir() and not folder.is_symlink() and {p.name for p in folder.iterdir()}=={
                f"{i}.bin" for i in range(len(item["parts"]))},"DATASET_PATH","Missing or extra component parts")

    def _part_file(self,name,ordinal):
        return _read_file(self.directory/"parts"/name/f"{ordinal}.bin",self.profile.part_bytes)


def restore_graph_dataset(reader,authorized_registry):
    require(isinstance(reader,GraphDatasetReader),"DATASET_READER","A graph dataset reader is required")
    reader.verify_integrity()
    registry=decode(reader.payload("registryEvidence"),reader.profile.total_bytes);keys(registry,{"entries"})
    require(type(registry["entries"]) is list and len(registry["entries"])<=2057,"DATASET_REGISTRY","Invalid registry entry count")
    stored={}
    for entry in registry["entries"]:
        keys(entry,{"ref","sha256","byteLength","rawText"});ref=uuid(entry["ref"])
        require(ref not in stored and type(entry["rawText"]) is str,"DATASET_REGISTRY","Duplicate or invalid registry identity")
        raw=entry["rawText"].encode("utf-8")
        require(type(entry["byteLength"]) is int and len(raw)==entry["byteLength"] and
                len(raw)<=reader.profile.registry_bytes and sha(raw)==entry["sha256"],"DATASET_REGISTRY","Registry bytes differ")
        stored[ref]=raw
    require(type(authorized_registry) is dict and stored==authorized_registry,
            "DATASET_AUTHORITY","Archive cannot authorize itself; exact independent registry bytes required")
    manifest=reader.manifest
    sources=[FinancialSource(reader.payload(s["componentId"]),s["preparedRoot"],s["calendarRef"],tuple(s["proofRefs"]))
             for s in manifest["financialSources"]]
    def compare(name,ordinal,raw):
        require(raw==reader.part(name,ordinal),"DATASET_RECOMPUTATION","Fresh source-derived bytes differ from archive")
    publication=compose_graph_dataset_components(SnapshotMarketView(reader.payload("marketDataset"),reader.payload("marketOrigin")),
        sources,authorized_registry,compare,market_calendar_ref=manifest["marketCalendarRef"],profile=reader.profile)
    require(publication.manifest_bytes==reader.manifest_bytes,"DATASET_RECOMPUTATION","Fresh complete graph closure differs")
    return publication.result
