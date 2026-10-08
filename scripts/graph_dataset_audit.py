"""Independent stdlib dataset/3 graph archive closure audit.

No engine imports, financial formula recomputation, provider, PDF authentication,
model fitting or admission. Legacy audit code and canonical byte semantics stay
unchanged. Only its independently implemented source/USTAR helpers are reused.
"""
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re

import dataset_audit as legacy
from dataset_audit import (AuditError, Checks, encode, decode, sha, root, without,
    identity, date, integer, symbols, regular, read_file, Archive,
    checked_part, checked_payload, MIB, TOTAL, MANIFEST, PART, MAX_PARTS, TAR_MAX)

LOGICAL = 24 * MIB
ROW = 65536
STATES = {'model_fin_'+s for s in (
    'revenue_quarter_yoy revenue_ttm_yoy operating_margin parent_net_margin '
    'cash_revenue_ratio profit_cash_asset_gap cash_assets_ratio capex_revenue_ratio '
    'cash_less_capex_assets assets_yoy cash_asset_share current_coverage '
    'liability_asset_share borrowings_asset_share receivable_asset_share goodwill_asset_share'
).split()}
TYPES = {
    "snapshot_scope_origin": {"sourceBundleId", "sourceSnapshotSha256", "marketRoot"},
    "registry_evidence": set(), "market_dataset": {"marketRoot"},
    "financial_input": {"inputRoot", "packRoot"},
    "financial_prepared_graph": {"packRoot", "preparedRoot", "calendarRoot", "preparedPayloadSha256"},
    "research_columns": {"financialDatasetRoot", "logicalJoinedSha256"},
    "dataset_schema": set(), "dataset_coverage": set(),
}

def validate_manifest(raw, check, expected_root=None):
    m = decode(raw, MANIFEST, check)
    check.keys(
        m,
        {
            "format",
            "version",
            "profile",
            "scope",
            "marketCalendarRef",
            "financialSources",
            "components",
            "roots",
        },
    )
    check.require(
        m["format"] == "atlas.quant.research_dataset"
        and type(m["version"]) is int
        and m["version"] == 3
        and m["profile"] == "financial_snapshot_graph_50_v1",
        "Unknown format/profile",
        "FORMAT",
    )
    if expected_root is not None:
        check.require(
            sha(raw) == identity(expected_root, check),
            "Pinned dataset root differs",
            "ROOT",
        )
    check.keys(m["scope"], {"symbols", "start", "end"})
    symbols(m["scope"]["symbols"], check, True)
    check.require(
        date(m["scope"]["start"], check) <= date(m["scope"]["end"], check),
        "Reversed scope",
        "SCOPE",
    )
    identity(m["marketCalendarRef"], check, True)
    check.keys(m["roots"], {"marketRoot", "financialDatasetRoot"})
    for value in m["roots"].values():
        identity(value, check)
    components = m["components"]
    check.require(isinstance(components, list), "Components must be a list", "SHAPE")
    integer(len(components), 1, 32, check)
    by_id, by_root, depths = {}, {}, {}
    total, part_count, input_bytes = len(raw), 0, 0
    for c in components:
        check.keys(
            c,
            {
                "componentId",
                "type",
                "version",
                "componentRoot",
                "semanticRoots",
                "encoding",
                "payloadSha256",
                "byteLength",
                "dependencies",
                "parts",
            },
        )
        name = c["componentId"]
        check.require(
            isinstance(name, str)
            and re.fullmatch(r"[a-z][A-Za-z0-9]{0,39}", name)
            and name not in by_id,
            "Invalid/duplicate component ID",
            "IDENTITY",
        )
        check.require(
            isinstance(c["type"], str)
            and c["type"] in TYPES
            and type(c["version"]) is int
            and c["version"] == 1
            and c["encoding"] == "raw_bytes",
            "Unknown type/encoding",
            "TYPE",
        )
        check.keys(c["semanticRoots"], TYPES[c["type"]])
        for value in c["semanticRoots"].values():
            identity(value, check)
        identity(c["payloadSha256"], check)
        cr = identity(c["componentRoot"], check)
        check.require(
            cr == root(without(c, "componentRoot")) and cr not in by_root,
            "Descriptor identity differs or repeats",
            "ROOT",
        )
        deps = c["dependencies"]
        check.require(
            isinstance(deps, list)
            and all(isinstance(d, str) and d in by_root for d in deps)
            and deps == sorted(set(deps)),
            "Dependencies must refer to unique prior components",
            "GRAPH",
        )
        depths[cr] = 0 if not deps else 1 + max(depths[d] for d in deps)
        check.require(depths[cr] <= 3, "Graph depth exceeds three edges", "GRAPH")
        integer(c["byteLength"], 1, TOTAL, check)
        check.require(
            isinstance(c["parts"], list) and c["parts"], "Missing parts", "PARTS"
        )
        size = 0
        for ordinal, p in enumerate(c["parts"]):
            check.keys(p, {"ordinal", "byteLength", "sha256"})
            integer(p["ordinal"], ordinal, ordinal, check)
            integer(p["byteLength"], 1, PART, check)
            identity(p["sha256"], check)
            size += p["byteLength"]
        check.require(
            size == c["byteLength"], "Part lengths do not cover component", "PARTS"
        )
        ceiling = (
            24 * MIB
            if c["type"] in {"market_dataset", "financial_input", "research_columns"}
            else TOTAL
        )
        check.require(size <= ceiling, "Typed component budget exceeded", "BUDGET")
        total += size
        part_count += len(c["parts"])
        input_bytes += size if c["type"] == "financial_input" else 0
        check.require(
            total <= TOTAL and part_count <= MAX_PARTS and input_bytes <= 24 * MIB,
            "Complete closure budget exceeded",
            "BUDGET",
        )
        by_id[name], by_root[cr] = c, c
    sources = m["financialSources"]
    check.require(isinstance(sources, list), "Sources must be a list", "SHAPE")
    integer(len(sources), 1, 8, check)
    expected = {
        "registryEvidence": "registry_evidence",
        "marketDataset": "market_dataset",
        "researchColumns": "research_columns",
        "schema": "dataset_schema",
        "coverage": "dataset_coverage",
    }
    if m["version"] == 3:
        expected["marketOrigin"] = "snapshot_scope_origin"
    for i, source in enumerate(sources):
        check.keys(source, {"componentId", "calendarRef", "proofRefs", "preparedRoot"})
        check.require(
            source["componentId"] == f"financialInput{i}",
            "Source/component ordering differs",
            "GRAPH",
        )
        identity(source["calendarRef"], check, True)
        identity(source["preparedRoot"], check)
        refs = source["proofRefs"]
        check.require(
            isinstance(refs, list) and len(refs) <= 256,
            "Proof reference budget",
            "BUDGET",
        )
        for ref in refs:
            identity(ref, check, True)
        check.require(
            refs == sorted(set(refs)), "Proof references must be sorted unique", "GRAPH"
        )
        expected[f"financialInput{i}"], expected[f"financialGraph{i}"] = (
            "financial_input",
            "financial_prepared_graph",
        )
    check.require(
        set(by_id) == set(expected),
        "Typed closure omitted or added a component",
        "GRAPH",
    )
    for name, kind in expected.items():
        check.require(
            by_id[name]["type"] == kind, "Component type/identity mismatch", "TYPE"
        )
    reg = by_id["registryEvidence"]["componentRoot"]
    global_deps = sorted(
        [by_id["marketDataset"]["componentRoot"]]
        + [by_id[f"financialGraph{i}"]["componentRoot"] for i in range(len(sources))]
    )
    for name, c in by_id.items():
        if name in {"registryEvidence", "marketOrigin"}:
            deps = []
        elif name == "marketDataset" and m["version"] == 3:
            deps = sorted([reg, by_id["marketOrigin"]["componentRoot"]])
        elif name in {"researchColumns", "schema", "coverage"}:
            deps = global_deps
        elif name.startswith("financialGraph"):
            deps = [by_id[name.replace("Graph", "Input")]["componentRoot"]]
        else:
            deps = [reg]
        check.equal(c["dependencies"], deps, "Component dependency closure differs")
    check.equal(
        by_id["marketDataset"]["semanticRoots"],
        {"marketRoot": m["roots"]["marketRoot"]},
        "Market root link differs",
    )
    check.equal(
        by_id["researchColumns"]["semanticRoots"],
        {"financialDatasetRoot": m["roots"]["financialDatasetRoot"], "logicalJoinedSha256": by_id["researchColumns"]["semanticRoots"]["logicalJoinedSha256"]},
        "Joined root link differs",
    )
    for i, source in enumerate(sources):
        inp, prep = (
            by_id[f"financialInput{i}"]["semanticRoots"],
            by_id[f"financialGraph{i}"]["semanticRoots"],
        )
        check.require(
            inp["packRoot"] == prep["packRoot"]
            and prep["preparedRoot"] == source["preparedRoot"],
            "Financial root links differ",
            "ROOT",
        )
    check.require((datetime.strptime(m["scope"]["end"], "%Y%m%d") - datetime.strptime(m["scope"]["start"], "%Y%m%d")).days < 366,
                  "Scope exceeds 366 inclusive days", "SCOPE")
    check.require(by_id["marketOrigin"]["semanticRoots"]["marketRoot"] == m["roots"]["marketRoot"],
                  "Origin market root differs", "ROOT")
    return m


def load_closure(path, check, expected_root):
    """At most 64MiB retained component bytes; each part read is <=512KiB."""
    path = Path(path)
    if path.is_symlink():
        raise AuditError("FILE", "Symlink input is not allowed")
    payloads = {}
    if path.is_dir():
        check.require(
            set(p.name for p in path.iterdir()) == {"manifest.json", "parts"},
            "Unexpected directory members",
            "DIRECTORY",
        )
        raw = read_file(path / "manifest.json", MANIFEST)
        m = validate_manifest(raw, check, expected_root)
        parts_dir = path / "parts"
        check.require(
            not parts_dir.is_symlink() and parts_dir.is_dir(),
            "Invalid parts directory",
            "DIRECTORY",
        )
        check.require(
            set(p.name for p in parts_dir.iterdir())
            == {c["componentId"] for c in m["components"]},
            "Missing or extra component directory",
            "DIRECTORY",
        )
        for c in m["components"]:
            folder = parts_dir / c["componentId"]
            check.require(
                not folder.is_symlink() and folder.is_dir(),
                "Invalid component directory",
                "DIRECTORY",
            )
            check.require(
                set(p.name for p in folder.iterdir())
                == {f"{p['ordinal']}.bin" for p in c["parts"]},
                "Missing or extra part",
                "DIRECTORY",
            )
            pieces = [
                checked_part(read_file(folder / f"{p['ordinal']}.bin", PART), p, check)
                for p in c["parts"]
            ]
            payloads[c["componentId"]] = checked_payload(b"".join(pieces), c, check)
    else:
        with regular(path, TAR_MAX) as stream:
            archive = Archive(stream, check)
            raw = archive.member("manifest.json", MANIFEST)
            m = validate_manifest(raw, check, expected_root)
            for c in m["components"]:
                pieces = [
                    checked_part(
                        archive.member(
                            f"parts/{c['componentId']}/{p['ordinal']}.bin",
                            PART,
                            p["byteLength"],
                        ),
                        p,
                        check,
                    )
                    for p in c["parts"]
                ]
                payloads[c["componentId"]] = checked_payload(b"".join(pieces), c, check)
            archive.finish()
    return raw, m, payloads



def count_list(value, maximum, check, minimum=0):
    check.require(type(value) is list and minimum <= len(value) <= maximum,
                  'Invalid bounded list', 'SHAPE')
    return value


def digest_stream(chunks, limit, check):
    h, size = hashlib.sha256(), 0
    for chunk in chunks:
        check.require(type(chunk) is bytes, 'Canonical stream needs bytes', 'JSON')
        size += len(chunk)
        check.require(size <= limit, 'Expanded canonical byte budget exceeded', 'BUDGET')
        h.update(chunk)
    return {'sha256': h.hexdigest(), 'byteLength': size}


def array_stream(values, check, item_limit=ROW):
    yield b'['
    for index, value in enumerate(values):
        raw = encode(value)
        check.require(len(raw) <= item_limit, 'Expanded item byte budget exceeded', 'BUDGET')
        if index:
            yield b','
        yield raw
    yield b']'


def object_stream(fields):
    """Fields are factories; no expanded document or event list is retained."""
    yield b'{'
    for index, key in enumerate(sorted(fields)):
        if index:
            yield b','
        yield encode(key)
        yield b':'
        yield from fields[key]()
    yield b'}'


def literal(value):
    return lambda: iter((encode(value),))


def verify_table(table, check=None):
    check = check or Checks()
    check.keys(table, {'format', 'version', 'rowCount', 'columns', 'logicalRows'})
    check.require(table['format'] == 'atlas.quant.exact_column_table'
                  and type(table['version']) is int and table['version'] == 1,
                  'Unknown column format', 'FORMAT')
    integer(table['rowCount'], 1, 110000, check)
    check.keys(table['logicalRows'], {'sha256', 'byteLength'})
    identity(table['logicalRows']['sha256'], check)
    integer(table['logicalRows']['byteLength'], 2, LOGICAL, check)
    names = set()
    for column in count_list(table['columns'], 128, check, 1):
        check.require(type(column) is dict and type(column.get('name')) is str
                      and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,95}', column['name'])
                      and column['name'] not in names, 'Invalid/repeated column name', 'COLUMN')
        names.add(column['name'])
        if column.get('kind') == 'number':
            check.keys(column, {'name', 'kind', 'values'})
            values = count_list(column['values'], 110000, check)
            for value in values:
                try:
                    finite = value is None or type(value) in (int, float) and math.isfinite(value)
                except OverflowError:
                    finite = False
                check.require(finite, 'Numeric cell is not a finite number/null', 'COLUMN')
        else:
            check.keys(column, {'name', 'kind', 'dictionary', 'indices'})
            check.require(column['kind'] == 'dictionary', 'Unknown column kind', 'COLUMN')
            dictionary = count_list(column['dictionary'], 110000, check, 1)
            tokens = set()
            for value in dictionary:
                check.require(value is None or type(value) is str and len(value.encode('utf-8')) <= 256,
                              'Invalid string/null cell', 'COLUMN')
                token = encode(value)
                check.require(token not in tokens, 'Duplicate dictionary value', 'COLUMN')
                tokens.add(token)
            values = count_list(column['indices'], 110000, check)
            seen = set()
            for index in values:
                integer(index, 0, len(dictionary)-1, check)
                if index not in seen:
                    check.require(index == len(seen), 'Dictionary is not first-occurrence ordered', 'COLUMN')
                    seen.add(index)
            check.require(len(seen) == len(dictionary), 'Unused dictionary value', 'COLUMN')
        check.require(len(values) == table['rowCount'], 'Column lengths differ', 'COLUMN')
    check.require(len(encode(table)) <= LOGICAL, 'Physical table byte budget exceeded', 'BUDGET')
    result = digest_stream(array_stream(table_rows(table), check), LOGICAL, check)
    check.equal(result, table['logicalRows'], 'Exact logical row bytes differ')
    return result


def table_rows(table):
    for index in range(table['rowCount']):
        yield table_row(table, index)


def table_row(table, index):
    return {c['name']: (c['values'][index] if c['kind'] == 'number'
                      else c['dictionary'][c['indices'][index]]) for c in table['columns']}


RESULT_KEYS = set('status decimalValue decimalPrecision rounding valueRepresentation unit periodEnd availableDate reasonCodes formulaVersion policyVersion mappingVersions asOf scope basis revisionHistory qualityFlags unitEvidenceLevels declarationHashes unitVerified lineageHash'.split())
DEPENDENCY_KEYS = set('field_id record_hash source_snapshot source_kind source_provider retrieved_at period_end announcement_date available_date report_type company_type scope basis raw_decimal raw_unit currency unit_evidence unit_evidence_kind mapping_version unit_scope unit_verified evidence_level quality_flags declaration_hashes'.split())
UNIT_KEYS = set('kind field_id provider status binding_hashes symbol period_end ann_date f_ann_date report_type company_type source_snapshot normalized_row_hash document_hashes policy_version input_root declaration_hashes'.split())
CALENDAR_KEYS = set('root sessions_hash coverage_start coverage_end complete kind evidence_reference'.split())


def graph_number(value, check):
    reasons = count_list(value['reasonCodes'], 32, check)
    check.require(all(type(s) is str and 1 <= len(s) <= 96 for s in reasons), 'Invalid reason strings', 'RESULT')
    if value['status'] == 'missing':
        check.require(value['decimalValue'] is None and bool(reasons), 'Invalid missing result', 'RESULT')
        return None
    check.require(value['status'] == 'ok' and not reasons and type(value['decimalValue']) is str
                  and len(value['decimalValue']) <= 256, 'Invalid available result', 'RESULT')
    try:
        dec = Decimal(value['decimalValue'])
        number = float(dec)
        valid = dec.is_finite() and math.isfinite(number) and not (number == 0 and dec != 0)
    except (InvalidOperation, ValueError, OverflowError):
        valid = False
    check.require(valid, 'Decimal conversion is nonfinite or underflows', 'NUMBER')
    return number


class PreparedGraph:
    def __init__(self, graph, check):
        self.graph, self.check = graph, check
        check.keys(graph, {'format', 'version', 'logicalPrepared', 'dependencies', 'calendars',
                           'events', 'assignments', 'panel', 'selection', 'provenance', 'coverage'})
        check.require(graph['format'] == 'atlas.quant.financial_prepared_graph'
                      and type(graph['version']) is int and graph['version'] == 1, 'Unknown prepared graph', 'FORMAT')
        meta = graph['logicalPrepared']
        check.keys(meta, {'sha256', 'byteLength', 'preparedRoot'})
        identity(meta['sha256'], check); identity(meta['preparedRoot'], check)
        integer(meta['byteLength'], 1, TOTAL, check)
        self.dependencies = self.dictionary('dependencies', 20000, DEPENDENCY_KEYS, ROW)
        self.calendars = self.dictionary('calendars', 8, CALENDAR_KEYS, 8192)
        for dep in self.dependencies.values():
            if dep['unit_scope'] is not None:
                check.keys(dep['unit_scope'], UNIT_KEYS)
        for cal in self.calendars.values():
            identity(cal['root'], check); identity(cal['sessions_hash'], check)
            date(cal['coverage_start'], check); date(cal['coverage_end'], check)
            check.require(type(cal['complete']) is bool, 'Calendar complete is not boolean', 'CALENDAR')
        selection, panel = graph['selection'], graph['panel']
        check.keys(selection, {'universe', 'selectedStates', 'scope', 'flowBasis', 'announcementStart'})
        check.keys(selection['universe'], {'symbols', 'start', 'end'})
        start, end = (date(selection['universe'][k], check) for k in ('start', 'end'))
        date(selection['announcementStart'], check)
        check.require(0 <= (datetime.strptime(end, '%Y%m%d')-datetime.strptime(start, '%Y%m%d')).days < 366
                      and selection['scope'] == 'consolidated' and selection['flowBasis'] == 'ytd', 'Invalid selection', 'SCOPE')
        check.keys(panel, {'symbols', 'sessions', 'stateIds', 'rowCount', 'columnNames'})
        symbols(panel['symbols'], check, True)
        check.equal(panel['symbols'], selection['universe']['symbols'], 'Panel security selection differs')
        sessions = count_list(panel['sessions'], 366, check, 1)
        for day in sessions:
            date(day, check)
        check.require(sessions == sorted(set(sessions)) and start <= sessions[0] <= sessions[-1] <= end,
                      'Invalid panel sessions', 'CALENDAR')
        states = count_list(panel['stateIds'], 16, check, 1)
        check.require(all(type(s) is str and s in STATES for s in states)
                      and len(set(states)) == len(states), 'Invalid state identities', 'STATE')
        check.equal(states, selection['selectedStates'], 'Panel state selection differs')
        check.equal(panel['columnNames'], ['ts_code', 'trade_date']+[c for s in states for c in (s, s+'__available_date')], 'Unexpected panel columns')
        integer(panel['rowCount'], 1, 110000, check)
        check.require(panel['rowCount'] == len(panel['symbols'])*len(sessions), 'Panel count differs', 'COVERAGE')
        self.events, self.numbers = {}, {}
        used_dependencies, used_calendars = set(), set()
        for event in count_list(graph['events'], 10000, check, 1):
            check.keys(event, {'id', 'symbol', 'stateId', 'computedAsOf', 'result', 'dependencyRefs', 'calendarRef'})
            eid = identity(event['id'], check)
            check.require(eid not in self.events and event['symbol'] in panel['symbols']
                          and event['stateId'] in states and event['computedAsOf'] in sessions, 'Invalid event identity/scope', 'EVENT')
            value = event['result']; check.keys(value, RESULT_KEYS)
            check.require(value['asOf'] == event['computedAsOf'] and type(value['decimalPrecision']) is int
                          and value['decimalPrecision'] == 34 and value['rounding'] == 'ROUND_HALF_EVEN'
                          and type(value['unitVerified']) is bool, 'Invalid event precision/cutoff', 'EVENT')
            refs = count_list(event['dependencyRefs'], 128, check)
            check.require(all(type(ref) is str and ref in self.dependencies for ref in refs)
                          and len(set(refs)) == len(refs), 'Dangling/duplicate dependency', 'REFERENCE')
            cref = event['calendarRef']
            check.require(cref is None or type(cref) is str and cref in self.calendars, 'Dangling calendar', 'REFERENCE')
            used_dependencies.update(refs)
            if cref is not None:
                used_calendars.add(cref)
            if value['availableDate'] is not None:
                check.require(date(value['availableDate'], check) <= event['computedAsOf'], 'Future availability', 'CAUSALITY')
            self.numbers[eid] = graph_number(value, check)
            check.require(value['status'] != 'ok' or value['availableDate'] is not None, 'Available state missing date', 'CAUSALITY')
            expanded = self.expand(event)
            check.require(root(without(expanded['result'], 'lineageHash')) == identity(value['lineageHash'], check), 'Expanded lineage differs', 'LINEAGE')
            check.require(root(without(expanded, 'id')) == eid, 'Expanded event differs', 'EVENT')
            self.events[eid] = event
        self.event_stream = digest_stream(array_stream((self.expand(e) for e in graph['events']), check, PART), 32*MIB, check)
        check.require(used_dependencies == set(self.dependencies) and used_calendars == set(self.calendars), 'Unused dictionary entry', 'REFERENCE')
        self.assignments = {s: [] for s in panel['symbols']}
        positions, used_events, previous = {d:i for i,d in enumerate(sessions)}, set(), None
        coverage = {s: {'okRows':0, 'missingRows':0, 'reasons':Counter()} for s in states}
        for assignment in count_list(graph['assignments'], 10000, check, 1):
            check.keys(assignment, {'symbol', 'from', 'through', 'states'})
            s = assignment['symbol']
            check.require(s in self.assignments and assignment['from'] in positions and assignment['through'] in positions
                          and assignment['from'] <= assignment['through'], 'Invalid assignment scope', 'ASSIGNMENT')
            order = (s, assignment['from'])
            check.require(previous is None or previous < order, 'Assignment order/duplicate differs', 'ASSIGNMENT')
            previous = order; check.keys(assignment['states'], states)
            n = positions[assignment['through']] - positions[assignment['from']] + 1
            for state, ref in assignment['states'].items():
                check.require(type(ref) is str and ref in self.events, 'Dangling assignment', 'ASSIGNMENT')
                event = self.events[ref]
                check.require(event['symbol'] == s and event['stateId'] == state and event['computedAsOf'] <= assignment['from'], 'Wrong/future assignment event', 'ASSIGNMENT')
                used_events.add(ref)
                value = event['result']
                coverage[state]['okRows' if value['status'] == 'ok' else 'missingRows'] += n
                if value['status'] == 'missing':
                    for reason in value['reasonCodes']:
                        coverage[state]['reasons'][reason] += n
            self.assignments[s].append(assignment)
        for items in self.assignments.values():
            cursor = 0
            for assignment in items:
                check.require(positions[assignment['from']] == cursor, 'Assignment gap/overlap', 'ASSIGNMENT')
                cursor = positions[assignment['through']] + 1
            check.require(cursor == len(sessions), 'Assignment tail missing', 'ASSIGNMENT')
        check.require(used_events == set(self.events), 'Unused event', 'ASSIGNMENT')
        check.equal(coverage, graph['coverage'], 'Coverage differs from assignments')
        provenance = graph['provenance']
        check.require(type(provenance) is dict and provenance.get('preparedRoot') == meta['preparedRoot']
                      and provenance.get('unitPolicy') in ('allow_declared', 'verified_only')
                      and type(provenance.get('panelRows')) is int and provenance['panelRows'] == panel['rowCount']
                      and type(provenance.get('stateEvents')) is int and provenance['stateEvents'] == len(self.events), 'Prepared metadata differs', 'ROOT')
        identity(provenance.get('inputRoot'), check)

    def dictionary(self, name, maximum, keys, ceiling):
        result, previous = {}, ''
        for item in count_list(self.graph[name], maximum, self.check):
            self.check.keys(item, {'id', 'value'}); self.check.keys(item['value'], keys)
            ref = identity(item['id'], self.check); raw = encode(item['value'])
            self.check.require(ref > previous and len(raw) <= ceiling and sha(raw) == ref, 'Invalid dictionary hash/order/size', 'DICTIONARY')
            result[ref], previous = item['value'], ref
        return result

    def expand(self, event):
        return {k:event[k] for k in ('id', 'symbol', 'stateId', 'computedAsOf')} | {'result': {
            **event['result'], 'dependencies':[self.dependencies[d] for d in event['dependencyRefs']],
            'calendar': self.calendars[event['calendarRef']] if event['calendarRef'] is not None else None}}

    def rows(self):
        for symbol, items in self.assignments.items():
            cursor = 0
            for day in self.graph['panel']['sessions']:
                while day > items[cursor]['through']:
                    cursor += 1
                row = {'ts_code':symbol, 'trade_date':day}
                for state, ref in items[cursor]['states'].items():
                    value = self.events[ref]['result']
                    row[state] = self.numbers[ref]
                    row[state+'__available_date'] = value['availableDate'] if value['status'] == 'ok' else None
                yield row

    def stream(self, prepared_root=False):
        g = self.graph
        fields = {'panel':lambda:array_stream(self.rows(), self.check),
                  'stateEvents':lambda:array_stream((self.expand(e) for e in g['events']), self.check, PART),
                  'assignments':literal(g['assignments']), 'coverage':literal(g['coverage'])}
        if prepared_root:
            fields.update(inputRoot=literal(g['provenance']['inputRoot']), unitPolicy=literal(g['provenance']['unitPolicy']), selection=literal(g['selection']))
        else:
            fields['provenance'] = literal(g['provenance'])
        return object_stream(fields)

    def verify(self, expected_prepared_root, expected_payload_sha256):
        identity(expected_prepared_root, self.check); identity(expected_payload_sha256, self.check)
        meta = self.graph['logicalPrepared']
        logical = digest_stream(self.stream(), TOTAL, self.check)
        prepared = digest_stream(self.stream(True), TOTAL, self.check)
        self.check.require(logical == {k:meta[k] for k in ('sha256','byteLength')}
                           and logical['sha256'] == expected_payload_sha256
                           and prepared['sha256'] == expected_prepared_root == meta['preparedRoot'], 'Original prepared payload/root differs', 'ROOT')
        return {'logicalPrepared':logical, 'preparedRoot':prepared['sha256'], 'expandedEventStream':self.event_stream}


def verify_graph(graph, *, expected_prepared_root, expected_payload_sha256, check=None):
    return PreparedGraph(graph, check or Checks()).verify(expected_prepared_root, expected_payload_sha256)


class RepeatableRows:
    def __init__(self, context):
        self.context = context

    def __iter__(self):
        return self.context.rows()


def check_graph_source(context, package, source_rows, calendar, check):
    g, p = context.graph, context.graph['provenance']
    check.equal(g['selection'], package['selection'], 'Graph changed exact package selection')
    for key in ('inputRoot', 'packRoot', 'unitPolicy'):
        check.equal(p[key], package[key], 'Prepared package reference differs')
    check.equal(p['calendar'], calendar, 'Prepared calendar differs')
    check.require(p['originalAsPublishedVerified'] is False and p['revisionTimeVerified'] is False,
                  'Prepared source-history trust cannot be upgraded', 'EVIDENCE')
    u = package['selection']['universe']
    check.equal(g['panel']['sessions'], [d for d in package['raw']['calendar']['sessions'] if u['start'] <= d <= u['end']], 'Prepared calendar coverage differs')
    grouped = {}
    for key, item in source_rows.items():
        grouped.setdefault((key[0], item[1].get('ts_code')), {})[key] = item
    checked = set()
    for event in g['events']:
        expanded = context.expand(event)
        value, dependencies = expanded['result'], expanded['result']['dependencies']
        check.equal(value['calendar'], calendar, 'Expanded event calendar differs')
        for ref, dep in zip(event['dependencyRefs'], dependencies):
            cache_key = (ref, event['symbol'])
            if cache_key not in checked:
                legacy.check_dependency(dep, expanded, package, grouped.get((dep['source_snapshot'], event['symbol']), {}), check)
                checked.add(cache_key)
            if dep['available_date'] is not None:
                check.require(date(dep['available_date'], check) <= value['asOf'], 'Future dependency', 'CAUSALITY')
                if dep['announcement_date'] is not None:
                    check.require(date(dep['announcement_date'], check) < dep['available_date'], 'Disclosure must precede availability', 'CAUSALITY')
        for key, values in (
            ('mappingVersions', {d['mapping_version'] for d in dependencies}),
            ('qualityFlags', {v for d in dependencies for v in d['quality_flags']}),
            ('declarationHashes', {v for d in dependencies for v in d['declaration_hashes']}),
            ('unitEvidenceLevels', {d['evidence_level'] for d in dependencies}),
        ):
            check.equal(value[key], sorted(values), 'Expanded dependency aggregate differs')
        check.require(value['unitVerified'] is (bool(dependencies) and all(d['unit_verified'] for d in dependencies)), 'Unit trust aggregate differs', 'LINEAGE')
    # This helper inspects only compact event results. It retains one security's
    # panel rows at a time; no expanded event list or complete prepared document.
    return legacy.prepared_summary(package, {'stateEvents':g['events'], 'panel':RepeatableRows(context),
        'assignments':g['assignments'], 'provenance':p}, check)


def verify_envelope(value, check):
    check.keys(value, {'schemaVersion', 'numericInput', 'provenance', 'logicalJoined'})
    check.require(type(value['schemaVersion']) is int and value['schemaVersion'] == 1
                  and type(value['provenance']) is dict, 'Invalid numerical envelope', 'FORMAT')
    verify_table(value['numericInput'], check)
    check.keys(value['logicalJoined'], {'sha256', 'byteLength'})
    identity(value['logicalJoined']['sha256'], check)
    integer(value['logicalJoined']['byteLength'], 1, LOGICAL, check)
    logical = digest_stream(object_stream({'schemaVersion':literal(1), 'provenance':literal(value['provenance']),
        'rows':lambda:array_stream(table_rows(value['numericInput']), check)}), LOGICAL, check)
    check.equal(logical, value['logicalJoined'], 'Complete logical joined document differs')
    return logical


def audit_semantics(manifest, payloads, check, registry_pins, source_pins):
    def value(name):
        return decode(payloads[name], TOTAL, check)
    components = {c['componentId']:c for c in manifest['components']}
    origin, market, envelope = value('marketOrigin'), value('marketDataset'), value('researchColumns')
    source_bytes = {'sourceManifest':origin['source']['manifestRawText'].encode('utf-8'),
                    'sourceSnapshot':origin['source']['snapshotRawText'].encode('utf-8'),
                    **{s['componentId']:payloads[s['componentId']] for s in manifest['financialSources']}}
    if source_pins is not None:
        check.require(type(source_pins) is dict and set(source_pins) == set(source_bytes), 'Source pins must cover exact original manifest/snapshot/package set', 'PINS')
        for name, raw in source_bytes.items():
            check.require(source_pins[name] == raw, 'Source bytes differ from independently supplied original', 'PINS')
    source_identities = {k:{'sha256':sha(v), 'byteLength':len(v)} for k,v in source_bytes.items()}
    records = legacy.registry_records(manifest, value('registryEvidence'), check, registry_pins)
    legacy.check_snapshot_origin(manifest, origin, market, check)
    check.equal(components['marketOrigin']['semanticRoots'], {'sourceBundleId':origin['source']['bundleId'],
        'sourceSnapshotSha256':origin['source']['snapshotSha256'], 'marketRoot':manifest['roots']['marketRoot']}, 'Origin descriptor roots differ')
    check.keys(market, {'schemaVersion', 'rows', 'provenance'})
    check.require(type(market['schemaVersion']) is int and market['schemaVersion'] == 1, 'Invalid market schema', 'FORMAT')
    logical = verify_envelope(envelope, check)
    table, jp, scope = envelope['numericInput'], envelope['provenance'], manifest['scope']
    check.equal(components['researchColumns']['semanticRoots'], {'financialDatasetRoot':jp['financialDatasetRoot'],
        'logicalJoinedSha256':logical['sha256']}, 'Numeric component semantic roots differ')
    check.require(root(market) == manifest['roots']['marketRoot'], 'Market root differs', 'ROOT')
    mcal = records[manifest['marketCalendarRef']]
    check.require(mcal['kind'] == 'calendar', 'Market calendar record has wrong kind', 'CALENDAR')
    cal = mcal['payload']; legacy.calendar_evidence(cal, check)
    sessions = [d for d in cal['sessions'] if scope['start'] <= d <= scope['end']]
    check.require(cal['coverage_start'] <= scope['start'] and cal['coverage_end'] >= scope['end'] and sessions, 'Market calendar coverage differs', 'CALENDAR')
    check.equal(market['provenance']['tradingDates'], sessions, 'Market sessions differ')
    market_index = legacy.row_index(market['rows'], check)
    check.require(not any(c.startswith('model_fin_') for c in market['rows'][0])
                  and not set(market['provenance']) & {'financialInputs', 'financialDatasetRoot', 'financialCompositionVersion', 'preparedRoot'}, 'Reserved financial source namespace', 'JOIN')
    joined_index, previous, normalized_count = {}, None, 0
    for index, row in enumerate(table_rows(table)):
        key = (date(row['trade_date'], check), row['ts_code'])
        check.require(key in market_index and key not in joined_index and (previous is None or previous < key), 'Joined row order/coordinates differ', 'JOIN')
        check.require(key[0] in sessions and key[1] in scope['symbols'], 'Market row outside scope', 'SCOPE')
        joined_index[key], previous = index, key
        for column, original in market_index[key].items():
            normalized = original
            if original is not None and column not in ('ts_code', 'trade_date') and not column.endswith('__available_date'):
                check.require(type(original) in (int, float) and math.isfinite(original), 'Market value must be finite numeric', 'JOIN')
                normalized = float(original)
                normalized_count += encode(normalized) != encode(original)
            check.require(column in row and encode(row[column]) == encode(normalized), 'Joined market token differs from prescribed float conversion', 'JOIN')
    check.require(set(joined_index) == set(market_index), 'Joined market rows omitted', 'JOIN')
    financial_inputs, package_roots, ownership, graph_reports, summaries = [], [], {}, [], []
    expected_external = dict(market['provenance'].get('externalFields', {}))
    synthetic = bool(market['provenance'].get('synthetic')
        or str(market['provenance'].get('source', '')).upper().startswith('SYNTHETIC')
        or str(market['provenance'].get('classification', '')).upper().startswith('SYNTHETIC'))
    for i, source in enumerate(manifest['financialSources']):
        package, graph = value(source['componentId']), value(f'financialGraph{i}')
        cal, raw_rows = legacy.check_package(package, source, records, scope, check)
        context = PreparedGraph(graph, check)
        descriptor = components[f'financialGraph{i}']['semanticRoots']
        graph_reports.append(context.verify(source['preparedRoot'], descriptor['preparedPayloadSha256']))
        check.equal(descriptor, {'packRoot':package['packRoot'], 'preparedRoot':source['preparedRoot'],
            'calendarRoot':cal['root'], 'preparedPayloadSha256':graph['logicalPrepared']['sha256']}, 'Graph semantic roots differ')
        check.equal(components[source['componentId']]['semanticRoots'], {k:package[k] for k in ('inputRoot','packRoot')}, 'Source component roots differ')
        summary = check_graph_source(context, package, raw_rows, cal, check)
        summaries.append(summary)
        selected = package['selection']['selectedStates']
        financial_inputs.append({'packRoot':package['packRoot'], 'preparedRoot':source['preparedRoot'],
            'calendarRoot':cal['root'], 'unitPolicy':package['unitPolicy'], 'selectedStateIds':selected})
        package_roots.append(package['packRoot'])
        synthetic |= package['raw']['sourceKind'] == 'fixture'
        for row in context.rows():
            key = (row['trade_date'], row['ts_code'])
            # Prepared panel includes the full calendar even if frozen market has
            # no observation at a date. A left join preserves market rows only.
            if key in joined_index:
                actual = table_row(table, joined_index[key])
                for column in graph['panel']['columnNames'][2:]:
                    check.require(column in actual and encode(actual[column]) == encode(row[column]), 'Joined financial token differs from expanded graph', 'JOIN')
        for state in selected:
            for symbol in package['selection']['universe']['symbols']:
                check.require((state,symbol) not in ownership, 'Overlapping financial state ownership', 'JOIN')
                ownership[state,symbol] = i
            evidence = graph['provenance']['externalFields'][state]
            entry = {'symbols':sorted(package['selection']['universe']['symbols']), 'packRoot':package['packRoot'],
                'preparedRoot':source['preparedRoot'], 'calendarRoot':cal['root'], 'unitPolicy':package['unitPolicy'], 'evidence':evidence}
            if state not in expected_external:
                expected_external[state] = {'source':'FROZEN_NATIVE_STATEMENT_PREPARATION', 'path':'financial_state/'+state,
                    'dataType':'number', 'unit':'ratio', 'availabilityPolicy':'point_in_time_asof',
                    'availableDateColumn':state+'__available_date', 'semanticKind':'native_statement_state',
                    'formulaId':state, 'formulaVersion':evidence['formulaVersion'], 'preparedInputs':[],
                    'authentication':'proof_and_calendar_authenticity_is_trusted_caller_responsibility', 'qualityFlags':[]}
            expected_external[state]['preparedInputs'].append(entry)
            expected_external[state]['qualityFlags'] = sorted(set(expected_external[state]['qualityFlags']) | set(evidence['qualityFlags']))
        # Drop the only decoded graph and source before expanding the next input.
        del context, graph, package, raw_rows
    check.require(package_roots == sorted(set(package_roots)), 'Package root ordering differs', 'ROOT')
    states = sorted({state for state,_ in ownership})
    check.require(len(states) <= 16, 'Joined state budget exceeded', 'BUDGET')
    expected_columns = set(market['rows'][0]) | set(states) | {s+'__available_date' for s in states}
    check.require({c['name'] for c in table['columns']} == expected_columns, 'Unexpected/missing joined columns', 'JOIN')
    for row in table_rows(table):
        for state in states:
            if (state,row['ts_code']) not in ownership:
                check.require(row[state] is None and row[state+'__available_date'] is None, 'Nonowner financial cell must be null', 'JOIN')
    check.equal(jp['externalFields'], expected_external, 'Joined external evidence differs')
    check.equal(jp['financialInputs'], financial_inputs, 'Joined financial references differ')
    financial_root = digest_stream(object_stream({'marketRoot':literal(root(market)), 'financialInputs':literal(financial_inputs),
        'rows':lambda:array_stream(table_rows(table), check), 'externalFields':literal(expected_external),
        'compositionVersion':literal('financial_dataset_v1')}), TOTAL, check)['sha256']
    check.require(jp['financialDatasetRoot'] == financial_root == manifest['roots']['financialDatasetRoot']
                  and jp['marketRoot'] == root(market) and jp['financialCompositionVersion'] == 'financial_dataset_v1', 'Financial joined root differs', 'ROOT')
    check.require(type(jp['rows']) is int and jp['rows'] == table['rowCount'] and jp['source'] == 'COMPOSED_FINANCIAL_DATASET'
                  and jp['marketSource'] == market['provenance'].get('source')
                  and jp['financialSourceScope'] == 'selected_frozen_report_set_not_complete_filing_history'
                  and jp['synthetic'] is bool(synthetic), 'Joined provenance source/count differs', 'EVIDENCE')
    check.equal(jp['tradingDates'], sessions, 'Joined sessions differ')
    schema, coverage = value('schema'), value('coverage')
    check.keys(schema, {'columns','externalFields','calendarSessions'})
    check.require(type(schema['columns']) is list and len(schema['columns']) == len(expected_columns)
                  and set(schema['columns']) == expected_columns, 'Schema columns differ', 'SCHEMA')
    check.equal(schema['columns'], [c['name'] for c in table['columns']], 'Schema/table column order differs')
    check.equal(schema['externalFields'], expected_external, 'Schema evidence differs')
    check.equal(schema['calendarSessions'], sessions, 'Schema calendar differs')
    check.equal(coverage, {'marketRows':len(market_index), 'observedSymbols':sorted({s for _,s in market_index}), 'financial':summaries}, 'Complete source coverage differs')
    return {'marketRows':len(market_index), 'joinedRows':table['rowCount'], 'states':len(states), 'financialInputs':len(financial_inputs),
        'events':sum(s['stateEventCount'] for s in summaries), 'registryRecords':len(records), 'roots':manifest['roots'],
        'financialRoots':financial_inputs, 'preparedGraphs':graph_reports, 'logicalJoined':logical,
        'totalExpandedPreparedPayloadBytes':sum(g['logicalPrepared']['byteLength'] for g in graph_reports),
        'maxExpandedPreparedComponentBytes':max(g['logicalPrepared']['byteLength'] for g in graph_reports),
        'preparedCanonicalStreamsMaterialized':False,
        'marketNumericTokensNormalized':normalized_count, 'sourceByteIdentities':source_identities,
        'sourceViewProjectionVerified':True, 'synthetic':bool(synthetic)}


def load_source_pins(path):
    """Separate caller supplied exact bytes: sourceManifest/sourceSnapshot/financialInputN."""
    path = Path(path)
    mapping = json.loads(read_file(path, MANIFEST), object_pairs_hook=legacy._pin_pairs)
    if type(mapping) is not dict or not 3 <= len(mapping) <= 10:
        raise AuditError('PINS', 'Source pins require manifest/snapshot/package local files')
    pins, total = {}, 0
    for key, filename in mapping.items():
        if not re.fullmatch(r'sourceManifest|sourceSnapshot|financialInput[0-7]', key) or type(filename) is not str or not filename or '://' in filename:
            raise AuditError('PINS', 'Invalid source pin identity or local path')
        target = Path(filename)
        raw = read_file(target if target.is_absolute() else path.parent/target, TOTAL)
        total += len(raw)
        if total > TOTAL:
            raise AuditError('PINS', 'Source pin aggregate byte budget exceeded')
        pins[key] = raw
    return pins


def audit_graph_dataset(path, *, expected_root=None, registry_pins=None, source_pins=None):
    check = Checks()
    try:
        raw, manifest, payloads = load_closure(path, check, expected_root)
        details = audit_semantics(manifest, payloads, check, registry_pins, source_pins)
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
        raise AuditError('SHAPE', 'Malformed typed graph payload or semantic reference') from exc
    return {'status':'PASS', 'auditor':'atlas.graph_dataset.stdlib_audit/1', 'datasetVersion':3,
        'datasetRoot':sha(raw), 'datasetRootPinned':expected_root is not None, 'checks':check.count,
        'profile':manifest['profile'], 'scope':manifest['scope'], 'componentCount':len(manifest['components']),
        'partCount':sum(len(c['parts']) for c in manifest['components']),
        'closureBytes':len(raw)+sum(len(v) for v in payloads.values()),
        'integrityVerified':True, 'semanticClosureVerified':True, 'exactGraphExpansionVerified':True,
        'exactNumericTokensVerified':True, 'externalRegistryBytesMatched':registry_pins is not None,
        'externalSourceBytesMatched':source_pins is not None,
        'trustStatus':'external_registry_and_source_bytes_matched' if registry_pins is not None and source_pins is not None else 'external_registry_bytes_matched' if registry_pins is not None else 'unverified',
        'sourceAuthorityVerified':False, 'financialFormulasRecomputed':False, 'pdfAuthenticityVerified':False,
        'sourceResearchFingerprintRecomputed':False, 'providerAuthenticityVerified':False,
        'modelAdmissionRegistered':False, 'modelFitted':False, 'providerCalls':0,
        'limitations':[
            'Caller must authorize dataset/source/registry pins independently; package-internal hashes alone do not establish authority.',
            'Closure and exact graph expansion are checked; financial formula arithmetic and normalized StatementRecord record_hash are not rederived.',
            'No PDF, supplier, original-as-published or complete historical revision authenticity is established.',
            'Engine research/data fingerprints use pandas rules and are not recomputed by this stdlib audit.',
            'Canonical source bytes and prescribed market numeric int-to-float normalization are separate identities.',
            'Prepared canonical streams are processed one graph at a time; encoded byte limits do not impose a peak Python RSS limit.'
        ], **details}
