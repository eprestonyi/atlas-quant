"""Exact finite numeric/string table, independently verified against logical rows.

Only a pure codec. A valid table never establishes provenance or source authority.
"""
import math
import re
from ..codec import decode,encode,keys,digest,require
from .streams import (ROW_LIMIT,LOGICAL_LIMIT,require_count,require_list,array_chunks,
    literal,stream_digest,canonical_chunks)

FORMAT='atlas.quant.exact_column_table'
NAME=re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,95}')
ROWS=110000
COLUMNS=128


def _numeric(value):
    require(value is None or type(value) in (int,float),'COLUMN_VALUE','Numeric bool/object/string is forbidden')
    if value is not None:
        try:finite=math.isfinite(value)
        except OverflowError:finite=False
        require(finite,'COLUMN_VALUE','Numeric value must be finite')


def _text(value):
    require(value is None or type(value) is str and len(value.encode('utf-8'))<=256,
            'COLUMN_VALUE','Dictionary cells must be bounded strings or null')


def _descriptor(value):
    keys(value,{'sha256','byteLength'});digest(value['sha256'])
    require_count(value['byteLength'],LOGICAL_LIMIT,minimum=2)


def _columns(table):
    keys(table,{'format','version','rowCount','columns','logicalRows'})
    require(table['format']==FORMAT and type(table['version']) is int and table['version']==1,
            'COLUMN_FORMAT','Unknown exact column table version')
    require_count(table['rowCount'],ROWS,minimum=1)
    columns=require_list(table['columns'],COLUMNS,minimum=1)
    _descriptor(table['logicalRows'])
    names=set()
    for column in columns:
        require(type(column) is dict,'COLUMN_SHAPE','Column must be an object')
        name=column.get('name')
        require(type(name) is str and bool(NAME.fullmatch(name)) and name not in names,
                'COLUMN_NAME','Column name invalid or repeated')
        names.add(name)
        if column.get('kind')=='number':
            keys(column,{'name','kind','values'})
            values=require_list(column['values'],ROWS)
            require(len(values)==table['rowCount'],'COLUMN_LENGTH','Column row count differs')
            for value in values:_numeric(value)
        else:
            keys(column,{'name','kind','dictionary','indices'})
            require(column['kind']=='dictionary','COLUMN_KIND','Unknown column encoding')
            values=require_list(column['dictionary'],ROWS,minimum=1)
            tokens=[]
            for value in values:_text(value);tokens.append(encode(value))
            require(len(set(tokens))==len(tokens),'COLUMN_DICTIONARY','Dictionary values must be unique')
            indices=require_list(column['indices'],ROWS)
            require(len(indices)==table['rowCount'],'COLUMN_LENGTH','Column row count differs')
            observed=set()
            for index in indices:
                require_count(index,len(values)-1)
                if index not in observed:
                    require(index==len(observed),'COLUMN_DICTIONARY','Dictionary must follow first occurrence order')
                    observed.add(index)
            require(len(observed)==len(values),'COLUMN_DICTIONARY','Unused dictionary value')
    return columns


def _rows(table,columns):
    for index in range(table['rowCount']):
        row={c['name']:(c['values'][index] if c['kind']=='number' else c['dictionary'][c['indices'][index]]) for c in columns}
        # Encode once per row to enforce the bounded logical expansion before yielding.
        require(len(encode(row))<=ROW_LIMIT,'COLUMN_ROW_BYTES','Logical row exceeds budget')
        yield row


def iter_rows(table):
    """Structural validation precedes iteration; call verify_table for identity."""
    yield from _rows(table,_columns(table))


def verify_table(table):
    columns=_columns(table)
    stream_digest(canonical_chunks(table),LOGICAL_LIMIT)
    actual=stream_digest(array_chunks((literal(row,ROW_LIMIT) for row in _rows(table,columns))),LOGICAL_LIMIT)
    require(actual==table['logicalRows'],'COLUMN_IDENTITY','Exact reconstructed logical rows differ')
    return {'transportVerified':True,'logicalRows':actual,'rowCount':table['rowCount'],
            'sourceAuthorityVerified':False,'modelFitted':False}


def encode_table(rows,column_kinds):
    """Explicit schema prevents ambiguous all-null columns and missing-key coercion."""
    require(type(column_kinds) is dict and 1<=len(column_kinds)<=COLUMNS,
            'COLUMN_SHAPE','Explicit bounded column schema required')
    columns=[];dictionary_maps=[]
    for name,kind in column_kinds.items():
        require(type(name) is str and bool(NAME.fullmatch(name)),'COLUMN_NAME','Invalid column name')
        require(kind in ('number','dictionary'),'COLUMN_KIND','Unknown column encoding')
        columns.append({'name':name,'kind':kind,**({'values':[]} if kind=='number' else {'dictionary':[],'indices':[]})})
        dictionary_maps.append({})
    count=0;size=2
    # Only the small rolling hash state is retained for source rows.
    import hashlib
    hasher=hashlib.sha256();hasher.update(b'[')
    for row in rows:
        require(type(row) is dict and set(row)==set(column_kinds),'COLUMN_SHAPE','Missing keys and null are distinct')
        count+=1;require_count(count,ROWS,minimum=1)
        raw=encode(row);require(len(raw)<=ROW_LIMIT,'COLUMN_ROW_BYTES','Logical row exceeds budget')
        size+=len(raw)+(1 if count>1 else 0)
        require(size<=LOGICAL_LIMIT,'COLUMN_BYTES','Logical row array exceeds unchanged 24 MiB limit')
        if count>1:hasher.update(b',')
        hasher.update(raw)
        for c,index in zip(columns,dictionary_maps):
            value=row[c['name']]
            if c['kind']=='number':_numeric(value);c['values'].append(value)
            else:
                _text(value);token=encode(value)
                if token not in index:index[token]=len(c['dictionary']);c['dictionary'].append(value)
                c['indices'].append(index[token])
    require_count(count,ROWS,minimum=1);hasher.update(b']')
    table={'format':FORMAT,'version':1,'rowCount':count,'columns':columns,
           'logicalRows':{'sha256':hasher.hexdigest(),'byteLength':size}}
    verify_table(table)
    return table


def decode_table(raw):
    table=decode(raw,LOGICAL_LIMIT)
    verify_table(table)
    return table
