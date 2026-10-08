"""Two fully verified offline origins; forecast snapshots remain explicitly unsupported."""
from dataclasses import dataclass
from .contract import *

LEGACY_KEYS=set('source classification synthetic transport retrievedAt symbols start end tradingDates rows dataFingerprint cacheHit providerCalls datasets adjustment calendar warnings observedColumns derivedColumns optionalFieldCoverage'.split())

@dataclass(frozen=True)
class FrozenMarketSource:
    descriptor_bytes: bytes
    originals: tuple[tuple[str, bytes], ...]
    rows_bytes: bytes
    provenance_bytes: bytes
    @property
    def source_root(self): return sha(self.descriptor_bytes)
    @property
    def descriptor(self): return decode(self.descriptor_bytes,DEFAULT_LIMITS.descriptor_bytes,canonical=True)


def _finish(kind, origin, originals, rows, meta, original_scope, limits, *, receipts):
    bases=validate_rows(rows,meta,original_scope,limits)
    artifacts=[{'name':k,'sha256':sha(v),'byteLength':len(v)} for k,v in sorted(originals.items())]
    evidence={'originalBytesVerified':True,'ownerGrantVerified':False,'filterResolutionVerified':False,
              'providerReceiptBytesRetained':receipts,'providerOriginIndependentlyAttested':False,
              'pointInTimeRevisionsVerified':False,'synthetic':meta['synthetic'],
              'numericReconstruction': 'original_responses_recomputed' if kind=='market_dataset' else 'legacy_cache_rows_fingerprint_and_adjustment_checked'}
    descriptor={'format':FORMAT,'version':VERSION,'originKind':kind,'origin':origin,'originalScope':original_scope,
                'originalCalendarSha256':sha(encode(meta['tradingDates'])),'originalRows':len(rows),
                'originalRowsSha256':sha(encode(rows)),'adjustment':{'method':'original_base_preserved','declaration':meta['adjustment'],'bases':bases},
                'declaredSource':meta.get('source'),'declaredClassification':meta.get('classification'),
                'artifacts':artifacts,'evidence':evidence}
    raw=encode(descriptor)
    require(len(raw)<=limits.descriptor_bytes and len(raw)+sum(len(v) for v in originals.values()) <= limits.total_bytes,
            'SOURCE_BUDGET','Complete original source and descriptor exceed closure budget')
    return FrozenMarketSource(raw,tuple(sorted(originals.items())),encode(rows),encode(meta))


def freeze_legacy_cache(raw, *, expected_sha256, limits=DEFAULT_LIMITS):
    limits.check()
    require(type(raw) is bytes and len(raw)<=limits.document_bytes and len(raw)<=limits.total_bytes,'SOURCE_BUDGET','Whole original cache exceeds budget')
    require(sha(raw)==digest(expected_sha256),'SOURCE_PIN','Original legacy bytes differ from explicit pin')
    value=decode(raw,limits.document_bytes);keys(value,{'rows','provenance'});meta=value['provenance'];keys(meta,LEGACY_KEYS)
    for k in ('source','classification','transport','retrievedAt','calendar'):
        require(type(meta[k]) is str and 0<len(meta[k])<=1000,'SOURCE_SCHEMA','Invalid original provenance string')
    declared=(meta['source'],meta['classification'],meta['synthetic'],meta['transport'],meta['calendar'])
    known_real=declared in [('TUSHARE_PRO','PROVIDER_DATA',False,t,'Tushare SSE official trading calendar; SH/SZ/BJ session alignment assumed') for t in ('private_proxy','official_https_rest')]
    known_fixture=declared==('SYNTHETIC_CACHE_FIXTURE','SYNTHETIC_FIXTURE',True,'offline_fixture','SYNTHETIC_FIXTURE_CALENDAR_DECLARATION')
    require(type(meta['synthetic']) is bool and (known_real or known_fixture),'SOURCE_ORIGIN_UNSUPPORTED','Only the declared historical Tushare cache or explicit synthetic fixture dialect is implemented')
    require(type(meta['cacheHit']) is bool and type(meta['providerCalls']) is int and 0<=meta['providerCalls']<=512,
            'SOURCE_SCHEMA','Invalid original cache request metadata')
    for k in ('datasets','warnings','observedColumns'):
        require(type(meta[k]) is list and all(type(x) is str for x in meta[k]),'SOURCE_SCHEMA','Invalid original provenance list')
    for k in ('derivedColumns','optionalFieldCoverage'):
        require(type(meta[k]) is dict,'SOURCE_SCHEMA','Invalid original provenance object')
    u=scope({k:meta[k] for k in ('symbols','start','end')})
    require(type(value['rows']) is list and type(meta['rows']) is int and meta['rows']==len(value['rows']) and meta['rows']<=110000,'SOURCE_ROWS','Original cache row count differs')
    require(sha(encode(value['rows']))==digest(meta['dataFingerprint']),'SOURCE_FINGERPRINT','Legacy canonical row fingerprint differs; no rounding or repair')
    return _finish('legacy_cache',{'decoder':'provider_cache_rows_provenance_v1','originalSha256':expected_sha256},
                   {'cache.json':raw},value['rows'],meta,u,limits,receipts=False)


def freeze_market_dataset(manifest_raw, plan_raw, scope_raw, read_part, *, expected_root, limits=DEFAULT_LIMITS):
    limits.check()
    from ..market_acquisition.reader import MarketSourceReader
    from ..market_acquisition.protocol import output_collections
    # No part callback is invoked until the sum of ALL original descriptors fits.
    reader=MarketSourceReader(manifest_raw,plan_raw,scope_raw,read_part,expected_root=expected_root)
    collections=output_collections(reader.manifest)
    declared=len(manifest_raw)+len(plan_raw)+len(scope_raw)+sum(c['byteLength'] for c in collections.values())
    count=3+sum(len(c['chunks']) for c in collections.values())
    require(declared+limits.descriptor_bytes<=limits.total_bytes and count<=limits.artifact_count,
            'SOURCE_BUDGET','Complete native original closure exceeds budget before any projection or part read')
    originals={'manifest.json':manifest_raw,'plan.json':plan_raw,'scope.json':scope_raw}
    for name,c in collections.items():
        for p in c['chunks']:
            originals[f'parts/{name}/{p["ordinal"]}.bin']=reader.part(name,p['ordinal'])
    frozen=MarketSourceReader(manifest_raw,plan_raw,scope_raw,lambda n,i:originals[f'parts/{n}/{i}.bin'],expected_root=expected_root)
    frozen.verify_integrity()
    rows=list(frozen.records('rows'));meta=next(frozen.records('provenance'))
    original_scope={k:reader.scope[k] for k in ('symbols','start','end')}
    return _finish('market_dataset',{'format':'atlas.quant.market_dataset','version':1,'datasetRoot':expected_root,'sourceKind':reader.manifest['sourceKind']},
                   originals,rows,meta,original_scope,limits,receipts=True)


def revalidate(source, *, limits=DEFAULT_LIMITS):
    require(type(source) is FrozenMarketSource,'SOURCE_SCHEMA','Frozen source type required')
    descriptor=decode(source.descriptor_bytes,limits.descriptor_bytes,canonical=True)
    raw=dict(source.originals)
    require(len(raw)==len(source.originals),'SOURCE_SCHEMA','Duplicate original role')
    if descriptor.get('originKind')=='legacy_cache':
        require(set(raw)=={'cache.json'},'SOURCE_SCHEMA','Unexpected original artifacts')
        verified=freeze_legacy_cache(raw['cache.json'],expected_sha256=descriptor['origin']['originalSha256'],limits=limits)
    elif descriptor.get('originKind')=='market_dataset':
        for name in ('manifest.json','plan.json','scope.json'):
            require(name in raw,'SOURCE_SCHEMA','Missing original source document')
        verified=freeze_market_dataset(raw['manifest.json'],raw['plan.json'],raw['scope.json'],lambda n,i:raw[f'parts/{n}/{i}.bin'],expected_root=descriptor['origin']['datasetRoot'],limits=limits)
    else: raise SourceError('SOURCE_ORIGIN_UNSUPPORTED','Only legacy_cache and market_dataset are implemented; forecast_snapshot is not registered')
    require(verified.descriptor_bytes==source.descriptor_bytes and verified.originals==source.originals
            and verified.rows_bytes==source.rows_bytes and verified.provenance_bytes==source.provenance_bytes,
            'SOURCE_INTEGRITY','Frozen source differs from fresh full original verification')
    return verified
