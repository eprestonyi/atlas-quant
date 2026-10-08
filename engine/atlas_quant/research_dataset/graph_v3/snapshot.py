"""Explicit local graph-auto admission and exact numerical column snapshots.

No hosted capability, queued claim, legacy parser or execution path is changed.
Only a fresh local source restoration may register the exact frame for F.
"""
from copy import deepcopy
from dataclasses import replace

from ...engine import _prepare_data
from ...financial_statements.admission import _register_composed,_commitment
from ...financial_statements.prepare import _safe_rows
from ..codec import decode,digest,encode,keys,require,sha,uuid
from ..profile import DEFAULT_PROFILE,FORMAT,check_profile
from ..research_profile import AUTO_PROFILE,validate_research_profile as validate_auto_rules
from .manifest import GRAPH_VERSION,validate_manifest
from .dataset import GraphDatasetReader,restore_graph_dataset
from .columns import encode_table,verify_table,iter_rows
from .streams import array_chunks,object_chunks,literal,stream_digest,canonical_chunks,ROW_LIMIT

RESEARCH_PROFILE='financial_fundamental_graph_auto_50_v1'
FINGERPRINT_VERSION='research_input_financial_column_v1'
SNAPSHOT_KEYS={'schemaVersion','fingerprintVersion','datasetRef','sourceEvidenceClosure','provenance',
               'dataFingerprint','sourceDataFingerprint','financialSourceCommitment','numericInput'}


def validate_research_profile(strategy,scope,*,research_profile,dataset_version=GRAPH_VERSION):
    require(research_profile==RESEARCH_PROFILE and type(dataset_version) is int and dataset_version==GRAPH_VERSION,
            'DATASET_RESEARCH_PROFILE','Explicit local graph-auto/1 admission and dataset/3 required')
    # Reuse only the unchanged mathematical/scope rules. No old source reader,
    # snapshot codec or byte guard is bypassed or asked to admit a graph.
    return validate_auto_rules(strategy,scope,research_profile=AUTO_PROFILE,dataset_version=2)


def dataset_reference(value):
    keys(value,{'datasetId','datasetRoot','format','version'});uuid(value['datasetId']);digest(value['datasetRoot'])
    require(value['format']==FORMAT and type(value['version']) is int and value['version']==GRAPH_VERSION,
            'DATASET_FORMAT','Explicit dataset/3 reference required')
    return value


def restore_graph_for_research(strategy,reader,authorized_registry,*,research_profile):
    require(type(reader) is GraphDatasetReader or isinstance(reader,GraphDatasetReader),'DATASET_READER','Explicit graph reader required')
    validate_research_profile(strategy,reader.manifest['scope'],research_profile=research_profile,dataset_version=reader.manifest['version'])
    result=restore_graph_dataset(reader,authorized_registry)
    # This is the sole new local bridge. No serialized metadata/token can invoke
    # it without complete fresh raw preparation and exact closure comparison.
    _register_composed(result.data,result.provenance)
    return replace(result,model_admission_registered=True)


def validate_snapshot(value,*,profile=DEFAULT_PROFILE):
    check_profile(profile);keys(value,SNAPSHOT_KEYS)
    require(type(value['schemaVersion']) is int and value['schemaVersion']==3
            and value['fingerprintVersion']==FINGERPRINT_VERSION
            and value['sourceEvidenceClosure']=='separate_research_dataset_v3',
            'DATASET_SNAPSHOT','Unknown financial column snapshot discriminator')
    dataset_reference(value['datasetRef'])
    for key in ('dataFingerprint','sourceDataFingerprint'):digest(value[key])
    require(type(value['provenance']) is dict,'DATASET_SNAPSHOT','Exact snapshot provenance required')
    require(value['sourceDataFingerprint']==value['provenance'].get('dataFingerprint'),
            'DATASET_SNAPSHOT','Source numeric fingerprint differs from provenance')
    require(all(k in value['provenance'] for k in ('financialCompositionVersion','marketRoot','financialDatasetRoot','financialInputs')),
            'DATASET_SNAPSHOT','Financial source commitment is incomplete')
    require(encode(value['financialSourceCommitment'])==encode(_commitment(value['provenance'])),
            'DATASET_SNAPSHOT','Source commitment differs from exact provenance')
    verify_table(value['numericInput'])
    physical=stream_digest(canonical_chunks(value),profile.joined_bytes)
    # Include all snapshot metadata and provenance in the logical expansion
    # bound, not just the numeric rows. The source joined envelope is also
    # independently bounded by its immutable source manifest/reader.
    fields={k:(lambda v=v:literal(v,profile.joined_bytes)) for k,v in value.items() if k!='numericInput'}
    fields['rows']=lambda:array_chunks(literal(row,ROW_LIMIT) for row in iter_rows(value['numericInput']))
    logical=stream_digest(object_chunks(fields),profile.joined_bytes)
    return {'physicalSnapshot':physical,'logicalExpandedSnapshot':logical,'sourceAuthorityVerified':False,'modelAdmissionRegistered':False}


def freeze_graph_input(strategy,result,dataset_ref,*,manifest_bytes,research_profile,profile=DEFAULT_PROFILE):
    dataset_reference(dataset_ref)
    manifest=validate_manifest(manifest_bytes,expected_root=dataset_ref['datasetRoot'],profile=profile)
    normalized=validate_research_profile(strategy,manifest['scope'],research_profile=research_profile,dataset_version=manifest['version'])
    expected=next(c for c in manifest['components'] if c['componentId']=='researchColumns')
    joined=stream_digest(canonical_chunks(result.to_dataset()),profile.joined_bytes)
    require(joined['sha256']==expected['semanticRoots']['logicalJoinedSha256']
            and result.provenance['financialDatasetRoot']==expected['semanticRoots']['financialDatasetRoot'],
            'DATASET_SNAPSHOT','Actual composed rows/provenance differ from source manifest')
    require(result.model_admission_registered is True,'DATASET_RESEARCH_PROFILE','Source-only restoration cannot freeze model input')
    _,_,audit=_prepare_data(result.data,normalized,result.provenance)
    rows=_safe_rows(result.data)
    kinds={k:'dictionary' if k in ('ts_code','trade_date') or k.endswith('__available_date') else 'number' for k in result.data.columns}
    snapshot={'schemaVersion':3,'fingerprintVersion':FINGERPRINT_VERSION,'datasetRef':deepcopy(dataset_ref),
        'sourceEvidenceClosure':'separate_research_dataset_v3','provenance':deepcopy(result.provenance),
        'dataFingerprint':audit['dataSha256'],'sourceDataFingerprint':result.provenance['dataFingerprint'],
        'financialSourceCommitment':audit['financialSourceCommitment'],'numericInput':encode_table(rows,kinds)}
    validate_snapshot(snapshot,profile=profile)
    return snapshot


def restore_graph_input(strategy,snapshot_bytes,reader,authorized_registry,*,research_profile):
    require(isinstance(reader,GraphDatasetReader),'DATASET_READER','Explicit graph source reader required')
    snapshot=decode(snapshot_bytes,reader.profile.joined_bytes);validate_snapshot(snapshot,profile=reader.profile)
    require(snapshot['datasetRef']['datasetRoot']==reader.dataset_root,'DATASET_ROOT','Snapshot belongs to another source closure')
    result=restore_graph_for_research(strategy,reader,authorized_registry,research_profile=research_profile)
    regenerated=freeze_graph_input(strategy,result,snapshot['datasetRef'],manifest_bytes=reader.manifest_bytes,
                                  research_profile=research_profile,profile=reader.profile)
    require(encode(regenerated)==snapshot_bytes,'DATASET_SNAPSHOT','Snapshot differs from freshly recomposed exact numerical input')
    return result
