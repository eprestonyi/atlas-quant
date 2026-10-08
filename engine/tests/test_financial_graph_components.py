"""Pure unadmitted codecs: exact legacy roots, values, type and hostile references."""
from copy import deepcopy
import json
import math
import pytest

from atlas_quant.financial_statements.package import decode_package,prepare_package
from atlas_quant.research_dataset import DatasetReader
from atlas_quant.research_dataset.compose import prepared_payload
from atlas_quant.research_dataset.codec import encode,sha
from atlas_quant.research_dataset.graph_v3 import (encode_table,decode_table,verify_table,iter_rows,
    encode_graph,decode_graph,verify_graph,iter_panel_rows)
from atlas_quant.research_dataset.graph_v3.streams import canonical_chunks,stream_digest
from types import SimpleNamespace
from test_research_dataset_components import sources


@pytest.fixture(scope='module')
def graph_fixture(sources):
    package=decode_package(sources['source'].package_bytes)
    payload=prepared_payload(SimpleNamespace(prepared=prepare_package(package)))
    graph=encode_graph(payload,package['selection'])
    return graph,payload


def pins(graph):
    return {'expected_prepared_root':graph['logicalPrepared']['preparedRoot'],
            'expected_payload_sha256':graph['logicalPrepared']['sha256']}


def test_exact_numeric_types_null_keys_and_first_occurrence_dictionary():
    rows=[{'x':1,'name':'b','z':-0.0},{'x':1.0,'name':None,'z':0.0},{'x':None,'name':'a','z':-0.0},
          {'x':4,'name':'b','z':2.5}]
    table=encode_table(rows,{'x':'number','name':'dictionary','z':'number'})
    assert table['columns'][1]['dictionary']==['b',None,'a']
    assert encode(list(iter_rows(decode_table(encode(table)))))==encode(rows)
    output=list(iter_rows(table))
    assert type(output[0]['x']) is int and type(output[1]['x']) is float
    assert math.copysign(1,output[0]['z'])==-1
    assert verify_table(table)['sourceAuthorityVerified'] is False
    for bad in [[{'x':None}], [{'x':True,'name':'b','z':0.}], [{'x':float('nan'),'name':'b','z':0.}]]:
        with pytest.raises(ValueError):encode_table(bad,{'x':'number','name':'dictionary','z':'number'})


@pytest.mark.parametrize('attack',['length','bool_index','bool_number','dictionary_order','unused','unknown_column','version','changed_int_float'])
def test_column_hostile_shapes_and_type_changes_are_not_normalized(attack):
    table=encode_table([{'x':1,'name':'b'},{'x':2.0,'name':'a'}],{'x':'number','name':'dictionary'})
    if attack=='length':table['columns'][0]['values'].pop()
    if attack=='bool_index':table['columns'][1]['indices'][0]=False
    if attack=='bool_number':table['columns'][0]['values'][0]=True
    if attack=='dictionary_order':table['columns'][1].update(dictionary=['a','b'],indices=[1,0])
    if attack=='unused':table['columns'][1]['dictionary'].append('unused')
    if attack=='unknown_column':table['columns'][0]['extra']=None
    if attack=='version':table['version']=True
    if attack=='changed_int_float':table['columns'][0]['values'][0]=1.0
    with pytest.raises(ValueError):verify_table(table)


def test_streamed_canonical_tokens_equal_existing_codec_and_respect_budget():
    source={'b':[1,1.0,-0.0,None,'中'],'a':{'value':2e-15}}
    expected=encode(source)
    assert b''.join(canonical_chunks(source))==expected
    assert stream_digest(canonical_chunks(source),len(expected))=={'sha256':sha(expected),'byteLength':len(expected)}
    with pytest.raises(ValueError):stream_digest(canonical_chunks(source),len(expected)-1)


def test_state_decimal_conversion_never_hides_underflow_nonfinite_or_signed_zero():
    from atlas_quant.research_dataset.graph_v3.prepared import _number
    for token in ['NaN','Infinity','-Infinity','1e-9999','1e9999']:
        with pytest.raises(ValueError):_number({'status':'ok','reasonCodes':[],'decimalValue':token})
    assert math.copysign(1,_number({'status':'ok','reasonCodes':[],'decimalValue':'-0'}))==-1
    with pytest.raises(ValueError):_number({'status':'missing','reasonCodes':[],'decimalValue':None})


def test_prepared_graph_reconstructs_exact_hand_fixture_rows_payload_and_prepared_root(graph_fixture):
    graph,payload=graph_fixture
    report=verify_graph(graph,**pins(graph))
    assert report['logicalPrepared']=={'sha256':sha(encode(payload)),'byteLength':len(encode(payload))}
    assert report['preparedRoot']==payload['provenance']['preparedRoot']
    assert report['sourceAuthorityVerified'] is False and report['modelFitted'] is False
    assert encode(list(iter_panel_rows(decode_graph(encode(graph),**pins(graph)))))==encode(payload['panel'])
    assert report['physicalBytes']<report['logicalPrepared']['byteLength']
    with pytest.raises(ValueError):DatasetReader(encode(graph),lambda *_:b'')
    with pytest.raises(ValueError):verify_graph(graph,**{**pins(graph),'expected_prepared_root':'0'*64})


@pytest.mark.parametrize('attack',['dependency_hash','duplicate_dependency','dangling_dependency','unused_calendar',
    'missing_null_key','bad_lineage','overlap','gap','future_availability','missing_event','prepared_root','column','version'])
def test_graph_fail_closed_before_root_or_authority_claim(graph_fixture,attack):
    original,_=graph_fixture;g=deepcopy(original)
    if attack=='dependency_hash':g['dependencies'][0]['value']['raw_decimal']='12345'
    if attack=='duplicate_dependency':g['dependencies'].append(deepcopy(g['dependencies'][0]))
    if attack=='dangling_dependency':g['events'][0]['dependencyRefs']=['0'*64]
    if attack=='unused_calendar':
        value=deepcopy(g['calendars'][0]['value']);value['evidence_reference']='unused'
        g['calendars'].append({'id':sha(encode(value)),'value':value});g['calendars'].sort(key=lambda x:x['id'])
    if attack=='missing_null_key':del g['events'][0]['result']['decimalValue']
    if attack=='bad_lineage':g['events'][0]['result']['lineageHash']='0'*64
    if attack=='overlap':g['assignments'].append(deepcopy(g['assignments'][0]))
    if attack=='gap':g['assignments'][0]['from']=g['panel']['sessions'][1]
    if attack=='future_availability':g['events'][0]['result']['availableDate']='20990101'
    if attack=='missing_event':g['events'].pop()
    if attack=='prepared_root':g['logicalPrepared']['preparedRoot']='0'*64;g['provenance']['preparedRoot']='0'*64
    if attack=='column':g['panel']['columnNames'].append('ignored')
    if attack=='version':g['version']=True
    with pytest.raises(ValueError):verify_graph(g,**pins(original))
