"""Independent adversarial review of basket execution and estimation timing."""
import copy
import numpy as np
import pandas as pd
import pytest

import atlas_quant.stat_arb as arb
from atlas_quant.engine import _prepare_data


def minimal_case(sessions=8):
    symbols = ['000001.SZ', '000002.SZ', '600000.SH']
    dates = pd.bdate_range('20250101', periods=sessions).strftime('%Y%m%d').tolist()
    strategy = arb.validate_stat_arb({'schemaVersion': 1, 'research': {'mode': 'stat_arb', 'observationDays': 1},
        'universe': {'symbols': symbols, 'start': dates[0], 'end': dates[-1]}, 'factors': [],
        'statArb': {'method': 'market_residual', 'borrowAnnualBps': 300},
        'portfolio': {'initialCapital': 100000, 'rebalanceDays': 1, 'rebalanceThresholdBps': 0},
        'costs': {'commissionBps': 2.5, 'slippageBps': 3, 'sellTaxBps': 5, 'transferBps': .1, 'minCommission': 5}})
    idx = pd.MultiIndex.from_product([dates, symbols], names=['trade_date', 'ts_code'])
    panel = pd.DataFrame(10., index=idx, columns=['open', 'high', 'low', 'close', 'vol'])
    signals = {'symbols': symbols, 'holdoutIndex': 0, 'targets': {dates[0]: np.array([.5, -.5, 0.])}}
    return strategy, panel, dates, signals


def test_missing_open_leg_does_not_manufacture_flat_target_and_fill_other_leg():
    s, panel, dates, signals = minimal_case()
    panel.loc[(dates[1], signals['symbols'][0]), :] = np.nan
    _, _, trades, ledger, skipped = arb._simulate_baskets(panel, dates, signals, s)
    assert not any(t['date'] == dates[1] for t in trades)
    assert ledger[1]['positions'] == []
    assert any(x['date'] == dates[1] and x['reason'] == 'basket_atomic_wait' for x in skipped)
    fills = [t for t in trades if t['date'] == dates[2]]
    assert {t['symbol'] for t in fills} == set(signals['symbols'][:2])
    assert sum(t['signedQuantity'] * t['price'] for t in fills) == pytest.approx(0)


def test_new_flat_observation_cancels_stale_pending_entry_before_execution_clock():
    s, panel, dates, signals = minimal_case()
    s['portfolio']['rebalanceDays'] = 5
    panel.loc[(dates[1], signals['symbols'][0]), :] = np.nan
    signals['targets'][dates[1]] = np.zeros(3)
    _, _, trades, ledger, _ = arb._simulate_baskets(panel, dates, signals, s)
    assert trades == [], 'An expired entry must not fill after a newer target-flat observation.'
    assert all(row['positions'] == [] for row in ledger)


def test_observation_and_execution_clocks_do_not_require_intersection_dates():
    s, panel, dates, signals = minimal_case(12)
    s['research']['observationDays'] = 2
    s['portfolio']['rebalanceDays'] = 3
    signals['targets'][dates[2]] = np.array([-.5, .5, 0.])
    _, _, trades, _, _ = arb._simulate_baskets(panel, dates, signals, s)
    assert any(t['date'] == dates[4] and t['signalDate'] == dates[2] for t in trades)


def test_short_proceeds_borrow_and_sell_tax_reconcile_against_independent_ledger():
    s, panel, dates, signals = minimal_case()
    s['portfolio']['rebalanceDays'] = 60
    panel.loc[(dates[2], signals['symbols'][0]), 'close'] = 12
    panel.loc[(dates[2], signals['symbols'][1]), 'close'] = 8
    metrics, equity, trades, ledger, _ = arb._simulate_baskets(panel, dates, signals, s)
    cash, positions = s['portfolio']['initialCapital'], {}
    cumulative_fees = {k: 0. for k in ['commission', 'slippage', 'tax', 'transfer', 'borrow']}
    for row in ledger:
        for trade in [t for t in trades if t['date'] == row['date']]:
            notional = trade['quantity'] * trade['price']
            commission = max(notional * .00025, 5)
            slippage = notional * .0003
            tax = notional * .0005 if trade['signedQuantity'] < 0 else 0
            transfer = notional * .00001
            expected = {'commission': commission, 'slippage': slippage, 'tax': tax, 'transfer': transfer}
            for key, value in expected.items():
                assert trade[key] == pytest.approx(value)
                cumulative_fees[key] += value
            cash -= trade['signedQuantity'] * trade['price'] + sum(expected.values())
            positions[trade['symbol']] = positions.get(trade['symbol'], 0) + trade['signedQuantity']
            assert cash == pytest.approx(trade['cashAfter'])
        short_value = sum(-p['value'] for p in row['positions'] if p['quantity'] < 0)
        borrow = short_value * .03 / 252
        assert row['borrowCost'] == pytest.approx(borrow)
        cumulative_fees['borrow'] += borrow
        cash -= borrow
        assert row['cash'] == pytest.approx(cash)
        assert row['equity'] == pytest.approx(cash + sum(p['quantity'] * p['mark'] for p in row['positions']))
        for position in row['positions']:
            assert positions[position['symbol']] == pytest.approx(position['quantity'])
    assert metrics['costBreakdown'] == pytest.approx(cumulative_fees)
    assert metrics['totalCosts'] == pytest.approx(sum(cumulative_fees.values()))
    assert equity[2]['equity'] > equity[1]['equity']
    assert not any(t['date'] == dates[2] for t in trades), 'Daily marks must move even without trading.'


def test_refits_do_not_erase_holding_age_and_suppress_all_time_exits(monkeypatch):
    s, panel, dates, _ = minimal_case(190)
    s['statArb'].update(formationDays=60, residualWindow=20, refitDays=1,
                        maxHoldingDays=2, entryZ=1.5, exitZ=.3, stopZ=4.)
    # Keep observed residuals strictly beyond entry but within stop. Common
    # market projection is unchanged across refits: no basis-change excuse.
    def stationary_extremes(history, projection):
        return np.array([-2., 2., 0.]), np.full(3, 5.), np.full(3, .8), np.zeros(3)
    monkeypatch.setattr(arb, 'residual_statistics', stationary_extremes)
    signals = arb._signals(panel, dates, s)
    nonzero = [row for row in signals['signals'] if row['symbol'] == s['universe']['symbols'][0]]
    assert any(row['state'] == 0 for row in nonzero), 'Refitting every date must not permit an indefinitely open episode.'
    streak = 0
    for row in nonzero:
        streak = streak + 1 if row['state'] else 0
        assert streak <= s['statArb']['maxHoldingDays']


def test_added_factor_baseline_uses_exact_same_observation_and_fit_cutoffs():
    s, panel, dates, _ = minimal_case(420)
    s['statArb'].update(method='pca_residual', components=1, formationDays=60, residualWindow=20)
    s['factors'] = [{'id': 'slow', 'expression': 'returns(close,180)', 'direction': 1}]
    rng = np.random.default_rng(22)
    panel['close'] = 10 * np.exp(np.cumsum(rng.normal(0, .01, (420, 3)), axis=0)).reshape(-1)
    with_factor = arb._signals(panel, dates, s, True)
    baseline = arb._signals(panel, dates, s, False)
    assert with_factor['startIndex'] == baseline['startIndex'] == 181
    assert with_factor['holdoutIndex'] == baseline['holdoutIndex']
    assert list(with_factor['targets']) == list(baseline['targets'])
    cutoffs = lambda x: [(fit['signalDate'], fit['formationStart'], fit['formationEnd']) for fit in x['fits']]
    assert cutoffs(with_factor) == cutoffs(baseline)
    assert all(fit['formationEnd'] < fit['signalDate'] for fit in with_factor['fits'])


def test_refit_cutoff_excludes_even_the_current_signal_close_and_factor_exposure():
    s, panel, dates, _ = minimal_case(230)
    s['statArb'].update(method='pca_residual', components=1, formationDays=60, residualWindow=20, refitDays=20)
    s['factors'] = [{'id': 'price_exposure', 'expression': 'close', 'direction': 1}]
    rng = np.random.default_rng(84)
    panel['close'] = (10 * np.exp(np.cumsum(rng.normal(0, .015, (230, 3)), axis=0))).reshape(-1)
    original = arb._signals(panel, dates, s)
    cut = dates[101]  # start=61, third 20-session refit
    changed = panel.copy()
    for i, symbol in enumerate(s['universe']['symbols']):
        mask = (changed.index.get_level_values('trade_date') >= cut) & (changed.index.get_level_values('ts_code') == symbol)
        changed.loc[mask, 'close'] *= 2 + i
    mutated = arb._signals(changed, dates, s)
    fit = lambda result: next(item for item in result['fits'] if item['signalDate'] == cut)
    assert fit(original) == fit(mutated)
    for date, weights in original['targets'].items():
        if date < cut:
            np.testing.assert_array_equal(weights, mutated['targets'][date])


def test_issued_pca_baskets_are_neutral_to_their_actual_recorded_formation_loadings():
    s, panel, dates, _ = minimal_case(230)
    s['statArb'].update(method='pca_residual', components=1, formationDays=60, residualWindow=20,
                        entryZ=.5, exitZ=.1, stopZ=8, maxHalfLife=252)
    rng = np.random.default_rng(128)
    levels = np.zeros((230, 3))
    for i in range(1, len(levels)):
        levels[i] = .7 * levels[i-1] + rng.normal(0, .02, 3)
    panel['close'] = (10 * np.exp(levels)).reshape(-1)
    signals = arb._signals(panel, dates, s)
    nonzero = 0
    for date, weights in signals['targets'].items():
        active = [f for f in signals['fits'] if f['signalDate'] <= date]
        if not active:
            continue
        B = np.asarray(active[-1]['loadings'])
        np.testing.assert_allclose(B.T @ weights, 0, atol=1e-10)
        assert abs(weights.sum()) < 1e-10
        if np.abs(weights).sum() > 1e-8:
            nonzero += 1
            assert np.abs(weights).sum() == pytest.approx(s['statArb']['grossExposure'])
    assert nonzero > 0


def test_holding_expiry_between_observations_forces_next_open_exit_despite_execution_clock(monkeypatch):
    s, panel, dates, _ = minimal_case(198)
    s['research']['observationDays'] = 10
    s['portfolio']['rebalanceDays'] = 60
    s['statArb'].update(formationDays=60, residualWindow=20, refitDays=20,
                        maxHoldingDays=3, entryZ=1.5, exitZ=.3, stopZ=4.)
    monkeypatch.setattr(arb, 'residual_statistics', lambda h, p: (np.array([-2., 2., 0.]), np.full(3, 5.), np.full(3, .8), np.zeros(3)))
    signals = arb._signals(panel, dates, s)
    first = signals['startIndex']
    expiry = first + 3
    assert dates[expiry] in signals['forceExecutionDates']
    np.testing.assert_allclose(signals['targets'][dates[expiry]], 0)
    assert any(r['date'] == dates[expiry] and r['reason'] == 'time_exit_between_observations' for r in signals['signals'])
    signals['holdoutIndex'] = first
    _, _, trades, ledger, _ = arb._simulate_baskets(panel, dates, signals, s)
    assert any(t['date'] == dates[first + 1] for t in trades)
    assert any(t['date'] == dates[expiry + 1] and t['signalDate'] == dates[expiry] for t in trades)
    assert next(row for row in ledger if row['date'] == dates[expiry + 1])['positions'] == []


def test_holding_expiry_after_final_observation_is_not_lost_and_latest_has_unique_symbols(monkeypatch):
    s, panel, dates, _ = minimal_case(198)
    s['research']['observationDays'] = 10
    s['statArb'].update(formationDays=60, residualWindow=20, refitDays=20,
                        maxHoldingDays=3, entryZ=1.5, exitZ=.3, stopZ=4.)
    monkeypatch.setattr(arb, 'residual_statistics', lambda h, p: (np.array([-2., 0., 0.]), np.full(3, 5.), np.full(3, .8), np.zeros(3)))
    signals = arb._signals(panel, dates, s)
    last_observation = list(range(signals['startIndex'], len(dates), 10))[-1]
    expiry = last_observation + 3
    assert expiry < len(dates) - 1
    assert dates[expiry] in signals['targets'], 'The final unsampled tail still contains observable holding-expiry dates.'
    assert dates[expiry] in signals['forceExecutionDates']
    np.testing.assert_allclose(signals['targets'][dates[expiry]], 0)
    assert {row['symbol'] for row in signals['latest']} == set(s['universe']['symbols'])
    assert len(signals['latest']) == len(s['universe']['symbols'])
    by_symbol = {row['symbol']: row for row in signals['signals']}
    assert signals['latest'] == [by_symbol[symbol] for symbol in signals['symbols']]
