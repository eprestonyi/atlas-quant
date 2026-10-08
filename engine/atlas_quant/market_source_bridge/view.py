"""The complete caller-frozen filter is the only membership input, never a second subset."""
from dataclasses import dataclass
from .contract import *
from .source import revalidate

@dataclass(frozen=True)
class FrozenMarketView:
    source: object
    scope_bytes: bytes
    market_bytes: bytes
    manifest_bytes: bytes
    @property
    def view_root(self): return sha(self.manifest_bytes)


def derive_view(source, filter_scope_bytes, scope_ref, *, mode, limits=DEFAULT_LIMITS):
    limits.check()
    # Revalidation also rejects constructed/replaced dataclass payloads.
    source=revalidate(source,limits=limits)
    target=filtered_scope(filter_scope_bytes,scope_ref,limits)
    original=source.descriptor['originalScope']
    require(set(target['symbols'])<=set(original['symbols']) and original['start']<=target['start']<=target['end']<=original['end'],
            'SOURCE_SCOPE','Whole frozen filter extends outside original source')
    require(type(mode) is str and mode in {'exact','explicit_subset'} and (mode=='exact')==(target==original),
            'SOURCE_SCOPE','Exact/subset mode must match entire frozen filter scope')
    rows=decode(source.rows_bytes,limits.total_bytes);meta=decode(source.provenance_bytes,limits.total_bytes)
    members=set(target['symbols']);selected=[r for r in rows if r['ts_code'] in members and target['start']<=r['trade_date']<=target['end']]
    require(0<len(selected)<=limits.target_rows and {r['ts_code'] for r in selected}==members,'SOURCE_SCOPE','Every complete-filter member needs observations; never truncate')
    sessions=[d for d in meta['tradingDates'] if target['start']<=d<=target['end']]
    transform={'kind':'frozen_filter_scope_view','version':1,'mode':mode,'universeScopeRef':scope_ref}
    provenance={'source':'FROZEN_MARKET_SOURCE_VIEW','classification':'LEGACY_CACHE_ORIGIN_UNVERIFIED' if source.descriptor['originKind']=='legacy_cache' else 'FROZEN_MARKET_DATASET_ORIGIN',
                'synthetic':meta['synthetic'],**target,'tradingDates':sessions,'rows':len(selected),
                'sourceRoot':source.source_root,'originKind':source.descriptor['originKind'],'evidence':source.descriptor['evidence'],
                'originalAdjustment':source.descriptor['adjustment'],'projection':transform,
                'rowFingerprintVersion':'exact_json_rows_v1','rowFingerprint':sha(encode(selected))}
    market=encode({'schemaVersion':1,'rows':selected,'provenance':provenance})
    require(len(market)<=limits.document_bytes,'SOURCE_BUDGET','Complete projected market exceeds budget')
    manifest={'format':VIEW_FORMAT,'version':1,'sourceRoot':source.source_root,'transform':transform,'targetScope':target,
              'sourceRows':len(rows),'selectedRows':len(selected),'removedRows':len(rows)-len(selected),
              'sourceCalendarSha256':sha(encode(meta['tradingDates'])),'selectedCalendarSha256':sha(encode(sessions)),
              'marketSha256':sha(market),'marketByteLength':len(market),'preservesRelativeRowOrder':True,
              'imputation':'none','warmupExtension':'none','adjustmentRebased':False,'ownerGrantVerified':False,'filterResolutionVerified':False}
    raw=encode(manifest)
    total=sum(len(v) for _,v in source.originals)+len(source.descriptor_bytes)+len(filter_scope_bytes)+len(market)+len(raw)
    require(len(raw)<=limits.descriptor_bytes and total<=limits.total_bytes,'SOURCE_BUDGET','Full original plus view closure exceeds budget; source is not trimmed')
    return FrozenMarketView(source,filter_scope_bytes,market,raw)
