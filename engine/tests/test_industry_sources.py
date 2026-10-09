"""Classification identities and executable history remain separate contracts."""
from collections import Counter
import json
from pathlib import Path

from atlas_quant.context_sources import REGISTRY, FIELDS
from atlas_quant.research_registry import build_catalog

ROOT = Path(__file__).resolve().parents[2]
SOURCES = json.loads((ROOT/'engine/atlas_quant/industry_sources.json').read_text())


def test_complete_versioned_classifications_have_unique_resolvable_parents():
    items = SOURCES['items']
    by_id = {item['id']: item for item in items}
    assert len(by_id) == len(items) == 784
    assert Counter(item['level'] for item in items if item['market'] == 'CN') == {1: 31, 2: 134, 3: 346}
    assert Counter(item['level'] for item in items if item['market'] == 'US') == {1: 11, 2: 25, 3: 74, 4: 163}
    for item in items:
        assert len(item['path']) == item['level'] and item['path'][-1] == item['name']
        assert 'Discontinued' not in item['name']
        if item['parentId']:
            parent = by_id[item['parentId']]
            assert parent['market'] == item['market'] and parent['level'] == item['level']-1
            assert item['path'][:-1] == parent['path']


def test_only_published_cn_indices_become_context_factors():
    ready = [item for item in SOURCES['items'] if item['historyStatus'] == 'adapter_supported_requires_observations']
    assert len(ready) == 414
    assert Counter(item['level'] for item in ready) == {1: 31, 2: 124, 3: 259}
    catalog = build_catalog()
    factors = {item['id']: item for item in catalog['factors']}
    for item in SOURCES['items']:
        if item in ready:
            factor = factors[item['factorId']]
            assert factor['scope'] == 'global' and factor['dataRequirement'] == 'named_index_history'
            assert all(field in FIELDS for field in factor['requiredFields'])
        else:
            assert 'factorId' not in item
    assert len(REGISTRY['items']) == len(ready)+6+60
    assert catalog['industrySources'] == SOURCES


def test_etf_proxy_metadata_never_masquerades_as_history_or_exact_classification():
    by_id = {item['id']: item for item in SOURCES['items']}
    assert len(SOURCES['proxies']) == 30
    for proxy in SOURCES['proxies']:
        assert proxy['sourceKind'] == 'etf_proxy' and proxy['isOfficialIndex'] is False
        assert proxy['mappingKind'] == 'research_proxy_not_equivalent'
        positive = {probe['symbol'] for probe in SOURCES['historyProbes'] if probe['api'] == 'yfinance_history' and probe.get('rowCount',0) > 0}
        assert proxy['historyStatus'] == ('adapter_supported_requires_observations' if proxy['symbol'] in positive else 'adapter_supported_history_unverified')
        assert proxy['provider'] == 'YAHOO_YFINANCE'
        assert proxy['factorId'].startswith('context_yf_')
        assert 'corporate_actions' in proxy['requiredData'] and 'asof_timestamp' in proxy['requiredData']
        assert any(source['ts_code'] == proxy['symbol'] and source['api'] == 'us_daily_adj' for source in REGISTRY['items'])
        for identity in proxy['classificationIds']:
            assert proxy['id'] in by_id[identity]['proxyIds']
    assert SOURCES['summary']['observedHistoryCount'] is None
    assert SOURCES['summary']['inventoryIsNotCoverage'] is True


def test_unpublished_classification_keeps_identity_without_invented_etf_mapping():
    by_id = {item['id']: item for item in SOURCES['items']}
    foundry = by_id['sw2021_270106']
    assert foundry['name'] == '集成电路制造'
    assert foundry['historyStatus'] == 'not_published_in_source'
    assert foundry['proxyIds'] == []
    assert 'ext_ctx_850816_si_close' not in FIELDS
    semiconductors = by_id['gics2023_45301020']
    assert semiconductors['proxyIds'] == ['us_etf_xsd']


def test_current_provider_identity_overrides_retired_document_code():
    row = next(item for item in SOURCES['items'] if item['id'] == 'sw2021_230501')
    assert row['contextCode'] == '850401.SI'
    assert row['factorId'] == 'context_850401_si_price'
    assert 'ext_ctx_850401_si_close' in FIELDS
    assert 'ext_ctx_850412_si_close' not in FIELDS
    snapshot = json.loads((ROOT/'data/sw2021-current-classification.json').read_text())
    assert len(snapshot['records']) == 480
    assert all(source['responseSha256'] and source['observedAt'] for source in snapshot['sources'])


def test_empty_us_probe_does_not_promote_all_etf_adapters_to_ready():
    xsd = next(proxy for proxy in SOURCES['proxies'] if proxy['symbol'] == 'XSD')
    assert xsd['lastProbe']['api'] == 'yfinance_history' and xsd['lastProbe']['rowCount'] == 9
    assert all(probe['rowCount'] == 0 for probe in SOURCES['historyProbes'] if probe['api'] == 'us_daily_adj')
    assert all(factor.get('historyStatus') == 'adapter_supported_history_unverified'
               for factor in build_catalog()['factors'] if 'us_daily_adj' in factor['sourceDatasets'])
