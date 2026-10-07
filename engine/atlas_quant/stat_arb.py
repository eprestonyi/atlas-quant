"""Causal basket-residual statistical-arbitrage research, not stock-return forecasts.

Each formation window estimates a hedge projection from information available
before the signal. Trades target signed baskets orthogonal to an intercept and
selected common exposures. Short inventory is explicitly hypothetical: these
results do not establish borrow availability or A-share execution feasibility.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from .factors import evaluate_expression, validate_expression

VERSION = '0.3.0'
DEFAULTS = dict(method='pca_residual', formationDays=126, residualWindow=60,
                components=2, refitDays=20, entryZ=2., exitZ=.5, stopZ=4.,
                maxHoldingDays=20, grossExposure=1., shorting='theoretical',
                borrowAnnualBps=300., maxHalfLife=60.)


def validate_stat_arb(strategy):
    from .engine import ResearchError, _number, _date
    def fail(text):
        raise ResearchError('INVALID_STAT_ARB', text)
    if not isinstance(strategy, dict):
        fail('统计套利配置须为对象')
    s = copy.deepcopy(strategy)
    if s.get('schemaVersion', 1) != 1:
        fail('需要 schemaVersion=1')
    for key in ('universe', 'research', 'statArb', 'portfolio', 'costs', 'preprocess'):
        if key in s and not isinstance(s[key], dict):
            fail(key + ' 须为对象')
    u = s.get('universe', {})
    symbols = u.get('symbols', [])
    import re
    if not isinstance(symbols, list) or not 3 <= len(symbols) <= 50 or any(not isinstance(x, str) or not re.fullmatch(r'\d{6}\.(SH|SZ)', x) for x in symbols) or len(set(symbols)) != len(symbols):
        fail('残差篮子研究需要3–50只不重复沪深A股；完整股票池请继续筛选或明确选择研究子集')
    start, end = _date(u.get('start')), _date(u.get('end'))
    if start >= end or (pd.Timestamp(end) - pd.Timestamp(start)).days > 366 * 8:
        fail('起止日期须递增且不超过8年')
    factors = s.setdefault('factors', [])
    if not isinstance(factors, list) or len(factors) > 32:
        fail('最多32个额外共同因子暴露')
    ids = set()
    for f in factors:
        if not isinstance(f, dict) or not isinstance(f.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', f['id']) or f['id'] in ids:
            fail('因子ID无效或重复')
        ids.add(f['id']); validate_expression(f.get('expression'))
        if isinstance(f.get('direction', 1), bool) or f.get('direction', 1) not in (-1, 1):
            fail('因子方向须为1或-1')
        f.setdefault('direction', 1)
    r = s.setdefault('research', {})
    if r.get('mode') != 'stat_arb':
        fail('此引擎仅处理stat_arb研究')
    r['observationDays'] = _number(r.get('observationDays', 1), '观察间隔', 1, 60, True)
    a = {**DEFAULTS, 'components': min(2, len(symbols) - 2), **s.get('statArb', {})}
    if a['method'] not in ('market_residual', 'pca_residual', 'factor_residual'):
        fail('共同成分方法无效')
    if a['shorting'] != 'theoretical':
        fail('尚未核实借券库存，仅支持明确标注的理论多空研究')
    for key, lo, hi, integer in [('formationDays',60,504,True),('residualWindow',20,252,True),('components',1,10,True),('refitDays',1,126,True),('entryZ',.25,6,False),('exitZ',0,5.9,False),('stopZ',.3,10,False),('maxHoldingDays',1,252,True),('grossExposure',.1,2,False),('borrowAnnualBps',0,10000,False),('maxHalfLife',1,252,False)]:
        a[key] = _number(a[key], key, lo, hi, integer)
    if not a['exitZ'] < a['entryZ'] < a['stopZ']:
        fail('需满足退出阈值 < 入场阈值 < 止损阈值')
    if a['residualWindow'] > a['formationDays']:
        fail('残差观察窗不可超过形成窗口')
    if a['method'] == 'pca_residual' and a['components'] > len(symbols) - 2:
        fail('PCA成分数最多为股票数减2，须保留残差自由度')
    if a['method'] == 'factor_residual' and not factors:
        fail('显式因子残差至少需要一个额外因子')
    s['statArb'] = a
    p = s.setdefault('portfolio', {})
    for key, default, lo, hi, integer in [('initialCapital',1e6,10000,1e9,False),('rebalanceDays',1,1,60,True),('rebalanceThresholdBps',25,0,10000,False)]:
        p[key] = _number(p.get(key, default), key, lo, hi, integer)
    c = s.setdefault('costs', {})
    for key, default, hi in [('commissionBps',2.5,100),('slippageBps',3,200),('sellTaxBps',5,100),('transferBps',.1,100),('minCommission',5,1000)]:
        c[key] = _number(c.get(key, default), key, 0, hi)
    pre = s.setdefault('preprocess', {})
    pre.setdefault('decorrelation', 'drop_correlated')
    if pre['decorrelation'] not in ('none', 'drop_correlated'):
        fail('去相关方式无效')
    pre['correlationThreshold'] = _number(pre.get('correlationThreshold', .9), '相关阈值', .5, 1)
    s['schemaVersion'] = 1
    return s


def hedge_projection(formation_returns, exposures, config, preprocess):
    """Return M=I-BB+, with ones in B, so every residual hedge is net zero.

    PCA vectors use past returns only. Added DSL factors are observable stock
    exposures at the formation cutoff, not estimates of tomorrow's return.
    """
    n = formation_returns.shape[1]
    columns, names, dropped = [np.ones(n)], ['dollar_intercept'], []
    explained = []
    if config['method'] == 'pca_residual':
        centered = formation_returns - formation_returns.mean(axis=0)
        _, singular, vt = np.linalg.svd(centered, full_matrices=False)
        denominator = float((singular**2).sum())
        for k in range(config['components']):
            columns.append(vt[k]); names.append('PC' + str(k + 1))
            explained.append(float(singular[k]**2 / denominator) if denominator else 0.)
    for name, raw in exposures.items():
        x = np.asarray(raw, dtype=float)
        if not np.isfinite(x).all() or np.std(x) < 1e-10:
            dropped.append({'id':name,'reason':'missing_or_constant_at_formation'}); continue
        x = (x - x.mean()) / x.std()
        prior = [(label, abs(float(np.corrcoef(x, col)[0, 1]))) for label, col in zip(names, columns) if np.std(col) > 1e-10]
        duplicate = next(((label, corr) for label, corr in prior if np.isfinite(corr) and corr >= preprocess['correlationThreshold']), None)
        if preprocess['decorrelation'] == 'drop_correlated' and duplicate:
            dropped.append({'id':name,'reason':'correlated_formation_exposure','with':duplicate[0],'absoluteCorrelation':duplicate[1]}); continue
        proposed = np.column_stack(columns + [x])
        rank = np.linalg.matrix_rank(proposed)
        if rank >= n:
            dropped.append({'id':name,'reason':'must_preserve_residual_degree_of_freedom'}); continue
        if rank == np.linalg.matrix_rank(np.column_stack(columns)):
            dropped.append({'id':name,'reason':'linearly_dependent_exposure'}); continue
        columns.append(x); names.append(name)
    B = np.column_stack(columns)
    M = np.eye(n) - B @ np.linalg.pinv(B)
    M = (M + M.T) / 2
    M[np.abs(M) < 1e-14] = 0
    return M, B, {'exposures':names,'dropped':dropped,'rank':int(np.linalg.matrix_rank(B)),
                  'residualDegreesOfFreedom':n-int(np.linalg.matrix_rank(B)),
                  'pcaVarianceRatios':explained,'projectionNetError':float(np.max(np.abs(M.sum(axis=0)))),
                  'projectionExposureError':float(np.max(np.abs(B.T @ M)))}


def residual_statistics(history, projection):
    residual_returns = np.asarray(history) @ projection
    levels = np.cumsum(residual_returns, axis=0)
    mean, scale = levels.mean(axis=0), levels.std(axis=0, ddof=1)
    z = np.divide(levels[-1] - mean, scale, out=np.full(len(mean), np.nan), where=scale > 1e-10)
    half, phi = np.full(len(mean), np.nan), np.full(len(mean), np.nan)
    for k in range(levels.shape[1]):
        prev, future = levels[:-1, k], levels[1:, k]
        variance = np.sum((prev-prev.mean())**2)
        if variance > 1e-16:
            slope = float(np.sum((prev-prev.mean())*(future-future.mean())) / variance)
            phi[k] = slope
            if 0 < slope < 1:
                half[k] = -math.log(2) / math.log(slope)
    return z, half, phi, levels[-1]


def _signals(panel, dates, strategy, include_factors=True):
    a = strategy['statArb']; symbols = sorted(strategy['universe']['symbols']); n = len(symbols)
    closes = panel['close'].unstack('ts_code').reindex(index=dates, columns=symbols)
    # No forward-fill and no manufactured returns around a missing session.
    returns = np.log(closes).diff().to_numpy()
    factor_values = {f['id']:evaluate_expression(f['expression'],panel).unstack('ts_code').reindex(index=dates,columns=symbols) * f['direction'] for f in strategy['factors']} if include_factors else {}
    factor_warmup = max((validate_expression(f['expression'])['lookback'] for f in strategy['factors']),default=0)
    start = max(a['formationDays'] + 1, factor_warmup + 1)
    if len(dates)-start < 90:
        from .engine import ResearchError
        raise ResearchError('INSUFFICIENT_STAT_ARB_DATA','形成窗口之后至少需要90个交易日用于开发/独立测试')
    # Cut from known calendar, not from future returns or profitable observations.
    holdout = start + int((len(dates)-start)*.7)
    observations = list(range(start, len(dates), strategy['research']['observationDays']))
    targets, fits, rows, diagnostics = {}, [], [], []
    force_dates=set(); previous_observation=start-1
    state, opened = np.zeros(n), np.full(n,-1)
    active_projection = None; active_B = None; last_fit = -100000; fit_id = None
    total_signals = 0
    for t in observations:
        if active_projection is not None:
            expirations=sorted(set(int(opened[k]+a['maxHoldingDays']) for k in range(n) if state[k] and previous_observation < opened[k]+a['maxHoldingDays'] < t))
            for expiry in expirations:
                for k in range(n):
                    if state[k] and opened[k]+a['maxHoldingDays']<=expiry:
                        state[k]=0
                        rows.append({'date':dates[expiry],'symbol':symbols[k],'zScore':None,'halfLifeSessions':None,'ar1':None,'residualLevel':None,'state':0,'reason':'time_exit_between_observations','modelFitId':fit_id})
                        total_signals+=1
                w=active_projection@state; gross=float(np.abs(w).sum())
                targets[dates[expiry]]=w/gross*a['grossExposure'] if gross>1e-10 else np.zeros(n)
                force_dates.add(dates[expiry])
        previous_observation=t
        if t-last_fit >= a['refitDays'] or active_projection is None:
            train = returns[t-a['formationDays']:t]
            if not np.isfinite(train).all():
                # Remain flat until a complete formation window is available.
                state[:] = 0; active_projection = None
                targets[dates[t]] = np.zeros(n)
                diagnostics.append({'date':dates[t],'reason':'incomplete_formation_window','action':'target_flat'})
                continue
            extra = {name:values.iloc[t-1].to_numpy() for name,values in factor_values.items()}
            M,B,meta=hedge_projection(train,extra,a,strategy['preprocess'])
            active_projection,active_B,last_fit=M,B,t
            # Re-estimation changes hedge exposures, not the age of an open
            # residual position. Preserve entry age so frequent refits cannot
            # evade the declared maximum holding period.
            fit_id=len(fits)
            fits.append({'id':fit_id,'signalDate':dates[t],'formationStart':dates[t-a['formationDays']],'formationEnd':dates[t-1],
                         'symbols':symbols,**meta,'loadings':B.tolist(),
                         'estimation':'past_return_PCA_and_cutoff_stock_exposures','holdoutDataUsedForParameterSelection':False})
        hist=returns[t-a['residualWindow']+1:t+1]
        if not np.isfinite(hist).all():
            state[:]=0; targets[dates[t]]=np.zeros(n)
            diagnostics.append({'date':dates[t],'reason':'incomplete_residual_window','action':'target_flat'});continue
        z,half,phi,level=residual_statistics(hist,active_projection)
        for k,symbol in enumerate(symbols):
            reason='flat'
            valid=np.isfinite(z[k]) and np.isfinite(half[k]) and half[k] <= a['maxHalfLife']
            if state[k]:
                # state=+1 is long the residual basket, opened at negative z.
                if not valid:reason='invalid_mean_reversion';state[k]=0;force_dates.add(dates[t])
                elif abs(z[k])>=a['stopZ']:reason='stop';state[k]=0;force_dates.add(dates[t])
                elif state[k]*z[k]>=-a['exitZ']:reason='converged';state[k]=0;force_dates.add(dates[t])
                elif t-opened[k]>=a['maxHoldingDays']:reason='time_exit';state[k]=0;force_dates.add(dates[t])
                else:reason='hold'
            elif valid and a['entryZ']<=abs(z[k])<a['stopZ']:
                state[k]=-np.sign(z[k]);opened[k]=t;reason='enter'
            elif not valid:reason='mean_reversion_not_established'
            total_signals+=1
            rows.append({'date':dates[t],'symbol':symbol,'zScore':float(z[k]),'halfLifeSessions':float(half[k]),'ar1':float(phi[k]),
                         'residualLevel':float(level[k]),'state':int(state[k]),'reason':reason,'modelFitId':fit_id})
        weights=active_projection@state
        norm=float(np.abs(weights).sum())
        weights=weights/norm*a['grossExposure'] if norm>1e-10 else np.zeros(n)
        targets[dates[t]]=weights
    if active_projection is not None:
        expirations=sorted(set(int(opened[k]+a['maxHoldingDays']) for k in range(n) if state[k] and previous_observation < opened[k]+a['maxHoldingDays'] < len(dates)))
        for expiry in expirations:
            for k in range(n):
                if state[k] and opened[k]+a['maxHoldingDays']<=expiry:
                    state[k]=0;total_signals+=1
                    rows.append({'date':dates[expiry],'symbol':symbols[k],'zScore':None,'halfLifeSessions':None,'ar1':None,'residualLevel':None,'state':0,'reason':'time_exit_between_observations','modelFitId':fit_id})
            w=active_projection@state;gross=float(np.abs(w).sum())
            targets[dates[expiry]]=w/gross*a['grossExposure'] if gross>1e-10 else np.zeros(n)
            force_dates.add(dates[expiry])
    return {'targets':targets,'fits':fits,'signals':rows,'signalCount':total_signals,'diagnostics':diagnostics,
            'holdoutIndex':holdout,'startIndex':start,'symbols':symbols,
            'latest':list({row['symbol']:row for row in rows}.values()),'forceExecutionDates':sorted(force_dates)}


def _simulate_baskets(panel, dates, signal_data, strategy, zero_costs=False):
    from .engine import _trade_costs
    a,p=strategy['statArb'],strategy['portfolio']; c=dict(strategy['costs'])
    if zero_costs:c={k:0. for k in c}
    symbols=signal_data['symbols'];n=len(symbols)
    cash=capital=float(p['initialCapital']);positions=np.zeros(n);marks=np.zeros(n)
    acquired=np.full(n,-1);pending=None;latest_instruction=None;last_instruction=-100000
    trades=[];equity=[];ledger=[];skips=[];peak=capital;turnover=0.;total_cost=0.;bankrupt=False
    fee_totals=dict(commission=0.,slippage=0.,tax=0.,transfer=0.,borrow=0.)
    simulation_start=signal_data['holdoutIndex']
    # Start the holdout flat. No development holdings are silently inherited.
    targets={d:w for d,w in signal_data['targets'].items() if d>=dates[simulation_start]}
    for t in range(simulation_start,len(dates)):
        date=dates[t];day=panel.xs(date,level='trade_date').reindex(symbols)
        op=day.open.to_numpy();cl=day.close.to_numpy();vol=day.vol.to_numpy()
        tradable=np.isfinite(op)&(vol>0)
        marked_open=np.where(np.isfinite(op),op,marks)
        nav_open=cash+float(positions@marked_open)
        fees_today=0.
        prior=dates[t-1] if t else None
        # Every target is from a strictly earlier close; rebalance throttles execution.
        if prior in targets:
            if pending is not None and pending['signalDate'] != prior:
                skips.append({'date':date,'reason':'stale_pending_cancelled','oldSignalDate':pending['signalDate'],'replacementSignalDate':prior})
                pending=None
            latest_instruction={'signalDate':prior,'weights':targets[prior],'forceExit':prior in signal_data.get('forceExecutionDates',[])}
        if latest_instruction is not None and (t-last_instruction>=p['rebalanceDays'] or (latest_instruction.get('forceExit') and latest_instruction['signalDate']==prior) or (np.abs(latest_instruction['weights']).sum()<1e-12 and np.abs(positions).sum()>1e-10)):
            pending=latest_instruction;last_instruction=t
        if pending is not None and not bankrupt:
            target=np.divide(nav_open*pending['weights'],op,out=positions.copy(),where=np.isfinite(op)&(op>0))
            delta=target-positions
            target_weights=pending['weights']
            current_weights=np.divide(positions*marked_open,nav_open,out=np.zeros(n),where=nav_open>0)
            # Basket-level no-trade band: retain hedge as a whole, never drop single legs.
            distance=float(np.abs(target_weights-current_weights).sum())
            close_all=np.abs(target_weights).sum()<1e-12
            if distance<=p['rebalanceThresholdBps']/10000 and not pending.get('forceExit') and not (close_all and np.abs(positions).sum()>1e-10):
                skips.append({'date':date,'reason':'basket_rebalance_band','weightDistance':distance});pending=None
            else:
                needed=(np.abs(delta)>1e-9) | ((np.abs(target_weights-current_weights)>1e-10)&~tradable)
                blocked=np.where(needed&~tradable)[0].tolist()
                locked=[k for k in range(n) if delta[k]<0 and positions[k]>0 and acquired[k]>=t]
                if blocked or locked:
                    skips.append({'date':date,'signalDate':pending['signalDate'],'reason':'basket_atomic_wait','unavailable':[symbols[k] for k in blocked],'tPlusOneLocked':[symbols[k] for k in locked]})
                else:
                    # Signed notional settlement; theoretical shorts release cash but
                    # gross exposure remains bounded by declared target, not cash reuse.
                    order=[k for k in range(n) if needed[k] and delta[k]<0]+[k for k in range(n) if needed[k] and delta[k]>0]
                    for k in order:
                        side='BUY' if delta[k]>0 else 'SELL';quantity=float(abs(delta[k]));notional=quantity*float(op[k])
                        fees=_trade_costs(notional,side,c)
                        before=positions[k]
                        cash-=float(delta[k]*op[k])+fees['cost'];positions[k]=target[k]
                        if positions[k]>0 and positions[k]>max(before,0):acquired[k]=t
                        # One opening batch per date ensures newly acquired long stock
                        # is never sold in the same session, including sign crossings.
                        fees_today+=fees['cost'];total_cost+=fees['cost'];turnover+=notional
                        for key in ('commission','slippage','tax','transfer'):fee_totals[key]+=fees.get(key,0.)
                        trades.append({'date':date,'signalDate':pending['signalDate'],'symbol':symbols[k],'side':side,'quantity':quantity,'signedQuantity':float(delta[k]),'price':float(op[k]),'notional':notional,**fees,'cashAfter':cash,'positionAfter':float(positions[k]),'shortInventory':'hypothetical','settlementRule':'A_SHARE_LONG_T_PLUS_ONE'})
                    pending=None
        marks=np.where(np.isfinite(cl),cl,marks)
        borrow=float(np.maximum(-positions,0)@marks)*(0 if zero_costs else a['borrowAnnualBps'])/10000/252
        cash-=borrow;fees_today+=borrow;total_cost+=borrow;fee_totals['borrow']+=borrow
        nav=cash+float(positions@marks)
        if nav<=0:bankrupt=True
        peak=max(peak,nav);long=float(np.maximum(positions,0)@marks);short=float(np.maximum(-positions,0)@marks)
        eq={'date':date,'equity':nav,'benchmark':capital,'drawdown':nav/peak-1,'cash':cash,'positionsValue':long-short,'dailyCosts':fees_today,'grossExposure':(long+short)/nav if nav>0 else None,'netExposure':(long-short)/nav if nav>0 else None,'borrowCost':borrow}
        equity.append(eq)
        ledger.append({**eq,'staleMarks':[symbols[k] for k in range(n) if not np.isfinite(cl[k]) and abs(positions[k])>1e-10],'pendingBasket':{'signalDate':pending['signalDate'],'targetWeights':pending['weights'].tolist()} if pending else None,'positions':[{'symbol':symbols[k],'quantity':float(q),'mark':float(marks[k]),'value':float(q*marks[k]),'side':'long' if q>0 else 'short'} for k,q in enumerate(positions) if abs(q)>1e-10]})
        if bankrupt:break
    navs=np.array([x['equity'] for x in equity]);rets=navs[1:]/navs[:-1]-1
    std=float(np.std(rets,ddof=1)) if len(rets)>1 else 0
    total=float(navs[-1]/capital-1);ann=float((navs[-1]/capital)**(252/max(1,len(rets)))-1) if navs[-1]>0 else -1.
    metrics=dict(totalReturn=total,annualReturn=ann,volatility=std*np.sqrt(252),sharpe=float(rets.mean()/std*np.sqrt(252)) if std>1e-12 else None,maxDrawdown=min(x['drawdown'] for x in equity),turnover=turnover/max(1,float(navs.mean())),totalCosts=total_cost,costBreakdown=fee_totals,costDragOnInitialCapital=total_cost/capital,tradeCount=len(trades),benchmarkReturn=0.,bankrupt=bankrupt)
    return metrics,equity,trades,ledger,skips


def run_stat_arb(strategy,data,provenance):
    from .engine import ResearchError,_prepare_data,_finite_json
    s=validate_stat_arb(strategy)
    if provenance is not None and not isinstance(provenance,dict):raise ResearchError('INVALID_PROVENANCE','provenance须为对象')
    provenance=copy.deepcopy(provenance or {})
    panel,dates,audit=_prepare_data(data,s,provenance)
    with threadpool_limits(limits=1):
        signal_data=_signals(panel,dates,s)
        metrics,equity,trades,ledger,skips=_simulate_baskets(panel,dates,signal_data,s)
        gross_metrics,*_=_simulate_baskets(panel,dates,signal_data,s,True)
        base=None
        if s['factors']:
            baseline_s=copy.deepcopy(s)
            # The baseline keeps PCA or market common exposures; factor-only
            # extensions compare against the market-intercept residual.
            if baseline_s['statArb']['method']=='factor_residual':baseline_s['statArb']['method']='market_residual'
            baseline=_signals(panel,dates,baseline_s,False)
            # Use identical calendar boundaries, including factor warmup.
            baseline['holdoutIndex']=signal_data['holdoutIndex']
            bm,*_=_simulate_baskets(panel,dates,baseline,baseline_s)
            base={'sameUniverse':True,'sameCosts':True,'sameHoldout':True,'parameterRetuning':False,'baselineMethod':baseline_s['statArb']['method'],'baselineMetrics':bm,'extendedMetrics':metrics,'deltaNetReturn':metrics['totalReturn']-bm['totalReturn'],'deltaSharpe':metrics['sharpe']-bm['sharpe'] if metrics['sharpe'] is not None and bm['sharpe'] is not None else None,'interpretation':'Single predeclared holdout comparison; improvement is not statistical significance or proof of alpha.'}
    hs=dates[signal_data['holdoutIndex']];he=equity[-1]['date'];holdout_rows=[row for row in signal_data['signals'] if row['date']>=hs]
    state_counts={}
    for row in holdout_rows:state_counts[row['reason']]=state_counts.get(row['reason'],0)+1
    fit_hash=hashlib.sha256(json.dumps(signal_data['fits'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    warnings=['THEORETICAL_SHORTS_BORROW_INVENTORY_NOT_VERIFIED','FRACTIONAL_ADJUSTED_RESEARCH_UNITS_NOT_EXCHANGE_LOTS','CURRENT_MEMBERSHIP_SURVIVORSHIP_BIAS','FIXED_FEES_NOT_HISTORICAL_TAX_SCHEDULE','NO_INTRADAY_OR_LIMIT_UP_DOWN_QUEUE_SIMULATION','AR1_HALF_LIFE_IS_NOT_A_COINTEGRATION_TEST','SINGLE_HOLDOUT_NOT_PROOF_OF_ALPHA']
    if provenance.get('synthetic'):warnings.insert(0,'SYNTHETIC_DATA_NOT_MARKET_EVIDENCE')
    if signal_data['diagnostics']:warnings.append('INCOMPLETE_WINDOWS_TARGET_FLAT')
    if s['statArb']['method']=='factor_residual' and all(len(f['exposures'])==1 for f in signal_data['fits']):warnings.append('ALL_ADDED_FACTOR_EXPOSURES_DROPPED')
    result={'schemaVersion':1,'status':'completed','engineVersion':VERSION,'strategy':s,'research':{'mode':'stat_arb','object':'hedged_basket_residual_convergence','singleStockReturnForecast':False,'observationDays':s['research']['observationDays']},
        'provenance':{**provenance,**audit,'strategySha256':hashlib.sha256(json.dumps(s,sort_keys=True,separators=(',',':')).encode()).hexdigest()},
        'metrics':metrics,'equity':equity,'trades':trades,'warnings':warnings,
        'selection':{'winner':s['statArb']['method'],'winnerTrialId':'predeclared:0','params':s['statArb'],'metric':'not_optimized','reason':'研究模板与阈值事前固定；仅滚动更新共同成分，不根据样本外收益挑参数。','candidates':[],'trials':[],'trialCount':1,'holdoutUsedForSelection':False,'qualified':False,'deploymentQualified':False,'evidenceStatus':'UNVALIDATED_THEORETICAL_STAT_ARB','splits':{'development':{'start':dates[signal_data['startIndex']],'end':dates[signal_data['holdoutIndex']-1]},'holdout':{'start':hs,'end':he},'unit':'unique_trade_date','parameterSelection':'none','modelRefits':'rolling_past_data_only'}},
        'statArb':{'method':s['statArb']['method'],'configuration':s['statArb'],'symbols':signal_data['symbols'],'modelFits':signal_data['fits'],'modelFitsSha256':fit_hash,'signals':{'rows':holdout_rows[-5000:],'totalRows':len(holdout_rows),'truncated':len(holdout_rows)>5000,'latest':signal_data['latest'],'stateCounts':state_counts},'evidence':{'convergenceAssumed':False,'halfLifeIsDiagnosticNotProof':True,'cointegrationTestPerformed':False,'shortInventoryVerified':False,'executableInCashOnlyAccount':False,'parametersPredeclared':True,'finalHoldout':{'start':hs,'end':he}},'baselineComparison':base,'costComparison':{'net':metrics,'zeroCostCounterfactual':gross_metrics,'deltaReturn':gross_metrics['totalReturn']-metrics['totalReturn'],'sameSignalSchedule':True},'diagnostics':signal_data['diagnostics'],'exposure':{'targetGross':s['statArb']['grossExposure'],'targetNet':0,'maxAbsoluteRealizedNet':max((abs(x['netExposure']) for x in equity if x['netExposure'] is not None),default=0),'maxRealizedGross':max((x['grossExposure'] for x in equity if x['grossExposure'] is not None),default=0),'neutralityScope':'Dollar and formation exposure neutral at instruction; price drift between rebalances is reported.'}},
        'factors':[{'id':f['id'],'role':'formation_cutoff_hedge_exposure','directionNotStockRanking':True} for f in s['factors']],
        'predictions':None,'trainingDiagnostics':{'period':'rolling_formation_only','modelFits':len(signal_data['fits'])},
        'validation':{'method':'predeclared_rules_rolling_formation_terminal_holdout','futureDataUsed':False,'universeSnapshotIsHistorical':False},
        'execution':{'unit':'fractional_adjusted_research_unit','fillRule':'prior close basket instruction; next available open; atomic all-leg execution','positionCap':'gross target at rebalance, drift reported','costUnit':'basis_points_of_reference_notional_plus_minimum_commission','turnoverDefinition':'gross_traded_notional / mean_daily_equity','benchmarkDefinition':'cash_without_interest','terminalLiquidation':False,'shorting':'theoretical_unverified_inventory','borrowAccrualRule':'short_market_value_at_close * annual_bps / 10000 / 252 per trading session','notModeled':['borrow_inventory','calendar_day_accrual','margin_interest','forced_buyins','board_lots','price_limit_order_queue'],'riskExitsBypassRebalanceBand':True,'settlement':'A_SHARE_LONG_T_PLUS_ONE','ledger':ledger,'skipped':skips}}
    return _finite_json(result)
