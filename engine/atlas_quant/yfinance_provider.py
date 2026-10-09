"""One bounded Yahoo Finance history acquisition with an explicit version contract.

Yahoo data is owner-private research input; the open source adapter grants no
redistribution rights. Old Tushare sources retain their separate identities.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

import pandas as pd

VERSION = '1.7.0'
OPTIONS = {'interval': '1d', 'auto_adjust': False, 'back_adjust': False,
           'repair': False, 'actions': True, 'keepna': True, 'rounding': False}
CACHE = None
MAX_HTTP = 8
MAX_BYTES = 8 * 1024 * 1024


def validate_details(value):
    """Closed archive metadata, without cookies, crumbs, URLs or response bodies."""
    keys = {'provider', 'libraryVersion', 'retrievedAt', 'currency', 'exchangeTimezoneName',
            'instrumentType', 'libraryCalls', 'httpReceipts'}
    if not isinstance(value, dict) or set(value) != keys:
        return False
    if (value['provider'] != 'YAHOO_YFINANCE' or value['libraryVersion'] != VERSION
            or value['currency'] != 'USD' or value['exchangeTimezoneName'] != 'America/New_York'
            or value['instrumentType'] != 'ETF' or type(value['libraryCalls']) is not int or value['libraryCalls'] != 1):
        return False
    try:
        instant = datetime.fromisoformat(value['retrievedAt'].replace('Z', '+00:00'))
        if instant.tzinfo is None:
            return False
    except (ValueError, TypeError, AttributeError):
        return False
    receipts = value['httpReceipts']
    return isinstance(receipts, list) and 1 <= len(receipts) <= MAX_HTTP and all(
        isinstance(row, dict) and set(row) == {'host', 'path', 'status', 'bytes', 'sha256'}
        and row['host'] in {'query1.finance.yahoo.com', 'query2.finance.yahoo.com', 'fc.yahoo.com', 'guce.yahoo.com', 'consent.yahoo.com'}
        and isinstance(row['path'], str) and row['path'].startswith('/') and len(row['path']) <= 160 and '?' not in row['path']
        and type(row['status']) is int and 100 <= row['status'] <= 599
        and type(row['bytes']) is int and 0 <= row['bytes'] <= MAX_BYTES
        and isinstance(row['sha256'], str) and len(row['sha256']) == 64 and all(c in '0123456789abcdef' for c in row['sha256'])
        for row in receipts)


def parse_history(code, frame, metadata, start, end):
    from .provider import ProviderError
    if (metadata.get('symbol') != code or metadata.get('currency') != 'USD'
            or metadata.get('instrumentType') != 'ETF' or metadata.get('exchangeTimezoneName') != 'America/New_York'):
        raise ProviderError('YAHOO_IDENTITY', 'Yahoo ETF 身份、币种或交易时区不符。')
    required = {'Close', 'Adj Close', 'Volume', 'Dividends', 'Stock Splits'}
    if frame.empty or not required.issubset(frame) or not isinstance(frame.index, pd.DatetimeIndex) or str(frame.index.tz) != 'America/New_York':
        raise ProviderError('YAHOO_DATA_MISSING', 'Yahoo 未返回完整的带时区 ETF 复权行情。')
    records = []
    previous = None
    for stamp, row in frame.iterrows():
        date = stamp.strftime('%Y%m%d')
        if not start <= date <= end or previous is not None and date <= previous:
            raise ProviderError('YAHOO_DATE', 'Yahoo 行情日期重复或超出请求范围。')
        previous = date
        out = {'ts_code': code, 'trade_date': date}
        for key, column in [('close','Close'), ('adj_close','Adj Close'), ('vol','Volume'), ('dividends','Dividends'), ('stock_splits','Stock Splits')]:
            value = row[column]
            if pd.isna(value):
                raise ProviderError('YAHOO_DATA_MISSING', 'Yahoo 行情包含缺失数值。')
            if isinstance(value, (str, bool)) or not math.isfinite(float(value)) or float(value) < 0 or key in {'close', 'adj_close'} and float(value) <= 0:
                raise ProviderError('YAHOO_NUMERIC', 'Yahoo 行情数值无效。')
            out[key] = float(value)
        records.append(out)
    return pd.DataFrame(records)


class _FreshHistoryData:
    """Delegate one ticker's cached reads to its real transport.

    yfinance's singleton cache survives sessions and would otherwise return
    an earlier response without this capture's receipts. Do not clear or alter
    the global cache, and do not relabel a cache hit as a new acquisition.
    """
    def __init__(self, data):
        self.data = data

    def cache_get(self, *args, **kwargs):
        return self.data.get(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.data, name)


def history(params, evidence_dir=None):
    """Exactly one library history call; bounded HTTP including bootstrap calls.

    Yahoo end is exclusive. The public adapter contract uses inclusive dates.
    Adjusted Close is retained directly; Volume is shares and amount is absent.
    No info call, repair, fallback provider, or application-level retry.
    """
    global CACHE
    from .provider import ProviderError, parse_date
    import yfinance as yf
    from curl_cffi.requests import Session
    if yf.__version__ != VERSION:
        raise ProviderError('YAHOO_VERSION', 'Yahoo 行情运行库版本与冻结协议不一致。')
    code, start, end = params['ts_code'], params['start_date'], params['end_date']
    parse_date(start); parse_date(end)
    receipts, begun = [], time.monotonic()
    attempts = 0
    evidence = Path(evidence_dir) if evidence_dir else None
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
    class BoundedSession(Session):
        def request(self, method, url, *args, **kwargs):
            nonlocal attempts
            target = urlsplit(url)
            if target.scheme != 'https' or target.hostname not in {'query1.finance.yahoo.com', 'query2.finance.yahoo.com', 'fc.yahoo.com', 'guce.yahoo.com', 'consent.yahoo.com'}:
                raise ProviderError('YAHOO_HTTP_TARGET', 'Yahoo 请求目标不受支持。')
            if attempts >= MAX_HTTP or time.monotonic()-begun > 90:
                raise ProviderError('YAHOO_HTTP_LIMIT', 'Yahoo 请求次数或等待超限。')
            if '/chart/' in target.path and any(r['path'] == target.path and r['status'] >= 400 for r in receipts):
                raise ProviderError('YAHOO_NO_REPLAY', 'Yahoo 失败行情请求不自动重放。')
            kwargs['timeout'] = min(float(kwargs.get('timeout') or 15), 15)
            attempts += 1
            response = super().request(method, url, *args, **kwargs)
            body = response.content
            if len(body) > MAX_BYTES:
                raise ProviderError('YAHOO_HTTP_SIZE', 'Yahoo 响应大小超限。')
            receipt = {'host': target.hostname, 'path': target.path or '/', 'status': response.status_code,
                       'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
            receipts.append(receipt)
            if evidence and '/chart/' in target.path:
                (evidence/f'chart-{len(receipts):02d}.bin').write_bytes(body)
            return response
    if CACHE is None:
        CACHE = tempfile.TemporaryDirectory(prefix='atlas-yahoo-cache-')
        yf.set_tz_cache_location(CACHE.name)
    yf.config.network.retries = 0
    yf.config.debug.hide_exceptions = False
    try:
        with BoundedSession(impersonate='chrome') as session:
            ticker = yf.Ticker(code, session=session)
            ticker._data = _FreshHistoryData(ticker._data)
            frame = ticker.history(start=datetime.strptime(start, '%Y%m%d').strftime('%Y-%m-%d'),
                                   end=(datetime.strptime(end, '%Y%m%d')+timedelta(days=1)).strftime('%Y-%m-%d'),
                                   timeout=15, **OPTIONS)
            # Only cached metadata populated by this history request. Accessing
            # tradingPeriods through the lazy public wrapper triggers more HTTP.
            metadata = ticker._price_history._history_metadata
            result = parse_history(code, frame, metadata, start, end)
        details = {'provider': 'YAHOO_YFINANCE', 'libraryVersion': VERSION,
                   'retrievedAt': datetime.now(timezone.utc).isoformat(),
                   'currency': metadata['currency'], 'exchangeTimezoneName': metadata['exchangeTimezoneName'],
                   'instrumentType': metadata['instrumentType'], 'libraryCalls': 1, 'httpReceipts': receipts}
        if not validate_details(details):
            raise ProviderError('YAHOO_PROVENANCE', 'Yahoo 来源记录无效。')
        return result, details
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('YAHOO_REQUEST_FAILED', 'Yahoo 行情请求未完成；未重试或切换来源。') from None
    finally:
        if evidence:
            import json
            (evidence/'http-receipts.json').write_text(json.dumps(receipts, indent=2)+'\n')
