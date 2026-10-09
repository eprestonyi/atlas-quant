"""Exercise the pinned library's real history path with synthetic HTTP only."""
import hashlib
import json
from types import SimpleNamespace

import pandas as pd

from atlas_quant import yfinance_provider as yahoo


def test_repeated_history_has_fresh_response_and_receipts(monkeypatch):
    from curl_cffi.requests import Session
    from yfinance.data import YfData

    dates = pd.date_range('2024-03-11 09:30', periods=3, freq='B', tz='America/New_York')
    generation = 0
    requests = []

    def request(self, method, url, *args, **kwargs):
        # Every HTTP request is intercepted, including the timezone bootstrap.
        # Distinct prices prove the second capture did not reuse cached JSON.
        closes = [100. + generation, 101. + generation, 102. + generation]
        body = json.dumps({'chart': {'error': None, 'result': [{
            'meta': {'symbol': 'XSD', 'currency': 'USD', 'instrumentType': 'ETF',
                     'exchangeTimezoneName': 'America/New_York'},
            'timestamp': [int(stamp.timestamp()) for stamp in dates],
            'indicators': {'quote': [{'open': closes, 'high': closes, 'low': closes,
                                     'close': closes, 'volume': [10, 20, 30]}],
                           'adjclose': [{'adjclose': closes}]},
        }]}}).encode()
        requests.append({'session': id(self), 'body': body})
        return SimpleNamespace(content=body, status_code=200, url=url,
                               text=body.decode(), json=lambda: json.loads(body))

    monkeypatch.setattr(Session, 'request', request)
    monkeypatch.setattr(YfData, '_get_cookie_and_crumb', lambda *a, **kw: ('offline', 'basic'))
    YfData().cache_get.cache_clear()
    params = {'ts_code': 'XSD', 'start_date': '20240311', 'end_date': '20240313'}
    try:
        for generation in (1, 2):
            before = len(requests)
            frame, details = yahoo.history(params)
            captured = requests[before:]
            assert captured, 'each history capture must cross its own HTTP transport'
            assert len(details['httpReceipts']) == len(captured)
            assert [row['sha256'] for row in details['httpReceipts']] == [
                hashlib.sha256(row['body']).hexdigest() for row in captured]
            assert all(row['path'].endswith('/chart/XSD') for row in details['httpReceipts'])
            assert frame['close'].tolist() == [100. + generation, 101. + generation, 102. + generation]
            assert frame['trade_date'].tolist() == ['20240311', '20240312', '20240313']
            assert yahoo.validate_details(details)
    finally:
        YfData().cache_get.cache_clear()
