"""Independent scalar-return identities. Standard library only; never fits F."""
import math


def check_return_row(row, strategy, near, require):
    require(row.get('schema') == 'asset-return-observation/1', 'Missing scalar return record version')
    require(not any(k in row for k in ('entryDate', 'expectedEntry', 'expectedFuture', 'expectedGrossPnl')),
            'Scalar research contains fabricated execution output')
    require(row['featureDate'] == row['date'], 'Feature date mismatch')
    require(row.get('informationCutoff') == row['date'] + '_AFTER_CLOSE',
            'Return information cutoff must be the observation close')
    mode = strategy['research']['returnStudy']['mode']
    start, end = row['responseStartDate'], row['responseEndDate']
    require((mode == 'forecast' and start == row['date']) or
            (mode == 'association' and end == row['date']), 'Return interval/mode mismatch')
    if start is not None and end is not None:
        require(start < end, 'Return interval must increase')
    if row['labelMaturedAt'] is not None:
        require(row['labelMaturedAt'] == end, 'Return maturity is not interval end')
    if row['status'] != 'valid':
        return
    require(row['originPrice'] > 0 and row['responseScale'] > 0, 'Invalid return origin or scale')
    near(row['predictedReturn'], row['predictedResponse'] * row['responseScale'], 'Return inverse scale')
    near(row['conditionalPrice'], row['originPrice'] * (1 + row['predictedReturn']), 'Conditional price inverse')
    if row['observedReturn'] is not None:
        near(row['observedReturn'], row['observedResponse'] * row['responseScale'], 'Observed return scale')
        near(row['responseResidual'], row['observedResponse'] - row['predictedResponse'], 'Response residual')


def check_return_source(rows, strategy, snapshot, near, require):
    """Reconstruct targets/scales using independent price arithmetic from raw frozen rows."""
    prices = {(r['trade_date'], r['ts_code']): r['close'] for r in snapshot['rows']}
    supplied = snapshot.get('provenance', {}).get('tradingDates')
    dates = sorted(set(supplied if supplied else (r['trade_date'] for r in snapshot['rows'])))
    dates = [d for d in dates if strategy['universe']['start'] <= d <= strategy['universe']['end']]
    positions = {d: i for i, d in enumerate(dates)}
    h = strategy['target']['horizonSessions']
    mode = strategy['research']['returnStudy']['mode']
    normalization = strategy['target']['normalization']
    for row in rows:
        i = positions[row['date']]
        start_i, end_i = (i, i + h) if mode == 'forecast' else (i - h, i)
        start = dates[start_i] if start_i >= 0 else None
        end = dates[end_i] if end_i < len(dates) else None
        require(row['responseStartDate'] == start and row['responseEndDate'] == end,
                'Declared h does not match source calendar')
        symbol = row['assetSymbol']
        origin = prices.get((start, symbol))
        future = prices.get((end, symbol))
        if origin is not None and row.get('originPrice') is not None:
            near(row['originPrice'], origin, 'Source origin close')
        if origin is not None and future is not None and row.get('observedReturn') is not None:
            near(row['observedReturn'], future / origin - 1, 'Source close-close return')
        scale = 1.0
        if normalization['kind'] == 'trailing_volatility':
            window = normalization['windowSessions']
            scale = None
            if start_i >= window:
                history = [prices.get((dates[j], symbol)) for j in range(start_i - window, start_i + 1)]
                if all(isinstance(x, (int, float)) and x > 0 and math.isfinite(x) for x in history):
                    returns = [right / left - 1 for left, right in zip(history, history[1:])]
                    mean = sum(returns) / window
                    sigma = math.sqrt(sum((r - mean) ** 2 for r in returns) / (window - 1))
                    if sigma > normalization['minimum']:
                        scale = sigma * math.sqrt(h)
        if row.get('responseScale') is not None:
            require(scale is not None, 'Unavailable volatility was invented')
            near(row['responseScale'], scale, 'Source origin-known volatility scale')
