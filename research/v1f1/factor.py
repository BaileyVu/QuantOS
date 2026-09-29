"""Causal, continuous-notional economic factor simulation. No exchange effects."""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np

HOUR=3600000

@dataclass
class Series:
    symbol: str
    start: int
    perp: np.ndarray  # open, high, low, close, volume, quote_volume, taker_quote
    index: np.ndarray
    mark: np.ndarray
    funding: np.ndarray  # timestamp, interval_hours, actual signed rate

    def funding_before(self,timestamp):
        return int(np.searchsorted(self.funding[:,0],timestamp,side='right'))-1


def features(s: Series,t: int):
    """t is the decision hour; only fully completed bars before t are visible."""
    if t<720 or t>len(s.perp):return None
    p=s.perp[t-720:t]
    if not np.all(np.isfinite(p)) or np.any(p[:,0:4]<=0):return None
    if not np.all(np.isfinite(s.index[t-25:t])) or not np.all(np.isfinite(s.mark[t-25:t])):return None
    j=s.funding_before(s.start+(t-1)*HOUR)
    if j<0:return None
    event=s.funding[j]
    if s.start+t*HOUR-event[0]>(event[1]+1)*HOUR:return None
    price=p[-1,3];index=s.index[t-1,3]
    quote=float(p[-24:,5].sum())
    if quote<=0:return None
    return np.array([
        (index-price)/price,
        -event[2]*8/event[1],
        price/p[-25,3]-1,
        2*p[-24:,6].sum()/quote-1,
        (index/s.index[t-25,3])/(price/p[-25,3])-1,
        np.std(np.diff(np.log(p[-169:,3])),ddof=1)*np.sqrt(24),
        p[:,5].sum()/30,
    ])


def rank01(values):
    """Average ranks for ties; symbol ordering only resolves selection ties."""
    values=np.asarray(values)
    if not np.all(np.isfinite(values)):raise ValueError('invalid ranking values')
    order=np.argsort(values,kind='stable');ranks=np.empty(len(values));i=0
    while i<len(order):
        j=i+1
        while j<len(order) and values[order[j]]==values[order[i]]:j+=1
        ranks[order[i:j]]=(i+j-1)/2
        i=j
    return ranks/max(1,len(values)-1)


def select(candidates,series,t,variant,legs):
    if variant not in ('basis','composite') or legs not in (1,2):raise ValueError('unregistered structure')
    eligible=[]
    for symbol in candidates:
        f=features(series[symbol],t)
        if f is not None:eligible.append((symbol,f))
        if len(eligible)==25:break
    if len(eligible)<15:return [],len(eligible)
    x=np.asarray([e[1] for e in eligible])
    if variant=='basis':score=rank01(x[:,0])
    else:score=np.column_stack([rank01(x[:,j]) for j in range(4)])@np.array([.5,.2,.2,.1])
    ranked=sorted(range(len(eligible)),key=lambda j:(-score[j],eligible[j][0]))
    long=ranked[:legs];short=ranked[-legs:]
    # A completely flat cross-section has no distinguishable top/bottom signal.
    if min(score[long])<=max(score[short]):return [],len(eligible)
    return [(eligible[j][0],1/(2*legs)) for j in long]+[(eligible[j][0],-1/(2*legs)) for j in short],len(eligible)


def funding_pnl(quantity,rate,mark):
    if not all(math.isfinite(x) for x in (quantity,rate,mark)) or mark<=0:raise ValueError('invalid funding')
    return -quantity*mark*rate


def leg_account(quantity,entry,exit,fee_bps,entry_slip_bps,exit_slip_bps):
    if not all(math.isfinite(x) for x in (quantity,entry,exit,fee_bps,entry_slip_bps,exit_slip_bps)):
        raise ValueError('invalid accounting input')
    if quantity==0 or min(entry,exit)<=0 or min(fee_bps,entry_slip_bps,exit_slip_bps)<0:
        raise ValueError('invalid accounting input')
    sign=1 if quantity>0 else -1
    entry_fill=entry*(1+sign*entry_slip_bps/10000)
    exit_fill=exit*(1-sign*exit_slip_bps/10000)
    fees=abs(quantity)*(entry_fill+exit_fill)*fee_bps/10000
    slip=abs(quantity)*(entry*entry_slip_bps+exit*exit_slip_bps)/10000
    return quantity*(exit-entry),fees,slip,abs(quantity)*(entry+exit)


def simulate(series,portfolio,t,horizon,fee_bps,slip_bps):
    """One coordinated portfolio episode. Quantity is per unit starting equity.

    Signals at t enter at t+1 open. Funding uses actual event timestamps/rates
    and the authoritative mark open of the containing hour as a disclosed proxy.
    A missing required observation closes all legs at the last trustworthy close
    with >=10 bps exit slippage. That conservative retrospective exit convention
    is authorized for this factor layer, not a realizable production fill claim.
    """
    entry=t+1;scheduled=entry+horizon
    if not portfolio:return None
    if abs(sum(abs(w) for _,w in portfolio)-1)>1e-12 or abs(sum(w for _,w in portfolio))>1e-12:
        raise ValueError('gross/net exposure')
    if len({s for s,w in portfolio})!=len(portfolio):raise ValueError('duplicate legs')
    if any(scheduled>=len(series[s].perp) for s,w in portfolio):raise ValueError('outcome outside coverage')
    # Failed coordinated entry: cancel the whole hypothetical basket, no naked leg.
    if any(not np.isfinite(series[s].perp[entry,0]) or not np.isfinite(series[s].mark[entry,0]) for s,w in portfolio):
        return {'entry_failed':True,'data_loss':False,'net':0.,'price':0.,'funding':0.,'fees':0.,'slippage':0.,'turnover':0.,'long':0.,'short':0.,'legs':[],'curve':np.zeros(horizon+1),'max_gross_exposure':0.,'max_abs_net_exposure':0.}
    quantities={s:w/series[s].perp[entry,0] for s,w in portfolio}
    exit_hour=scheduled;forced=False
    for h in range(entry,scheduled+1):
        for symbol,w in portfolio:
            s=series[symbol]
            valid=np.isfinite(s.perp[h]).all() and np.isfinite(s.mark[h]).all()
            j=s.funding_before(s.start+h*HOUR)
            funding_current=j>=0 and s.start+h*HOUR-s.funding[j,0]<=(s.funding[j,1]+1)*HOUR
            if not valid or not funding_current:
                exit_hour=h;forced=True;break
        if forced:break
    # Entry row may be valid at open but invalid for valuation/funding: immediate
    # coordinated stress exit at the known open, never at an unknown future price.
    valuation_end=max(entry,exit_hour)
    legs=[];curve=np.zeros(horizon+1)
    for symbol,w in portfolio:
        s=series[symbol];q=quantities[symbol];entry_price=s.perp[entry,0]
        exit_price=(s.perp[exit_hour-1,3] if forced and exit_hour>entry else s.perp[exit_hour,0])
        p,fee,slip,turnover=leg_account(q,entry_price,exit_price,fee_bps,slip_bps,max(10,slip_bps) if forced else slip_bps)
        funding=0.;fund_by_hour=np.zeros(horizon+1)
        entry_ms=s.start+entry*HOUR
        exit_ms=s.start+exit_hour*HOUR-(1 if forced and exit_hour>entry else 0)
        lo=int(np.searchsorted(s.funding[:,0],entry_ms,side='right'))
        hi=int(np.searchsorted(s.funding[:,0],exit_ms,side='right'))
        for event in s.funding[lo:hi]:
            if entry_ms<event[0]<=exit_ms:
                h=int((event[0]-s.start)//HOUR)
                mark=s.mark[h,0]
                if not np.isfinite(mark):raise ValueError('funding mark missing before forced exit')
                amount=funding_pnl(q,event[2],mark);funding+=amount
                fund_by_hour[min(h-entry,horizon)]+=amount
        net=p+funding-fee-slip
        legs.append({'symbol':symbol,'weight':w,'price':p,'funding':funding,'fees':fee,'slippage':slip,'net':net,'turnover':turnover})
        entry_cost=abs(q)*entry_price*(slip_bps/10000+(1+(1 if q>0 else -1)*slip_bps/10000)*fee_bps/10000)
        cumfund=np.cumsum(fund_by_hour)
        for offset in range(horizon+1):
            h=entry+offset
            if h>=valuation_end:curve[offset]+=net
            else:curve[offset]+=q*(s.mark[h,0]-entry_price)+cumfund[offset]-entry_cost
    result={key:sum(x[key] for x in legs) for key in ('price','funding','fees','slippage','net','turnover')}
    result.update({'long':sum(x['net'] for x in legs if x['weight']>0),'short':sum(x['net'] for x in legs if x['weight']<0),
                   'data_loss':forced,'entry_failed':False,'legs':legs,'curve':curve})
    gross=[];net=[]
    for offset in range(max(0,valuation_end-entry)):
        marked=[quantities[s]*series[s].mark[entry+offset,0] for s,w in portfolio]
        denominator=max(1e-12,1+curve[offset])
        gross.append(sum(abs(x) for x in marked)/denominator)
        net.append(abs(sum(marked))/denominator)
    result['max_gross_exposure']=max(gross,default=1.)
    result['max_abs_net_exposure']=max(net,default=0.)
    return result
