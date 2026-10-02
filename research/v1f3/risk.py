"""Risk-only uniform scaling of the unchanged F2 Variant A intent stream."""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from research.v1f1.factor import HOUR
from research.v1f2.carry import observations, fill_cost, valid_open

ANNUAL_HOURS = 365*24
THROTTLES = {'moderate': (1., .75, .5, .25, 0.),
             'defensive': (1., .5, .25, .1, 0.)}


def throttle(drawdown, family):
    if family not in THROTTLES: raise ValueError('unregistered throttle')
    return THROTTLES[family][np.searchsorted([.10,.20,.30,.35],drawdown,side='right')]


def clusters(correlation):
    groups = [{i} for i in range(len(correlation))]
    for i in range(len(correlation)):
        for j in range(i):
            if abs(correlation[i,j]) >= .8:
                left=next(g for g in groups if i in g);right=next(g for g in groups if j in g)
                if left is not right:
                    left.update(right);groups.remove(right)
    return groups


def snapshot(panel, quantities, h, reference_equity):
    """Execution h uses Alpha intent from h-1 and returns closed before h-1."""
    t=h-1
    if not quantities or reference_equity<=0 or t<720:return None
    names=sorted(quantities)
    prices=np.array([panel[s].perp[h,0] for s in names])
    if not np.isfinite(prices).all() or np.any(prices<=0):return None
    weights=np.array([quantities[s] for s in names])*prices/reference_equity
    history=np.array([panel[s].perp[t-720:t,3] for s in names+['BTCUSDT']])
    if not np.isfinite(history).all() or np.any(history<=0):return None
    returns=np.diff(np.log(history),axis=1)
    covariance=np.cov(returns[:-1],ddof=1)*ANNUAL_HOURS
    variance=float(weights@covariance@weights)
    if variance<=0:return None
    vol=np.sqrt(variance)
    std=np.sqrt(np.diag(covariance))
    if np.any(std<=0) or np.var(returns[-1],ddof=1)<=0:return None
    correlation=covariance/np.outer(std,std)
    beta=np.array([np.cov(x,returns[-1],ddof=1)[0,1]/np.var(returns[-1],ddof=1) for x in returns[:-1]])
    rates=[]
    for s in names:
        value=observations(panel[s],t)
        if value is None:return None
        rates.append(value[1])
    carry=-float(weights@np.array(rates))*3*365
    return dict(names=names, weights=weights, covariance=covariance, vol=float(vol),
                contribution=np.abs(weights*(covariance@weights)/vol),
                cluster_gross=max(sum(abs(weights[j]) for j in g) for g in clusters(correlation)),
                beta=float(weights@beta), carry=carry, carry_risk=carry/vol)


def scale_for(state, target, carry_threshold, drawdown, family):
    if state is None or state['carry']<=0 or state['carry_risk']<carry_threshold:return 0.
    w=state['weights']; gross=np.abs(w).sum()
    limits=[1.,target/state['vol'],(.4*target)/max(state['contribution']),
            .25/max(abs(w)),.5/state['cluster_gross'],1/gross]
    for exposure,cap in [(abs(state['beta']),.15),(abs(w.sum()),.10),
                         (w[w>0].sum(),.55),(-w[w<0].sum(),.55)]:
        if exposure>0:limits.append(cap/exposure)
    return max(0.,min(limits))*throttle(drawdown,family)


@dataclass
class Account:
    cash: float = 1.
    q: dict = field(default_factory=dict)
    last: dict = field(default_factory=dict)
    totals: dict = field(default_factory=lambda: dict(price=0.,funding=0.,fees=0.,slippage=0.,long=0.,short=0.))
    fills: list = field(default_factory=list)
    changes: int = 0
    data_loss: int = 0
    opened: dict = field(default_factory=dict)
    durations: list = field(default_factory=list)
    turnover: float = 0.

    def book(self,key,value,q):
        self.totals[key]+=value
        self.totals['long' if q>0 else 'short']+=value if key in ('price','funding') else -value

    def trade(self,target,prices,h,fee,slip,reason):
        changed=False
        capital=max(self.cash,1e-12)
        for s in sorted(set(self.q)|set(target)):
            old,new=self.q.get(s,0.),target.get(s,0.);delta=new-old
            if abs(delta)<=1e-12*max(1.,abs(old),abs(new)):continue
            changed=True;f,c=fill_cost(delta,prices[s],fee,slip)
            close=min(abs(old),abs(delta)) if old*delta<0 else 0.
            fraction=close/abs(delta)
            if fraction:self.book('fees',f*fraction,old);self.book('slippage',c*fraction,old)
            if fraction<1:self.book('fees',f*(1-fraction),new);self.book('slippage',c*(1-fraction),new)
            self.cash-=f+c
            self.turnover+=abs(delta)*prices[s]/capital
            if old and (not new or old*new<0):self.durations.append(h-self.opened.pop(s))
            if new and (not old or old*new<0):self.opened[s]=h
            self.fills.append(dict(hour=h,symbol=s,delta=float(delta),notional=float(abs(delta)*prices[s]),
                                   fees=float(f),slippage=float(c),reason=reason))
        self.q={s:q for s,q in target.items() if q}
        self.last={s:prices[s] for s in self.q}
        self.changes+=int(changed)

    def advance(self,panel,h,fee,slip):
        invalid=any(not valid_open(panel[s],h) for s in self.q)
        prices={s:panel[s].perp[h-1,3] if invalid else panel[s].perp[h,0] for s in self.q}
        if any(not np.isfinite(p) or p<=0 for p in prices.values()):raise ValueError('untrustworthy exit')
        for symbol,q in self.q.items():
            s=panel[symbol];amount=q*(prices[symbol]-self.last[symbol])
            self.cash+=amount;self.book('price',amount,q)
            self.last[symbol]=prices[symbol]
            lo=np.searchsorted(s.funding[:,0],s.start+(h-1)*HOUR,side='right')
            hi=np.searchsorted(s.funding[:,0],s.start+h*HOUR,side='left' if invalid else 'right')
            for stamp,interval,rate in s.funding[lo:hi]:
                mark=s.mark[int((stamp-s.start)//HOUR),0]
                if not np.isfinite(mark):raise ValueError('missing funding mark')
                amount=-q*mark*rate;self.cash+=amount;self.book('funding',amount,q)
        if invalid:
            self.trade({},prices,h,fee,max(10,slip),'data_loss');self.data_loss+=1
        return invalid

    def marked(self,panel,h):
        return self.cash+sum(q*(panel[s].mark[h,0]-panel[s].perp[h,0]) for s,q in self.q.items())


class IntentTape:
    """Expose only reference fills through the requested execution hour."""
    def __init__(self,fills):
        self.fills=fills;self.cursor=0;self.q={};self.hour=-1
        if any(a['hour']>b['hour'] for a,b in zip(fills,fills[1:])):raise ValueError('unsorted Alpha tape')

    def at(self,h):
        if h<self.hour:raise ValueError('Alpha clock reversal')
        self.hour=h
        while self.cursor<len(self.fills) and self.fills[self.cursor]['hour']<=h:
            f=self.fills[self.cursor];s=f['symbol'];self.q[s]=self.q.get(s,0.)+f['delta']
            if abs(self.q[s])<1e-12:self.q.pop(s,None)
            self.cursor+=1
        return self.q.copy()


def run_overlay(panel, references, states, candidate, fee, slip):
    """Same Alpha for every overlay. Risk never feeds back into ranking/state."""
    account=Account();peak=1.;max_dd=0.;locked=False;max_gross=0.;max_net=0.
    folds=[];all_daily=[];all_equity=[];active=0;violations=0;risk_rows=[]
    for year,first,last,reference in references:
        tape=IntentTape(reference['fills']);start_equity=account.cash
        before=account.totals.copy();eq=np.full(last-first+1,start_equity)
        for h in range(first,last):
            lost=account.advance(panel,h,fee,slip)
            marked=account.marked(panel,h);peak=max(peak,marked)
            dd=1-marked/peak;max_dd=max(max_dd,dd)
            if dd>=.35 or marked<=0:locked=True
            intent=tape.at(h)
            force=locked or not intent or h==last-1
            if force:
                account.trade({}, {s:panel[s].perp[h,0] for s in account.q},h,fee,slip,
                              'drawdown_lock' if locked else 'alpha_exit')
            elif h%8==1 and not lost:
                state=states.get(h)
                multiplier=scale_for(state,candidate['vol_target'],candidate['carry_threshold'],dd,candidate['throttle'])
                old_gross=sum(abs(q)*panel[s].perp[h,0] for s,q in account.q.items())
                friction=((1+slip/10000)*fee+slip)/10000
                # Reserve for both all possible delta costs and adverse new basis valuation.
                unit_basis=0. if state is None else sum(w*multiplier*(panel[s].mark[h,0]/panel[s].perp[h,0]-1)
                    for s,w in zip(state['names'],state['weights']))
                budget=max(0.,account.cash-friction*old_gross)/(1+friction+abs(unit_basis))
                target={} if state is None else {s:float(w*multiplier*budget/panel[s].perp[h,0])
                                                 for s,w in zip(state['names'],state['weights']) if multiplier}
                prices={s:panel[s].perp[h,0] for s in set(account.q)|set(target)}
                if any(not valid_open(panel[s],h) for s in target):target={}
                account.trade(target,prices,h,fee,slip,'risk_rebalance')
                # Verify submitted exposure constraints using actual post-cost equity.
                post=account.marked(panel,h)
                effective=multiplier*budget/max(post,1e-12) if target else 0.
                if state is not None and multiplier:
                    w=state['weights']*effective
                    invalid=(effective>1+1e-8 or np.abs(w).sum()>1+1e-8 or max(abs(w))>.25+1e-8
                             or state['cluster_gross']*effective>.5+1e-8 or abs(state['beta'])*effective>.15+1e-8
                             or abs(w.sum())>.10+1e-8 or w[w>0].sum()>.55+1e-8 or -w[w<0].sum()>.55+1e-8
                             or state['vol']*effective>candidate['vol_target']+1e-8
                             or max(state['contribution'])*effective>.4*candidate['vol_target']+1e-8)
                    violations+=int(invalid)
                risk_rows.append(dict(hour=h,scale=multiplier,drawdown=float(dd),locked=locked))
            # Exposure drift triggers immediate proportional reduction, never >1x orders.
            marked=account.marked(panel,h)
            gross=sum(abs(q)*panel[s].mark[h,0] for s,q in account.q.items())
            if gross>max(0.,marked) and account.q:
                friction=((1+slip/10000)*fee+slip)/10000
                perp_gross=sum(abs(q)*panel[s].perp[h,0] for s,q in account.q.items())
                basis=marked-account.cash
                cut=max(0.,account.cash-friction*perp_gross)/(gross-basis-friction*perp_gross)
                cut=min(1.,max(0.,cut))
                account.trade({s:q*cut for s,q in account.q.items()},
                              {s:panel[s].perp[h,0] for s in account.q},h,fee,slip,'gross_emergency')
            marked=account.marked(panel,h)
            if 1-marked/peak>=.35 and account.q:
                locked=True
                account.trade({}, {s:panel[s].perp[h,0] for s in account.q},h,fee,slip,'drawdown_lock')
                marked=account.marked(panel,h)
            eq[h-first]=marked
            peak=max(peak,marked);max_dd=max(max_dd,1-marked/peak)
            if account.q:
                active+=1
                vals=[q*panel[s].mark[h,0] for s,q in account.q.items()]
                max_gross=max(max_gross,sum(abs(v) for v in vals)/max(marked,1e-12))
                max_net=max(max_net,abs(sum(vals))/max(marked,1e-12))
                violations+=int(sum(abs(v) for v in vals)>marked*(1+1e-8))
            if marked<=0:raise ValueError('economic insolvency; no holdout permission')
        eq[-1]=account.cash
        daily=eq[24::24]/eq[:-24:24]-1
        folds.append(dict(year=year,equity=eq/start_equity,daily=daily,
                          components={k:(account.totals[k]-before[k])/start_equity for k in before}))
        all_daily.extend(daily);all_equity.extend(eq[1:])
    return dict(equity=np.asarray(all_equity),daily=np.asarray(all_daily),folds=folds,totals=account.totals,
                fills=account.fills,changes=len({f['hour'] for f in account.fills}),data_loss=account.data_loss,
                holding_hours=account.durations,turnover=account.turnover,
                active_fraction=active/len(all_equity),max_drawdown=max_dd,locked=locked,
                rule_violations=violations,max_gross=max_gross,max_net=max_net,risk_rows=risk_rows)
