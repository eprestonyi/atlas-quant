"""Bounded canonical streams. No entire expanded document is accumulated."""
import hashlib
import json
from ..codec import encode, require

MIB=1024**2
ROW_LIMIT=65536
LOGICAL_LIMIT=24*MIB
PREPARED_LIMIT=64*MIB
EVENT_LIMIT=32*MIB


def canonical_chunks(value):
    """The same JSON tokens as codec.encode, without one expanded byte buffer."""
    try:
        for part in json.JSONEncoder(ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).iterencode(value):
            yield part.encode('utf-8')
    except (ValueError,TypeError,OverflowError,UnicodeError,RecursionError):
        require(False,'GRAPH_JSON','Finite canonical stream required')


def array_chunks(values):
    yield b'['
    first=True
    for chunks in values:
        if not first:yield b','
        first=False
        yield from chunks
    yield b']'


def object_chunks(fields):
    yield b'{'
    for i,key in enumerate(sorted(fields)):
        if i:yield b','
        yield encode(key);yield b':'
        yield from fields[key]()
    yield b'}'


def literal(value, limit=PREPARED_LIMIT):
    raw=encode(value)
    require(len(raw)<=limit,'GRAPH_BYTES','Canonical value exceeds component budget')
    return (raw,)


def stream_digest(chunks, limit):
    size=0;digest=hashlib.sha256()
    for raw in chunks:
        require(isinstance(raw,bytes),'GRAPH_BYTES','Canonical stream requires bytes')
        size+=len(raw)
        require(size<=limit,'GRAPH_BYTES','Logical canonical expansion exceeds its unchanged limit')
        digest.update(raw)
    return {'sha256':digest.hexdigest(),'byteLength':size}


def require_count(value, maximum, *, minimum=0):
    require(type(value) is int and minimum<=value<=maximum,'GRAPH_SHAPE','Invalid bounded count')


def require_list(value, maximum, *, minimum=0):
    require(type(value) is list and minimum<=len(value)<=maximum,'GRAPH_SHAPE','Invalid bounded list')
    return value
