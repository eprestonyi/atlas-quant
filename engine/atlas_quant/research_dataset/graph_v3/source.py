"""Fresh raw-package preparation for dataset/3, with no model admission.

Only compact graph bytes persist between packages. Original prepared/event,
joined and physical closure guards are independent and never enlarged.
"""
from copy import deepcopy
from dataclasses import dataclass
import pandas as pd

from ...financial_runner.trust import _registry,calendar_scope,resolve_package_registry
from ...financial_statements.dataset import summarize_prepared,_bounded_rows
from ...financial_statements.package import _private_content,prepare_package
from ...financial_statements.prepare import AdapterBudget,_safe_rows
from ...provider import _records,_validate_panel
from ..codec import decode,digest,encode,keys,require,sha,uuid
from ..compose import FinancialSource,registry_payload
from .manifest import scope
from ..profile import DEFAULT_PROFILE,check_profile
from .prepared import encode_graph,iter_panel_rows
from .columns import encode_table
from .streams import canonical_chunks,stream_digest,PREPARED_LIMIT


@dataclass(frozen=True)
class GraphSourceResult:
    data: pd.DataFrame
    provenance: dict
    financial_summaries: tuple
    logical_joined: dict
    model_admission_registered: bool = False

    @property
    def validation_report(self):
        return {"sourceAuthorityVerified":True,"financialRecomputed":True,"modelAdmissionRegistered":False,
            "authorityMeaning":"independently_authorized_registry_bytes_and_recomputed_frozen_packages",
            "synthetic":self.provenance["synthetic"],"originalAsPublishedVerified":False,
            "completeHistoricalVersionsVerified":False,"revisionTimeVerified":False}

    def to_dataset(self):
        return {"schemaVersion":1,"rows":_safe_rows(self.data),"provenance":deepcopy(self.provenance)}


@dataclass(frozen=True)
class PreparedClosure:
    sources: tuple
    registry_bytes: bytes
    graph_payloads: tuple
    result: GraphSourceResult
    columns_bytes: bytes


def prepare_source(scope_value,market_bytes,sources,authorized_registry,market_calendar_ref,
                   *,profile=DEFAULT_PROFILE,retained_origin_bytes=0):
    check_profile(profile)
    scope(scope_value, profile)
    uuid(market_calendar_ref)
    require(
        isinstance(sources, (tuple, list))
        and 1 <= len(sources) <= profile.max_inputs
        and all(isinstance(s, FinancialSource) for s in sources),
        "DATASET_SOURCE",
        "Explicit bounded financial sources are required",
    )
    require(
        6 + 2 * len(sources) <= min(profile.max_components, profile.max_parts),
        "DATASET_BUDGET",
        "Known component count exceeds component/part budget",
    )
    require(
        isinstance(market_bytes, bytes) and len(market_bytes) <= profile.market_bytes,
        "DATASET_BUDGET",
        "Frozen market input exceeds its budget",
    )
    known = len(market_bytes)
    refs = {market_calendar_ref}
    for source in sources:
        digest(source.prepared_root)
        uuid(source.calendar_ref)
        require(
            isinstance(source.package_bytes, bytes)
            and isinstance(source.proof_refs, tuple)
            and len(source.proof_refs) <= 256,
            "DATASET_SOURCE",
            "Financial source bytes/proof references are invalid",
        )
        for ref in source.proof_refs:
            uuid(ref)
        require(
            list(source.proof_refs) == sorted(set(source.proof_refs)),
            "DATASET_SOURCE",
            "Proof references must be sorted and unique",
        )
        refs.update((source.calendar_ref, *source.proof_refs))
        known += len(source.package_bytes)
    require(
        known - len(market_bytes) <= profile.package_bytes
        and known + profile.manifest_bytes < profile.total_bytes,
        "DATASET_BUDGET",
        "Known source bytes exceed the closure budget",
    )
    registry_raw = registry_payload(authorized_registry, refs, profile)
    require(
        known + len(registry_raw) + profile.manifest_bytes < profile.total_bytes,
        "DATASET_BUDGET",
        "Known registry/source bytes exceed the closure budget",
    )
    market = decode(market_bytes, profile.market_bytes)
    keys(market, {"schemaVersion", "rows", "provenance"})
    require(
        type(market["schemaVersion"]) is int and market["schemaVersion"] == 1,
        "DATASET_MARKET",
        "Unsupported market dataset version",
    )
    _private_content(market)
    meta, rows = market["provenance"], market["rows"]
    require(
        isinstance(meta, dict)
        and isinstance(rows, list)
        and 1 <= len(rows) <= profile.max_rows,
        "DATASET_MARKET",
        "Market rows/provenance missing or oversized",
    )
    require(
        all(isinstance(r, dict) for r in rows),
        "DATASET_MARKET",
        "Market rows must be objects",
    )
    input_columns = set(rows[0])
    require(
        all(set(row) == input_columns for row in rows)
        and not any(k.startswith("model_fin_") for k in input_columns),
        "DATASET_COLUMNS",
        "Rows need exact common columns, with no prefilled financial states",
    )
    # Existing panel validator defines supported numerical columns. Reject its
    # projected-away columns explicitly rather than losing them silently.
    frame = _validate_panel(
        {"universe": scope_value}, rows, external_fields=meta.get("externalFields")
    )
    require(
        input_columns == set(frame.columns),
        "DATASET_COLUMNS",
        "Unregistered market columns would be discarded",
    )
    calendar = _registry(authorized_registry[market_calendar_ref], "calendar")
    require(
        calendar["scope"] == calendar_scope(calendar["payload"]),
        "DATASET_CALENDAR",
        "Market calendar registry scope mismatch",
    )
    dates = [
        d
        for d in calendar["payload"]["sessions"]
        if scope_value["start"] <= d <= scope_value["end"]
    ]
    require(
        calendar["payload"]["complete"] is True
        and calendar["payload"]["coverage_start"] <= scope_value["start"]
        and calendar["payload"]["coverage_end"] >= scope_value["end"]
        and dates
        and meta.get("tradingDates") == dates,
        "DATASET_CALENDAR",
        "Market sessions must match the externally authorized calendar exactly",
    )
    for key in ("symbols", "start", "end"):
        if key in meta:
            value = meta[key]
            if key == "symbols":
                # Acquisition order is retained in the frozen source bytes.
                # Universe membership is unordered, but duplicates are invalid.
                require(
                    isinstance(value, list)
                    and all(isinstance(symbol, str) for symbol in value)
                    and len(value) == len(set(value)),
                    "DATASET_SCOPE",
                    "Market provenance symbols must be unique strings",
                )
                value = sorted(value)
            require(
                value == scope_value[key],
                "DATASET_SCOPE",
                "Market provenance scope differs",
            )
    trusted = []
    for source in sources:
        value = resolve_package_registry(
            source.package_bytes,
            authorized_registry[source.calendar_ref],
            [authorized_registry[ref] for ref in source.proof_refs],
        )
        # Financial source bytes have their own canonical contract. No decimal
        # normalization or bundle canonicalizer may rewrite them.
        require(
            encode(value.package) == source.package_bytes,
            "DATASET_CANONICAL",
            "Financial package must retain its canonical bytes",
        )
        trusted.append(value)
    ordering = sorted(range(len(sources)), key=lambda i: trusted[i].package["packRoot"])
    sources = [sources[i] for i in ordering]
    trusted = [trusted[i] for i in ordering]
    # A package's authentic registry scope is not interchangeable with another
    # package's source proof, and no serialized provenance grants admission.
    require(len({t.package["packRoot"] for t in trusted})==len(trusted),
            "DUPLICATE_FINANCIAL_INPUT","Duplicate frozen financial package")
    require(not set(meta)&{"financialInputs","financialDatasetRoot","financialCompositionVersion","preparedRoot"},
            "FINANCIAL_FIELD_COLLISION","Reserved provenance must be recomputed")
    external_original=meta.get("externalFields",{})
    require(isinstance(external_original,dict) and not any(str(k).startswith("model_fin_") for k in external_original),
            "FINANCIAL_FIELD_COLLISION","Reserved financial fields cannot come from market metadata")
    claimed=set();all_states=set()
    for t in trusted:
        package=t.package;selected=package["selection"];u=selected["universe"]
        selected_dates=[d for d in package["raw"]["calendar"]["sessions"] if scope_value["start"]<=d<=scope_value["end"]]
        require(u["start"]==scope_value["start"] and u["end"]==scope_value["end"] and
                set(u["symbols"]).issubset(scope_value["symbols"]) and selected_dates==dates,
                "FINANCIAL_CALENDAR_MISMATCH","Source scope and calendar must exactly match the research interval")
        all_states.update(selected["selectedStates"])
        require(len(all_states)<=16,"DATASET_BUDGET","At most 16 financial states are admitted to this source format")
        for symbol in u["symbols"]:
            for state in selected["selectedStates"]:
                require((symbol,state) not in claimed,"FINANCIAL_FIELD_COLLISION","Financial input scopes overlap")
                claimed.add((symbol,state))
    output=frame.copy();field_sources={};summaries=[];graphs=[]
    retained=known+len(registry_raw)+retained_origin_bytes
    require(retained+profile.manifest_bytes<=profile.total_bytes,"DATASET_BUDGET","Known closure exceeds unchanged physical budget")
    for source,t in zip(sources,trusted):
        package=t.package
        # Each independent legal expanded input uses original guards. Expanded
        # payloads are discarded before the next input, not all retained at once.
        prepared=prepare_package(package,trusted_unit_proofs=bool(t.reviewed_proofs),budget=AdapterBudget())
        require(prepared.provenance["preparedRoot"]==source.prepared_root,
                "DATASET_PREPARATION","Fresh raw preparation differs from the pinned source root")
        payload={"panel":_safe_rows(prepared.panel),"provenance":prepared.provenance,
                 "stateEvents":prepared.state_events,"assignments":prepared.assignments,"coverage":prepared.coverage}
        logical=stream_digest(canonical_chunks(payload),PREPARED_LIMIT)
        graph=encode_graph(payload,package["selection"])
        require(graph["logicalPrepared"]["sha256"]==logical["sha256"],"DATASET_PREPARATION","Expanded preparation identity differs")
        graph_raw=encode(graph);retained+=len(graph_raw)
        require(retained+profile.manifest_bytes<=profile.total_bytes,"DATASET_BUDGET","Compact retained closure exceeds original limit")
        summary=summarize_prepared(package,prepared)
        # Use the reconstructed graph panel as the actual join input, proving the
        # compact representation supplies the same values and availability.
        right=pd.DataFrame(iter_panel_rows(graph)).set_index(["ts_code","trade_date"])
        index=pd.MultiIndex.from_frame(output[["ts_code","trade_date"]])
        symbols=set(package["selection"]["universe"]["symbols"]);mask=output.ts_code.isin(symbols)
        for state in package["selection"]["selectedStates"]:
            for column in (state,state+"__available_date"):
                if column not in output:output[column]=None
                output.loc[mask,column]=right[column].reindex(index[mask]).to_numpy()
            field_sources.setdefault(state,[]).append({"symbols":sorted(symbols),"packRoot":package["packRoot"],
                "preparedRoot":prepared.provenance["preparedRoot"],"calendarRoot":summary["calendarRoot"],
                "unitPolicy":package["unitPolicy"],"evidence":deepcopy(prepared.provenance["externalFields"][state])})
        summaries.append(summary);graphs.append(graph_raw)
        del payload,prepared,graph,right,graph_raw
    external=deepcopy(external_original)
    for state,items in field_sources.items():
        external[state]={"source":"FROZEN_NATIVE_STATEMENT_PREPARATION","path":"financial_state/"+state,
            "dataType":"number","unit":"ratio","availabilityPolicy":"point_in_time_asof",
            "availableDateColumn":state+"__available_date","semanticKind":"native_statement_state",
            "formulaId":state,"formulaVersion":items[0]["evidence"]["formulaVersion"],"preparedInputs":items,
            "authentication":"proof_and_calendar_authenticity_is_trusted_caller_responsibility",
            "qualityFlags":sorted({f for item in items for f in item["evidence"]["qualityFlags"]})}
    records=_bounded_rows(output,profile.joined_bytes)
    output=_validate_panel({"universe":scope_value},records,external_fields=external)
    records=_safe_rows(output)
    inputs=[{k:s[k] for k in ("packRoot","preparedRoot","calendarRoot","unitPolicy","selectedStateIds")} for s in summaries]
    provenance=deepcopy(meta)
    provenance.update(source="COMPOSED_FINANCIAL_DATASET",marketRoot=sha(market_bytes),marketSource=meta.get("source"),
        financialCompositionVersion="financial_dataset_v1",financialInputs=inputs,externalFields=external,rows=len(output),
        dataFingerprint=sha(encode(_records(output))),synthetic=bool(meta.get("synthetic") or
            str(meta.get("source","")).upper().startswith("SYNTHETIC") or
            str(meta.get("classification","")).upper().startswith("SYNTHETIC") or
            any(t.package["raw"]["sourceKind"]=="fixture" for t in trusted)),
        financialSourceScope="selected_frozen_report_set_not_complete_filing_history")
    provenance["financialDatasetRoot"]=stream_digest(canonical_chunks({"marketRoot":provenance["marketRoot"],
        "financialInputs":inputs,"rows":records,"externalFields":external,"compositionVersion":"financial_dataset_v1"}),
        profile.joined_bytes)["sha256"]
    joined=stream_digest(canonical_chunks({"schemaVersion":1,"rows":records,"provenance":provenance}),profile.joined_bytes)
    kinds={k:"dictionary" if k in ("ts_code","trade_date") or k.endswith("__available_date") else "number" for k in output.columns}
    table=encode_table(records,kinds)
    column_raw=encode({"schemaVersion":1,"numericInput":table,"provenance":provenance,"logicalJoined":joined})
    require(len(column_raw)<=profile.joined_bytes and retained+len(column_raw)+profile.manifest_bytes<=profile.total_bytes,
            "DATASET_BUDGET","Numeric columns or compact closure exceed unchanged limits")
    return PreparedClosure(tuple(sources),registry_raw,tuple(graphs),
        GraphSourceResult(output,provenance,tuple(summaries),joined),column_raw)
