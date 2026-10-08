"""Lossless prepared-state graph with bounded streaming legacy-root verification.

This component neither grants registry authority nor admits financial model use.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import fields
from decimal import Decimal, InvalidOperation
from datetime import datetime
import math
import re

from ...financial_statements.contracts import UnitScope,parse_date
from ...financial_statements.results import CalendarEvidence,Dependency
from ...financial_statements.recipes import RECIPES
from ..codec import decode,encode,sha,keys,digest,require
from .streams import (MIB,ROW_LIMIT,PREPARED_LIMIT,EVENT_LIMIT,array_chunks,object_chunks,
    literal,stream_digest,require_count,require_list,canonical_chunks)

FORMAT='atlas.quant.financial_prepared_graph'
SYMBOL=re.compile(r'\d{6}\.(SH|SZ)')
RESULT_KEYS={'status','decimalValue','decimalPrecision','rounding','valueRepresentation','unit','periodEnd',
    'availableDate','reasonCodes','formulaVersion','policyVersion','mappingVersions','asOf','scope','basis',
    'revisionHistory','qualityFlags','unitEvidenceLevels','declarationHashes','unitVerified','lineageHash'}


def _date(value):
    require(type(value) is str,'GRAPH_DATE','Exact date string required')
    parse_date(value)


def _table(rows,maximum,allowed,node_limit):
    require_list(rows,maximum)
    table={};previous=''
    for item in rows:
        keys(item,{'id','value'});identity=digest(item['id'])
        require(identity>previous,'GRAPH_DICTIONARY','Dictionary ids must be unique and sorted')
        previous=identity;value=item['value'];keys(value,allowed)
        raw=encode(value)
        require(len(raw)<=node_limit and sha(raw)==identity,'GRAPH_DICTIONARY','Dictionary hash or bytes differ')
        table[identity]=value
    return table


def _number(result):
    require(result['status'] in ('ok','missing') and type(result['reasonCodes']) is list,
            'GRAPH_RESULT','Invalid state status')
    require(len(result['reasonCodes'])<=32 and all(type(x) is str and 1<=len(x)<=96 for x in result['reasonCodes']),
            'GRAPH_RESULT','Missing reasons must be bounded strings')
    if result['status']=='missing':
        require(result['decimalValue'] is None and bool(result['reasonCodes']),
                'GRAPH_RESULT','Missing state must retain its null value and reason')
        return None
    require(type(result['decimalValue']) is str and len(result['decimalValue'])<=256
            and result['reasonCodes']==[],'GRAPH_RESULT','Available state needs exact decimal and no missing reasons')
    try:
        dec=Decimal(result['decimalValue']);number=float(dec)
    except (InvalidOperation,ValueError,OverflowError):
        require(False,'GRAPH_NUMBER','Invalid decimal state')
    require(dec.is_finite() and math.isfinite(number) and not (number==0 and dec!=0),
            'GRAPH_NUMBER','State cannot convert to finite float without underflow')
    return number


def _expanded_event(event,dependencies,calendars):
    return {k:event[k] for k in ('id','symbol','stateId','computedAsOf')} | {'result':{
        **event['result'],'dependencies':[dependencies[x] for x in event['dependencyRefs']],
        'calendar':calendars[event['calendarRef']] if event['calendarRef'] is not None else None}}


def _validate(graph):
    keys(graph,{'format','version','logicalPrepared','dependencies','calendars','events','assignments',
                'panel','selection','provenance','coverage'})
    require(graph['format']==FORMAT and type(graph['version']) is int and graph['version']==1,
            'GRAPH_FORMAT','Unknown prepared graph format')
    meta=graph['logicalPrepared'];keys(meta,{'sha256','byteLength','preparedRoot'})
    digest(meta['sha256']);digest(meta['preparedRoot']);require_count(meta['byteLength'],PREPARED_LIMIT,minimum=1)
    dependencies=_table(graph['dependencies'],20000,{f.name for f in fields(Dependency)},ROW_LIMIT)
    for dep in dependencies.values():
        if dep['unit_scope'] is not None:keys(dep['unit_scope'],{f.name for f in fields(UnitScope)})
    calendars=_table(graph['calendars'],8,{f.name for f in fields(CalendarEvidence)},8192)
    for cal in calendars.values():
        digest(cal['root']);digest(cal['sessions_hash']);_date(cal['coverage_start']);_date(cal['coverage_end'])
        require(type(cal['complete']) is bool,'GRAPH_CALENDAR','Calendar completeness must be boolean')
    selection=graph['selection']
    keys(selection,{'universe','selectedStates','scope','flowBasis','announcementStart'})
    u=selection['universe'];keys(u,{'symbols','start','end'});_date(u['start']);_date(u['end']);_date(selection['announcementStart'])
    require(selection['scope']=='consolidated' and selection['flowBasis']=='ytd' and u['start']<=u['end'],
            'GRAPH_SELECTION','Unsupported financial preparation selection')
    span=(datetime.strptime(u['end'],'%Y%m%d')-datetime.strptime(u['start'],'%Y%m%d')).days+1
    require(1<=span<=366,'GRAPH_SELECTION','Prepared graph interval exceeds one inclusive year')
    panel=graph['panel'];keys(panel,{'symbols','sessions','stateIds','rowCount','columnNames'})
    symbols=require_list(panel['symbols'],50,minimum=1)
    require(all(type(s) is str and SYMBOL.fullmatch(s) for s in symbols) and symbols==sorted(set(symbols))
            and symbols==u['symbols'],'GRAPH_PANEL','Exact sorted security scope differs')
    sessions=require_list(panel['sessions'],366,minimum=1)
    for day in sessions:_date(day)
    require(sessions==sorted(set(sessions)) and u['start']<=sessions[0]<=sessions[-1]<=u['end'],
            'GRAPH_PANEL','Exact complete panel session scope differs')
    states=require_list(panel['stateIds'],16,minimum=1)
    require(all(type(s) is str and s in RECIPES for s in states) and len(set(states))==len(states)
            and states==selection['selectedStates'],'GRAPH_PANEL','Exact registered states differ')
    columns=['ts_code','trade_date']+[name for state in states for name in (state,state+'__available_date')]
    require(panel['columnNames']==columns,'GRAPH_PANEL','Prepared columns cannot be added or removed')
    require_count(panel['rowCount'],110000,minimum=1)
    require(panel['rowCount']==len(symbols)*len(sessions),'GRAPH_PANEL','Prepared panel count differs')
    events=require_list(graph['events'],10000,minimum=1)
    event_map={};used_dep=set();used_cal=set();numbers={};event_bytes=0
    for event in events:
        keys(event,{'id','symbol','stateId','computedAsOf','result','dependencyRefs','calendarRef'})
        identity=digest(event['id']);_date(event['computedAsOf'])
        require(identity not in event_map and event['symbol'] in symbols and event['stateId'] in states
                and event['computedAsOf'] in sessions,'GRAPH_EVENT','Unknown/duplicate event scope')
        keys(event['result'],RESULT_KEYS);result=event['result']
        require(type(result['decimalPrecision']) is int and result['decimalPrecision']==34
                and result['rounding']=='ROUND_HALF_EVEN' and type(result['unitVerified']) is bool,
                'GRAPH_RESULT','Fixed decimal precision/rounding or unit evidence type differs')
        require(result['asOf']==event['computedAsOf'],'GRAPH_EVENT','Event cutoff differs')
        refs=require_list(event['dependencyRefs'],128)
        require(all(type(ref) is str and ref in dependencies for ref in refs)
                and len(set(refs))==len(refs),'GRAPH_REFERENCE','Missing or duplicate dependency reference')
        cal=event['calendarRef']
        require(cal is None or type(cal) is str and cal in calendars,'GRAPH_REFERENCE','Missing calendar reference')
        used_dep.update(refs)
        if cal is not None:used_cal.add(cal)
        if result['availableDate'] is not None:
            _date(result['availableDate'])
            require(result['availableDate']<=event['computedAsOf'],'GRAPH_AVAILABILITY','Future state availability')
        numbers[identity]=_number(result)
        expanded=_expanded_event(event,dependencies,calendars)
        body={k:v for k,v in expanded['result'].items() if k!='lineageHash'}
        require(sha(encode(body))==digest(result['lineageHash']),'GRAPH_LINEAGE','Expanded lineage identity differs')
        require(sha(encode({k:v for k,v in expanded.items() if k!='id'}))==identity,
                'GRAPH_EVENT','Expanded event identity differs')
        event_bytes+=len(encode(expanded))+1
        require(event_bytes+2<=EVENT_LIMIT,'GRAPH_BYTES','Expanded event stream exceeds existing 32 MiB limit')
        event_map[identity]=event
    require(used_dep==set(dependencies) and used_cal==set(calendars),'GRAPH_REFERENCE','Unused dictionary entries')
    assignments=require_list(graph['assignments'],10000,minimum=1)
    by_symbol={s:[] for s in symbols};used_events=set();last_order=None;positions={d:i for i,d in enumerate(sessions)}
    for assignment in assignments:
        keys(assignment,{'symbol','from','through','states'});s=assignment['symbol']
        require(s in by_symbol and assignment['from'] in positions and assignment['through'] in positions
                and assignment['from']<=assignment['through'],'GRAPH_ASSIGNMENT','Invalid assignment scope')
        order=(s,assignment['from'])
        require(last_order is None or last_order<order,'GRAPH_ASSIGNMENT','Assignments must retain sorted unique order')
        last_order=order;keys(assignment['states'],set(states))
        for state,ref in assignment['states'].items():
            require(type(ref) is str and ref in event_map,'GRAPH_REFERENCE','Missing assignment event')
            event=event_map[ref]
            require(event['symbol']==s and event['stateId']==state and event['computedAsOf']<=assignment['from'],
                    'GRAPH_ASSIGNMENT','Assignment cannot change event scope or availability')
            used_events.add(ref)
        by_symbol[s].append(assignment)
    for s,items in by_symbol.items():
        expected=0
        for item in items:
            require(positions[item['from']]==expected,'GRAPH_ASSIGNMENT','Assignment gap or overlap')
            expected=positions[item['through']]+1
        require(expected==len(sessions),'GRAPH_ASSIGNMENT','Assignment coverage incomplete')
    require(used_events==set(event_map),'GRAPH_ASSIGNMENT','Unreferenced events cannot be silently ignored')
    require(type(graph['provenance']) is dict and graph['provenance'].get('preparedRoot')==meta['preparedRoot'],
            'GRAPH_ROOT','Prepared provenance root differs')
    digest(graph['provenance'].get('inputRoot'))
    require(graph['provenance'].get('unitPolicy') in ('allow_declared','verified_only'),
            'GRAPH_ROOT','Prepared unit policy missing or invalid')
    require(graph['provenance'].get('panelRows')==panel['rowCount'] and graph['provenance'].get('stateEvents')==len(events),
            'GRAPH_ROOT','Prepared provenance counts differ')
    keys(graph['coverage'],set(states))
    for value in graph['coverage'].values():
        keys(value,{'okRows','missingRows','reasons'})
        require_count(value['okRows'],panel['rowCount']);require_count(value['missingRows'],panel['rowCount'])
        require(type(value['reasons']) is dict,'GRAPH_COVERAGE','Missing reasons require a count map')
    return dependencies,calendars,event_map,numbers,by_symbol


def _panel_rows(graph,context):
    _,_,events,numbers,by_symbol=context
    for symbol in graph['panel']['symbols']:
        items=by_symbol[symbol];index=0
        for day in graph['panel']['sessions']:
            while day>items[index]['through']:index+=1
            assignment=items[index];row={'ts_code':symbol,'trade_date':day}
            for state in graph['panel']['stateIds']:
                ref=assignment['states'][state];value=events[ref]['result']
                row[state]=numbers[ref]
                row[state+'__available_date']=value['availableDate'] if value['status']=='ok' else None
            yield row


def iter_panel_rows(graph):
    yield from _panel_rows(graph,_validate(graph))


def _legacy_stream(graph,context,prepared_root=False):
    deps,cals,*_=context
    fields={'panel':lambda:array_chunks(literal(row,ROW_LIMIT) for row in _panel_rows(graph,context)),
            'stateEvents':lambda:array_chunks(literal(_expanded_event(event,deps,cals),512*1024) for event in graph['events']),
            'assignments':lambda:iter(literal(graph['assignments'])),
            'coverage':lambda:iter(literal(graph['coverage']))}
    if prepared_root:
        fields.update({'inputRoot':lambda:iter(literal(graph['provenance']['inputRoot'])),
            'unitPolicy':lambda:iter(literal(graph['provenance']['unitPolicy'])),
            'selection':lambda:iter(literal(graph['selection']))})
    else:fields['provenance']=lambda:iter(literal(graph['provenance']))
    return object_chunks(fields)


def verify_graph(graph,*,expected_prepared_root,expected_payload_sha256):
    digest(expected_prepared_root);digest(expected_payload_sha256)
    context=_validate(graph)
    physical=stream_digest(canonical_chunks(graph),PREPARED_LIMIT)
    meta=graph['logicalPrepared']
    require(meta['preparedRoot']==expected_prepared_root and meta['sha256']==expected_payload_sha256,
            'GRAPH_ROOT','Graph does not match the caller-pinned logical roots')
    logical=stream_digest(_legacy_stream(graph,context),PREPARED_LIMIT)
    require(logical=={k:meta[k] for k in ('sha256','byteLength')},'GRAPH_ROOT','Reconstructed prepared payload differs')
    root=stream_digest(_legacy_stream(graph,context,True),PREPARED_LIMIT)
    require(root['sha256']==expected_prepared_root,'GRAPH_ROOT','Reconstructed original preparedRoot differs')
    # Independently aggregate status/reason coverage from original intervals.
    coverage={s:{'okRows':0,'missingRows':0,'reasons':Counter()} for s in graph['panel']['stateIds']}
    events=context[2];positions={d:i for i,d in enumerate(graph['panel']['sessions'])}
    for a in graph['assignments']:
        n=positions[a['through']]-positions[a['from']]+1
        for state,ref in a['states'].items():
            result=events[ref]['result'];status=result['status'];coverage[state]['okRows' if status=='ok' else 'missingRows']+=n
            if status=='missing':
                for reason in result['reasonCodes']:coverage[state]['reasons'][reason]+=n
    require(encode(coverage)==encode(graph['coverage']),'GRAPH_COVERAGE','Reconstructed coverage differs')
    return {'transportVerified':True,'logicalPrepared':logical,'preparedRoot':root['sha256'],
            'physicalBytes':physical['byteLength'],'sourceAuthorityVerified':False,'modelFitted':False}


def encode_graph(payload,selection):
    keys(payload,{'panel','provenance','stateEvents','assignments','coverage'})
    require_list(payload['panel'],110000,minimum=1)
    states=selection['selectedStates'];symbols=selection['universe']['symbols']
    sessions=sorted({r['trade_date'] for r in payload['panel']})
    dependencies={};calendars={};events=[]
    for original in payload['stateEvents']:
        e=deepcopy(original);result=e['result'];refs=[]
        for dep in result.pop('dependencies'):
            identity=sha(encode(dep));dependencies[identity]=dep;refs.append(identity)
        cal=result.pop('calendar');ref=None
        if cal is not None:ref=sha(encode(cal));calendars[ref]=cal
        events.append({**e,'dependencyRefs':refs,'calendarRef':ref})
    logical=stream_digest(canonical_chunks(payload),PREPARED_LIMIT)
    graph={'format':FORMAT,'version':1,'logicalPrepared':{**logical,'preparedRoot':payload['provenance']['preparedRoot']},
        'dependencies':[{'id':k,'value':v} for k,v in sorted(dependencies.items())],
        'calendars':[{'id':k,'value':v} for k,v in sorted(calendars.items())],
        'events':events,'assignments':list(deepcopy(payload['assignments'])),'selection':deepcopy(selection),
        'provenance':deepcopy(payload['provenance']),'coverage':deepcopy(payload['coverage']),
        'panel':{'symbols':list(symbols),'sessions':sessions,'stateIds':list(states),'rowCount':len(payload['panel']),
            'columnNames':['ts_code','trade_date']+[n for s in states for n in (s,s+'__available_date')]}}
    verify_graph(graph,expected_prepared_root=graph['logicalPrepared']['preparedRoot'],expected_payload_sha256=logical['sha256'])
    return graph


def decode_graph(raw,*,expected_prepared_root,expected_payload_sha256):
    graph=decode(raw,PREPARED_LIMIT)
    verify_graph(graph,expected_prepared_root=expected_prepared_root,expected_payload_sha256=expected_payload_sha256)
    return graph
