import numpy as np, pandas as pd, zipfile, io, glob, sys, os, json, time
import lightgbm as lgb
from sklearn.tree import DecisionTreeRegressor, _tree
from statistics import NormalDist; ND=NormalDist(); rng=np.random.default_rng(17)
TF=int(sys.argv[1]); HELD='held' in sys.argv; K=1.5; HZ=48; TK=0.0004; t0=time.time()
P='/Users/bailey12/Desktop/PythonProject/edge_data/'
C11=['BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','DOGEUSDT','BNBUSDT','LINKUSDT','AVAXUSDT','LTCUSDT','BCHUSDT','FILUSDT']
C21=C11+['EOSUSDT','ADAUSDT','ETCUSDT','TRXUSDT','XLMUSDT','ATOMUSDT','BATUSDT','DASHUSDT','IOSTUSDT','IOTAUSDT']
SPL=pd.Timestamp('2023-07-01',tz='UTC').value//10**6
def rd(f):
    z=zipfile.ZipFile(f); d=pd.read_csv(io.BytesIO(z.read(z.namelist()[0])),header=None,usecols=range(6)); d=d.apply(pd.to_numeric,errors='coerce'); return d[d[0].notna()]
def bars(s):
    if TF==1:
        z=np.load(P+f'fv2c_v1/cache_{s}.npz'); return z['t'],z['o'],z['h'],z['l'],z['c'],z['v']
    fs=sorted(glob.glob(P+('ml5m/raw/k5-*.zip' if s=='BTCUSDT' else f'ml5m/raw_alt/{s}-k5-*.zip')))
    d=pd.concat([rd(f) for f in fs]).drop_duplicates(0).sort_values(0); t=d[0].astype(np.int64).values; t=np.where(t>1e14,t//1000,t)
    if TF==5: return t,*[d[i].astype(float).values for i in (1,2,3,4,5)]
    g=t//(TF*60000); df=pd.DataFrame({'g':g,'o':d[1].values,'h':d[2].values,'l':d[3].values,'c':d[4].values,'v':d[5].values}).groupby('g').agg(o=('o','first'),h=('h','max'),l=('l','min'),c=('c','last'),v=('v','sum'),n=('o','size'))
    df=df[df.n>=TF//5*0.9]; return df.index.values*TF*60000,df.o.values,df.h.values,df.l.values,df.c.values,df.v.values
def feats(t,o,h,l,c,v):
    s=pd.Series; atr=s(h-l).rolling(14).mean().values; A=np.maximum(atr,1e-12); X={}
    for j in range(3):
        oj,hj,lj,cj=[np.roll(x,j) for x in (o,h,l,c)]; rg=np.maximum(hj-lj,1e-12)
        X[f'b{j}']=(cj-oj)/A; X[f'uw{j}']=(hj-np.maximum(oj,cj))/A; X[f'lw{j}']=(np.minimum(oj,cj)-lj)/A; X[f'rg{j}']=(hj-lj)/A; X[f'cl{j}']=(cj-lj)/rg
    X['engulf']=np.sign(X['b0'])*((np.sign(X['b0'])!=np.sign(X['b1']))&(np.abs(X['b0'])>np.abs(X['b1'])))
    X['c_vs_h1']=(c-np.roll(h,1))/A; X['c_vs_l1']=(c-np.roll(l,1))/A
    X['ups5']=s((c>o).astype(float)).rolling(5).sum().values
    lc=np.log(c)
    for w in (3,12,48): X[f'r{w}']=lc-s(lc).shift(w).values
    day=t//86400000; tp=(h+l+c)/3
    vw=s(tp*v).groupby(day).cumsum().values/np.maximum(s(v).groupby(day).cumsum().values,1e-12)
    X['d_vwap']=(c-vw)/A; X['d_dopen']=(c-s(o).groupby(day).transform('first').values)/A
    for w in (20,50,200): X[f'd_ema{w}']=(c-s(c).ewm(span=w,adjust=False).mean().values)/A
    lv=np.log(v+1); X['vz']=(lv-s(lv).rolling(48).mean().values)/s(lv).rolling(48).std().values
    rg=h-l; X['rz']=(rg-s(rg).rolling(48).mean().values)/s(rg).rolling(48).std().values
    hr=(t//3600000)%24; X['hs']=np.sin(2*np.pi*hr/24); X['hc']=np.cos(2*np.pi*hr/24); X['dow']=(t//86400000+3)%7
    F=np.column_stack(list(X.values())); F[:200]=np.nan; return F,list(X),atr
def labels(h,l,c,atr,side,slip,step):
    N=len(c); idx=np.arange(0,N-HZ-2,step); pnl=np.full(N,np.nan); y=np.zeros(N)
    e=c[idx]; A=atr[idx]; ok=np.isfinite(A)&(A>0)
    fill=(l[idx+1]<e) if side>0 else (h[idx+1]>e); ok&=fill
    tp=e+side*K*A; sl=e-side*K*A
    first_tp=np.full(len(idx),HZ+5); first_sl=np.full(len(idx),HZ+5)
    for k in range(1,HZ+1):
        hk=h[idx+k]; lk=l[idx+k]
        hs=(lk<=sl) if side>0 else (hk>=sl); ht=((hk>tp) if side>0 else (lk<tp)) & (k>=2)
        first_sl=np.where((first_sl>HZ)&hs,k,first_sl); first_tp=np.where((first_tp>HZ)&ht,k,first_tp)
    r=np.where(first_sl<=first_tp, np.where(first_sl<=HZ,-K*A/e-TK-slip,np.nan), K*A/e)
    to=(first_sl>HZ)&(first_tp>HZ); r=np.where(to, side*(c[idx+HZ]/e-1)-TK-slip, r)
    pnl[idx[ok]]=r[ok]; y[idx[ok]]=((first_tp<first_sl)&(first_tp<=HZ))[ok]; return y,pnl
COINS=C11 if TF==1 else C21; step={1:5,5:3}.get(TF,1)
cache=f'data_{TF}m.npz'
if os.path.exists(cache):
    z=np.load(cache,allow_pickle=True); T,Fall,cid,YL,PL,YS,PS=[z[k] for k in ('T','F','cid','YL','PL','YS','PS')]; names=list(z['names'])
else:
    parts=[]
    for k,s in enumerate(COINS):
        t,o,h,l,c,v=bars(s); F,names,atr=feats(t,o,h,l,c,v); sl=0.0005 if s in ('BTCUSDT','ETHUSDT') else 0.001
        yl,pl=labels(h,l,c,atr,1,sl,step); ys,ps=labels(h,l,c,atr,-1,sl,step)
        keep=np.isfinite(pl)|np.isfinite(ps); parts.append((t[keep],F[keep],np.full(keep.sum(),k),yl[keep],pl[keep],ys[keep],ps[keep]))
        print(s,keep.sum(),round(time.time()-t0),'s',flush=True)
    T,Fall,cid,YL,PL,YS,PS=[np.concatenate([p[i] for p in parts]) for i in range(7)]
    np.savez(cache,T=T,F=Fall,cid=cid,YL=YL,PL=PL,YS=YS,PS=PS,names=np.array(names))
q=pd.PeriodIndex(pd.to_datetime(T,unit='ms'),freq='Q').astype(str).values; Qs=sorted(set(q))
first=pd.Period(pd.to_datetime(T.min(),unit='ms'),freq='Q')+4
ev=[x for x in Qs if (x>='2023Q3') == HELD and x>=str(first) and x<='2024Q4']
def evalset(sel_idx,pn_all,tag):
    pn=pn_all[sel_idx]; d=T[sel_idx]//86400000; ud=np.unique(d)
    if len(pn)<10: return dict(n=len(pn))
    g={u:pn[d==u] for u in ud}; bs=[np.mean(np.concatenate([g[u] for u in rng.choice(ud,len(ud))])) for _ in range(1000)]
    cand=np.where(np.isin(q,ev)&np.isfinite(pn_all))[0]; rnd=[np.nanmean(pn_all[rng.choice(cand,len(pn))]) for _ in range(200)]
    qs=pd.Series(pn).groupby(q[sel_idx]).sum()
    return dict(n=int(len(pn)),mean=float(pn.mean()*1e4),ci=[float(x) for x in np.percentile(bs,[5,95])*1e4],rand_p90=float(np.percentile(rnd,90)*1e4),qpos=float((qs>0).mean()),sr=float(pn.mean()/pn.std(ddof=1)),win=float((pn>0).mean()))
def one_pos(sel,ex_bars):
    out=[]; last={}
    for i in sel:  # sel sorted by time within coin implicitly: enforce no overlap per coin
        k=cid[i]
        if T[i]<last.get(k,-1): continue
        out.append(i); last[k]=T[i]+ex_bars*TF*60000
    return np.array(out,dtype=int)
res={}
for side,Y,PN in ((1,YL,PL),(-1,YS,PS)):
    sname='L' if side>0 else 'S'; sc=np.full(len(T),np.nan); thr={}
    for Q in ev:
        st=np.where(q==Q)[0]; tst=T[st].min()
        tr=np.where((T<tst-HZ*TF*60000)&np.isfinite(PN))[0]
        if len(tr)>1500000: tr=np.sort(rng.choice(tr,1500000,replace=False))
        m=lgb.LGBMClassifier(n_estimators=200,num_leaves=31,learning_rate=0.05,min_child_samples=500,subsample=0.7,subsample_freq=1,colsample_bytree=0.8,n_jobs=2,verbose=-1)
        m.fit(Fall[tr],Y[tr]); te=st[np.isfinite(PN[st])]
        sc[te]=m.predict_proba(Fall[te])[:,1]; ptr=m.predict_proba(Fall[tr[-200000:]])[:,1]; thr[Q]={0.01:np.quantile(ptr,0.99),0.02:np.quantile(ptr,0.98)}
        print(TF,sname,Q,len(tr),round(time.time()-t0),'s',flush=True)
    order=np.lexsort((T,cid))
    for qq in (0.01,0.02):
        sel=[i for i in order if np.isfinite(sc[i]) and sc[i]>=thr[q[i]][qq]]; sel=one_pos(sel,HZ)
        res[f'{sname}_top{int(qq*100)}']=evalset(sel,PN,'ml')
    # distilled rule (BUILD only): fit on first half of BUILD rows, choose leaf on second half
    rulef=f'rule_{TF}m_{sname}.json'
    if not HELD:
        b=np.where(np.isfinite(PN)&np.isin(q,ev)&np.isfinite(Fall).all(1))[0]; half=np.median(T[b])
        b1=b[T[b]<half]; b2=b[T[b]>=half]
        if len(b1)>800000: b1=np.sort(rng.choice(b1,800000,replace=False))
        tree=DecisionTreeRegressor(max_depth=3,min_samples_leaf=max(200,int(0.005*len(b1)))).fit(Fall[b1],PN[b1])
        leaves=tree.apply(Fall[b2]); best=max(set(leaves),key=lambda L:PN[b2][leaves==L].mean() if (leaves==L).sum()>=100 else -9)
        t_=tree.tree_; path=[]
        def find(node,conds):
            if t_.children_left[node]==_tree.TREE_LEAF: return conds if node==best else None
            f,th=t_.feature[node],t_.threshold[node]
            return find(t_.children_left[node],conds+[(int(f),'<=',float(th))]) or find(t_.children_right[node],conds+[(int(f),'>',float(th))])
        conds=find(0,[]); json.dump(conds,open(rulef,'w'))
    conds=json.load(open(rulef)); mask=np.isfinite(PN)&np.isin(q,ev)
    for f,op,th in conds: mask&=(Fall[:,f]<=th) if op=='<=' else (Fall[:,f]>th)
    sel=one_pos([i for i in order if mask[i]],HZ)
    r=evalset(sel,PN,'rule'); r['rule']=' AND '.join(f"{names[f]} {op} {th:.3g}" for f,op,th in conds); res[f'{sname}_rule']=r
for k,o in res.items(): print(('HELD ' if HELD else 'BUILD ')+f'{TF}m',k,{a:(round(b,2) if isinstance(b,float) else b) for a,b in o.items()},flush=True)
json.dump(res,open(f'{"held" if HELD else "build"}_{TF}m.json','w'),indent=1)
