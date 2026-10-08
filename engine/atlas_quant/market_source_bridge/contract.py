"""Offline, bounded source identities. Nothing here grants owner or model admission."""
from dataclasses import dataclass, fields
from datetime import datetime
import hashlib
import json
import math
import re
import struct

FORMAT = 'atlas.quant.frozen_market_source'
VIEW_FORMAT = 'atlas.quant.frozen_market_view'
VERSION = 1
BASE = 'ts_code trade_date open high low close raw_close vol amount adj_factor'.split()
OPTIONAL = set('turnover_rate turnover_rate_f volume_ratio pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm total_share float_share free_share total_mv circ_mv'.split())
ADJUSTMENT = 'OHLC multiplied by adj_factor / first observed adj_factor per symbol'
SCOPE_KEYS = set('format version membershipPolicy symbols symbolCount start end snapshotHash resolutionHash selection catalogSnapshot sourceUniverses steps algorithmVersion historicalMembershipVerified'.split())

class SourceError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)

def require(ok, code, message):
    if not ok:
        raise SourceError(code, message)

def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected), 'SOURCE_SCHEMA', 'Unexpected or missing fields')

def encode(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as exc:
        raise SourceError('SOURCE_JSON', 'Finite bounded JSON required') from exc

def decode(raw, maximum, *, canonical=False):
    require(type(raw) is bytes and 0 < len(raw) <= maximum, 'SOURCE_BUDGET', 'Original document exceeds byte limit')
    # Reject deep structure before json.loads allocates nested containers.
    depth = 0; quoted = False; escaped = False
    for c in raw:
        if quoted:
            if escaped: escaped = False
            elif c == 92: escaped = True
            elif c == 34: quoted = False
        elif c == 34: quoted = True
        elif c in (91, 123):
            depth += 1
            require(depth <= 40, 'SOURCE_JSON', 'JSON nesting budget exceeded')
        elif c in (93, 125): depth -= 1
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, 'SOURCE_JSON', 'Duplicate JSON key')
            result[k] = v
        return result
    def floating(value):
        number = float(value)
        require(math.isfinite(number), 'SOURCE_JSON', 'Nonfinite JSON number')
        return number
    def constant(_): raise SourceError('SOURCE_JSON', 'Nonfinite JSON token')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_float=floating, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, SourceError): raise
        raise SourceError('SOURCE_JSON', 'Invalid JSON') from exc
    if canonical:
        require(encode(value) == raw, 'SOURCE_CANONICAL', 'Canonical descriptor bytes required')
    return value

def sha(raw): return hashlib.sha256(raw).hexdigest()
def digest(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value), 'SOURCE_PIN', 'Exact lowercase SHA256 required')
    return value

def date(value):
    require(type(value) is str and re.fullmatch('[0-9]{8}', value), 'SOURCE_DATE', 'YYYYMMDD required')
    try: return datetime.strptime(value, '%Y%m%d')
    except ValueError as exc: raise SourceError('SOURCE_DATE', 'Invalid date') from exc

def number(value):
    if type(value) not in (int, float): return False
    try: return math.isfinite(value)
    except OverflowError: return False

def scope(value, *, maximum=1000):
    keys(value, {'symbols', 'start', 'end'})
    symbols = value['symbols']
    require(type(symbols) is list and 1 <= len(symbols) <= maximum and all(type(s) is str and re.fullmatch('[0-9]{6}\\.(SH|SZ)', s) for s in symbols)
            and symbols == sorted(set(symbols)), 'SOURCE_SCOPE', 'Sorted complete SH/SZ scope required')
    require(date(value['start']) <= date(value['end']), 'SOURCE_SCOPE', 'Scope interval is reversed')
    return value

@dataclass(frozen=True)
class SourceLimits:
    total_bytes: int = 64 * 1024**2
    document_bytes: int = 24 * 1024**2
    descriptor_bytes: int = 256 * 1024
    original_rows: int = 300000
    target_rows: int = 110000
    target_symbols: int = 50
    target_days: int = 366
    artifact_count: int = 580
    def check(self):
        require(type(self) is SourceLimits, 'SOURCE_BUDGET', 'SourceLimits type required')
        for f in fields(self):
            require(type(getattr(self, f.name)) is int and 1 <= getattr(self, f.name) <= f.default,
                    'SOURCE_BUDGET', 'Limits can only be reduced')
        return self

DEFAULT_LIMITS = SourceLimits()

def validate_rows(rows, meta, original_scope, limits):
    """Validate every original row before any projection; no numeric coercion."""
    require(type(meta) is dict and type(rows) is list and 0 < len(rows) <= limits.original_rows, 'SOURCE_ROWS', 'Complete rows required')
    require(type(meta.get('synthetic')) is bool, 'SOURCE_EVIDENCE', 'Explicit original synthetic state required')
    dates = meta.get('tradingDates')
    require(type(dates) is list and dates and all(type(d) is str for d in dates) and dates == sorted(set(dates)), 'SOURCE_CALENDAR', 'Complete original sessions required')
    for d in dates:
        date(d)
        require(original_scope['start'] <= d <= original_scope['end'], 'SOURCE_CALENDAR', 'Session outside original interval')
    calendar = set(dates); members = set(original_scope['symbols']); seen = set(); present = set(); first = {}
    require(type(rows[0]) is dict, 'SOURCE_ROWS', 'Rows must be objects')
    columns = set(rows[0])
    require(set(BASE) <= columns <= set(BASE) | OPTIONAL, 'SOURCE_COLUMNS', 'Only registered market columns; financial/external inputs need their own closure')
    for ordinal, row in enumerate(rows):
        keys(row, columns)
        s, d = row['ts_code'], row['trade_date']
        require(type(s) is str and s in members and type(d) is str and d in calendar, 'SOURCE_SCOPE', 'Original row outside full scope/calendar')
        require((s, d) not in seen, 'SOURCE_ROWS', 'Duplicate original observation')
        seen.add((s, d)); present.add(s)
        for name in columns - {'ts_code', 'trade_date'}:
            x = row[name]
            require(number(x) or name in OPTIONAL and x is None, 'SOURCE_NUMBER', 'Finite numeric value or declared optional null required; bool forbidden')
        require(all(row[k] > 0 for k in ('open','high','low','close','raw_close','adj_factor'))
                and row['vol'] >= 0 and row['amount'] >= 0, 'SOURCE_NUMBER', 'Invalid price/volume')
        require(row['high']+1e-9 >= max(row['open'],row['close'],row['low']) and row['low']-1e-9 <= min(row['open'],row['close'],row['high']), 'SOURCE_NUMBER', 'Inconsistent OHLC')
        if s not in first or d < first[s][1]['trade_date']: first[s] = (ordinal, row)
    require(present == members, 'SOURCE_SCOPE', 'Every original member must be observed')
    require(meta.get('adjustment') == ADJUSTMENT, 'ADJUSTMENT_ORIGIN_UNKNOWN', 'Only the retained first-observation adjustment convention is supported')
    for row in rows:
        base = first[row['ts_code']][1]['adj_factor']
        try: expected = row['raw_close'] * row['adj_factor'] / base
        except OverflowError as exc: raise SourceError('SOURCE_ADJUSTMENT', 'Adjustment arithmetic overflow') from exc
        require(math.isfinite(expected) and math.isclose(row['close'], expected, rel_tol=2e-10, abs_tol=2e-10), 'SOURCE_ADJUSTMENT', 'Original close differs from retained adjustment base')
    return [{'symbol':s,'date':first[s][1]['trade_date'],'rowOrdinal':first[s][0],
             'factorF64':struct.pack('>d',float(first[s][1]['adj_factor'])).hex()} for s in sorted(first)]

def filtered_scope(raw, ref, limits):
    keys(ref, {'scopeId','scopeRoot','format','version'})
    require(type(ref['scopeId']) is str and re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',ref['scopeId']), 'SOURCE_SCOPE', 'Scope UUID required')
    require(ref['format']=='atlas.quant.universe_scope' and type(ref['version']) is int and ref['version']==1 and sha(raw)==digest(ref['scopeRoot']), 'SOURCE_SCOPE', 'Frozen filter scope reference differs')
    # Existing scope roots bind exact incoming JS JSON bytes, not a Python re-encoding.
    s=decode(raw,limits.descriptor_bytes); keys(s,SCOPE_KEYS)
    require(s['format']=='atlas.quant.universe_scope' and type(s['version']) is int and s['version']==1
            and s['membershipPolicy']=='complete_filtered_set' and s['historicalMembershipVerified'] is False,
            'SOURCE_SCOPE','Complete existing filter scope required; no selected-member override')
    target=scope({k:s[k] for k in ('symbols','start','end')},maximum=limits.target_symbols)
    require(type(s['symbolCount']) is int and s['symbolCount']==len(s['symbols']), 'SOURCE_SCOPE', 'Full filtered member count differs')
    require((date(s['end'])-date(s['start'])).days+1 <= limits.target_days, 'SOURCE_SCOPE', 'Selected source interval exceeds profile')
    for k in ('snapshotHash','resolutionHash'): digest(s[k])
    keys(s['selection'], {'version','includeGroups','excludeGroups','includeSymbols','excludeSymbols'})
    require(type(s['selection']['version']) is int and s['selection']['version']==1 and all(type(s['selection'][k]) is list for k in ('includeGroups','excludeGroups','includeSymbols','excludeSymbols')),
            'SOURCE_SCOPE','Frozen filter rule shape invalid')
    require(type(s['catalogSnapshot']) is dict and s['catalogSnapshot'].get('hash')==s['snapshotHash'] and s['catalogSnapshot'].get('historicalMembershipVerified') is False
            and type(s['sourceUniverses']) is list and type(s['steps']) is list and type(s['algorithmVersion']) is str and 0<len(s['algorithmVersion'])<=200,
            'SOURCE_SCOPE','Frozen filter provenance invalid')
    return target
