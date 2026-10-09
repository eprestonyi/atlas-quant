#!/usr/bin/env python3
"""Import factual industry identities from frozen official source documents.

No provider requests, constituents, price history or index/ETF equivalence are
inferred. Pass the SW2021 Markdown table and the MSCI official structure XLSX.
The original documents remain outside the public source distribution.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SW_URL = 'https://tushare.pro/document/2?doc_id=181'
GICS_URL = 'https://www.msci.com/documents/1296102/23c8ec04-fd1c-3518-e04c-4aa37027889d'


def sw_rows(path):
    rows = {}
    for line in path.read_text().splitlines():
        line = re.sub(r'^L\d+: ', '', line).strip().strip('|')
        values = [part.strip() for part in line.split('|')]
        if len(values) < 9 or not re.fullmatch(r'\d{6}', values[0]) or not re.fullmatch(r'\d{6}', values[1]):
            continue
        code, index, l1, l2, l3, level, published = values[:7]
        names = [name for name in (l1, l2, l3) if name]
        row = {'id': 'sw2021_'+code, 'market': 'CN', 'taxonomy': 'SW2021',
               'code': code, 'name': names[-1], 'path': names, 'level': len(names),
               'parentId': None if len(names) == 1 else 'sw2021_'+(code[:2]+'0000' if len(names) == 2 else code[:4]+'00'),
               'sourceKind': 'official_index', 'sourceUrl': SW_URL, 'contextCode': index+'.SI',
               'publicationStatusAsOf': 'SW2021_OFFICIAL_STRUCTURE',
               'historyStatus': 'adapter_supported_requires_observations' if published == '1' else 'not_published_in_source',
               'proxyIds': []}
        if published == '1':
            row['factorId'] = 'context_'+index+'_si_price'
        if row['id'] in rows and rows[row['id']] != row:
            raise ValueError('Conflicting industry identity '+row['id'])
        rows[row['id']] = row
    if Counter(row['level'] for row in rows.values()) != {1: 31, 2: 134, 3: 346}:
        raise ValueError('Incomplete or changed SW2021 table; review source before importing')
    return sorted(rows.values(), key=lambda row: row['code'])


def gics_rows(path):
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as archive:
        strings = [''.join(value.itertext()) for value in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('m:si', ns)]
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    rows = {}
    for row in sheet.findall('.//m:row', ns):
        cells = {}
        for cell in row.findall('m:c', ns):
            value = cell.find('m:v', ns)
            if value is not None:
                cells[re.sub(r'\d+', '', cell.attrib['r'])] = strings[int(value.text)] if cell.attrib.get('t') == 's' else value.text
        for level, (key, label) in enumerate([('A','B'),('C','D'),('E','F'),('G','H')], 1):
            code, name = cells.get(key, ''), cells.get(label, '')
            if not re.fullmatch(r'\d{'+str(2*level)+'}', code) or not name or 'Discontinued' in name:
                continue
            name = re.sub(r'\s*\([^)]*\)\s*$', '', name).rstrip('* ').strip()
            rows[code] = {'id': 'gics2023_'+code, 'market': 'US', 'taxonomy': 'GICS2023',
                          'code': code, 'name': name, 'level': level,
                          'parentId': 'gics2023_'+code[:-2] if level > 1 else None,
                          'sourceKind': 'classification', 'sourceUrl': GICS_URL,
                          'historyStatus': 'not_connected', 'proxyIds': []}
    if Counter(row['level'] for row in rows.values()) != {1: 11, 2: 25, 3: 74, 4: 163}:
        raise ValueError('Incomplete or changed GICS structure; review before importing')
    for row in rows.values():
        row['path'] = [rows[row['code'][:length]]['name'] for length in range(2, len(row['code'])+1, 2)]
    return sorted(rows.values(), key=lambda row: row['code'])


def build(sw, gics):
    items = sw_rows(sw) + gics_rows(gics)
    current = json.loads((ROOT/'data/sw2021-current-classification.json').read_text())
    by_code = {row['code']: row for row in items if row['market'] == 'CN'}
    if Counter(row['level'] for row in current['records']) != {'L2': 134, 'L3': 346}:
        raise ValueError('Incomplete current SW classification snapshot')
    for update in current['records']:
        row = by_code[update['industry_code']]
        parent = by_code[update['parent_code']]
        row.update(name=update['industry_name'], contextCode=update['index_code'],
                   parentId=parent['id'], path=parent['path']+[update['industry_name']],
                   publicationStatusAsOf=next(source['observedAt'] for source in current['sources'] if source['params']['level'] == update['level']),
                   historyStatus='adapter_supported_requires_observations' if update['is_pub'] == '1' else 'not_published_in_source')
        row.pop('factorId', None)
        if update['is_pub'] == '1':
            row['factorId'] = 'context_'+update['index_code'].lower().replace('.', '_')+'_price' 
    proxies = json.loads((ROOT/'data/industry-proxies.json').read_text())['items']
    probes = json.loads((ROOT/'data/industry-history-probes.json').read_text())['probes']
    yahoo_probes = json.loads((ROOT/'data/yfinance-history-probes.json').read_text())['probes']
    by_id = {row['id']: row for row in items}
    for proxy in proxies:
        proxy['historyStatus'] = 'adapter_supported_history_unverified'
        proxy['lastProbe'] = next((probe for probe in yahoo_probes if probe['symbol'] == proxy['symbol']), None)
        if proxy['lastProbe'] and proxy['lastProbe']['rowCount'] > 0:
            proxy['historyStatus'] = 'adapter_supported_requires_observations'
        proxy['factorId'] = 'context_yf_'+proxy['symbol'].lower()+'_price'
        proxy['providerApi'] = 'yfinance_history'
        proxy['provider'] = 'YAHOO_YFINANCE'
        proxy['dataSourceUrl'] = 'https://finance.yahoo.com/quote/'+proxy['symbol']+'/history/'
        for identity in proxy['classificationIds']:
            if identity not in by_id:
                raise ValueError('Unregistered proxy classification '+identity)
            by_id[identity]['proxyIds'].append(proxy['id'])
    sources = [{'id': 'SW2021', 'url': SW_URL, 'documentSha256': hashlib.sha256(sw.read_bytes()).hexdigest(),
                'documentKind': 'official_table_extracted_text', 'classificationVersion': '2021'},
               {'id': 'GICS2023', 'url': GICS_URL, 'documentSha256': hashlib.sha256(gics.read_bytes()).hexdigest(),
                'documentKind': 'official_workbook', 'classificationVersion': '2023-03-17',
                'definitionEnhancementsThrough': '2025-02', 'discontinuedRowsExcluded': True},
               {'id': 'SW2021_CURRENT_CLASSIFICATION', 'url': SW_URL, 'documentKind': 'parsed_provider_response',
                'requests': current['sources'], 'historicalConstituents': False}]
    taxonomy = {'schema': 'industry-sources/1', 'sources': sources, 'items': items, 'proxies': proxies,
                'summary': {'classificationIdentities': len(items), 'cnClassificationIdentities': 511,
                            'usClassificationIdentities': 273, 'cnPublishedIndexAdapters': sum(row['market'] == 'CN' and row['historyStatus'] == 'adapter_supported_requires_observations' for row in items),
                            'etfProxyIdentities': len(proxies), 'observedHistoryCount': None,
                            'inventoryIsNotCoverage': True}, 'historyProbes': probes+yahoo_probes}
    context_path = ROOT/'engine/atlas_quant/context_sources.json'
    context = json.loads(context_path.read_text())
    original = {row['ts_code']: row for row in context['items'] if row['api'] == 'index_daily' or row.get('level') == 1}
    # Keep the original identities and ordering for historical fixture stability.
    additions = []
    for row in items:
        if row['market'] != 'CN' or row['historyStatus'] != 'adapter_supported_requires_observations':
            continue
        entry = {'ts_code': row['contextCode'], 'name': row['name'], 'api': 'sw_daily',
                 'category': '行业指数', 'scope': 'global', 'market': 'CN',
                 'taxonomy': 'SW2021', 'level': row['level'], 'industryId': row['id'],
                 'industryPath': row['path'], 'sourceKind': 'official_index', 'sourceUrl': SW_URL}
        if entry['ts_code'] in original:
            original[entry['ts_code']].update(entry)
        else:
            additions.append(entry)
    context['items'] = [row for row in original.values() if row['api'] != 'us_daily_adj'] + sorted(additions, key=lambda row: row['ts_code'])
    context['items'].extend({'ts_code': proxy['symbol'], 'name': proxy['name'],
                             'api': 'us_daily_adj', 'category': '美国行业ETF', 'scope': 'global',
                             'market': 'US', 'sourceKind': 'etf_proxy', 'sourceUrl': proxy['sourceUrl'],
                             'proxyId': proxy['id'], 'currency': 'USD',
                             'alignment': 'last_foreign_session_strictly_before_cn_date_max_7_calendar_days',
                             'priceAdjustment': 'close_times_adj_factor',
                             'historyStatus': 'adapter_supported_history_unverified'} for proxy in proxies)
    context['items'].extend({'ts_code': proxy['symbol'], 'aliasKey': 'yf_'+proxy['symbol'].lower(),
                             'name': proxy['name'], 'api': 'yfinance_history', 'provider': 'YAHOO_YFINANCE',
                             'providerApi': 'yfinance_history', 'category': '美国行业ETF', 'scope': 'global',
                             'market': 'US', 'sourceKind': 'etf_proxy', 'sourceUrl': proxy['sourceUrl'],
                             'dataSourceUrl': proxy['dataSourceUrl'], 'proxyId': proxy['id'], 'currency': 'USD',
                             'alignment': 'last_foreign_session_strictly_before_cn_date_max_7_calendar_days',
                             'priceAdjustment': 'yahoo_adj_close_split_dividend',
                             'historyStatus': proxy['historyStatus']} for proxy in proxies)
    context['classificationSources'] = [sources[0], sources[2]]
    return taxonomy, context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sw-table', type=Path, required=True)
    parser.add_argument('--gics-workbook', type=Path, required=True)
    args = parser.parse_args()
    taxonomy, context = build(args.sw_table, args.gics_workbook)
    for path, value in [(ROOT/'engine/atlas_quant/industry_sources.json', taxonomy),
                        (ROOT/'engine/atlas_quant/context_sources.json', context)]:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    # The Portal entry is uploaded as a standalone module; keep its explicit
    # allowlist synchronized without adding unresolved JSON imports there.
    portal = ROOT/'edge/portal-entry.mjs'
    codes = {api: sorted(row['ts_code'] for row in context['items'] if row['api'] == api)
             for api in ('index_daily', 'sw_daily', 'us_daily_adj')}
    contents, count = re.subn(r'^const CONTEXT_CODES=.*?;$',
                             'const CONTEXT_CODES='+json.dumps(codes, separators=(',', ':'))+';',
                             portal.read_text(), flags=re.M)
    if count != 1:
        raise ValueError('Portal context allowlist moved; review before importing')
    portal.write_text(contents)
    print(json.dumps(taxonomy['summary']))


if __name__ == '__main__':
    main()
