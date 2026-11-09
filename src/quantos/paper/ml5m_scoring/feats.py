import numpy as np, pandas as pd
sr=lambda x:pd.Series(x)
def feats(t,o,h,l,c,v,n,tb,fr):
    lc=np.log(c); X={}
    for w in (1,3,6,12,48,288): X[f'r{w}']=lc-sr(lc).shift(w).values
    rg=np.maximum(h-l,1e-12); atr=sr(h-l).rolling(48).mean().values
    X['body']=(c-o)/rg; X['uw']=(h-np.maximum(o,c))/rg; X['lw']=(np.minimum(o,c)-l)/rg; X['cl']=(c-l)/rg; X['rg_atr']=(h-l)/atr
    lv=np.log(v+1); X['vz']=(lv-sr(lv).rolling(288).mean().values)/sr(lv).rolling(288).std().values
    X['tbs']=tb/np.maximum(v,1e-9); X['tbs12']=sr(X['tbs']).rolling(12).mean().values
    ln=np.log(n+1); X['nz']=(ln-sr(ln).rolling(288).mean().values)/sr(ln).rolling(288).std().values
    for w in (20,50,200):
        e=sr(c).ewm(span=w,adjust=False).mean().values; X[f'd{w}']=(c-e)/atr
        if w<200: X[f's{w}']=(e-sr(e).shift(6).values)/atr
    X['rv12']=sr(X['r1']).rolling(12).std().values; X['rv288']=sr(X['r1']).rolling(288).std().values
    X['dd']=c/sr(h).rolling(288).max().values-1; X['ru']=c/sr(l).rolling(288).min().values-1
    hr=(t//3600000)%24; dw=(t//86400000+3)%7
    X['hs']=np.sin(2*np.pi*hr/24); X['hc']=np.cos(2*np.pi*hr/24); X['ds']=np.sin(2*np.pi*dw/7); X['dc']=np.cos(2*np.pi*dw/7)
    fi=np.searchsorted(fr.index.values,t,side='right')-1; X['fund']=np.where(fi>=0,fr.values[np.maximum(fi,0)],np.nan)
    X['tofund']=(28800000-(t%28800000))/60000.
    blc=BLC[0]
    b=blc.reindex(t).values
    for w in (12,48,288): X[f'b{w}']=b-blc.reindex(t-w*300000).values
    return np.column_stack(list(X.values()))

BLC=[None]
