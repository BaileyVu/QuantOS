from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import signal
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiohttp

from prediction_market_extensions.live.btc_features import LiveBtcFeatureStore

from .atomic import atomic_json
from .feed_health import FeedHealth, bounded_backoff
from .fees import fee_rate_from_payload, taker_fee
from .ledger import Ledger
from .model import FrozenLogisticModel, PublicBook, calibrate
from .phase2 import label_from_event
from .sizing import AskLevel, plan_market_buy

GAMMA="https://gamma-api.polymarket.com/events/slug/"
CLOB="https://clob.polymarket.com"
POLY_WS="wss://ws-subscriptions-clob.polymarket.com/ws/market"
BINANCE_WS="wss://stream.binance.com:9443/stream?streams=btcusdt@trade/btcusdt@depth20@100ms"
MARKET_SECONDS=300


@dataclass
class Market:
    slug:str; start:int; condition_id:str; up_token:str; down_token:str


class PaperEngine:
    def __init__(self, *, root:Path, runtime:Path, smoke_seconds:float|None=None) -> None:
        self.root=root; self.runtime=runtime; self.runtime.mkdir(parents=True,exist_ok=True)
        self.stop_event=asyncio.Event(); self.resubscribe=asyncio.Event(); self.smoke_seconds=smoke_seconds
        self.model=FrozenLogisticModel.load(root/"vendor/prediction-market-backtesting/live/models/btc_snapshot_model_s199_cost101_daily_stop120_profile.json")
        self.candidate=json.loads((root/"strategy_candidate.json").read_text())
        self.params=self.candidate["parameters"]
        self.ledger=Ledger(runtime/"ledger.sqlite3",experiment="s199-paper",initial_cash="200")
        self.feed=FeedHealth(max_age_ns=8_000_000_000); self.books:dict[str,PublicBook]={}
        self.markets:dict[str,Market]={}; self.token_market:dict[str,str]={}; self.local_sequence:dict[str,int]={}
        self.btc=LiveBtcFeatureStore(buffer_seconds=900,book_prefix="btc")
        self.btc_trade_ns=0; self.binance_book_ns=0; self.evaluated:set[str]=self.ledger.evaluated_slugs()
        self.evaluation_errors:dict[str,str]={}; self.evaluation_retry_ns:dict[str,int]={}
        self.shadow_errors:dict[tuple[str,int],str]={}; self.shadow_retry_ns:dict[tuple[str,int],int]={}
        self.session:aiohttp.ClientSession|None=None; self.started_ns=time.time_ns(); self.diagnostics_count=0
        self.live_checks={"gamma":False,"clob_snapshot":False,"polymarket_ws":False,"binance_ws":False}
        self.snapshot_failures:dict[str,int]={}; self.snapshot_retry_ns:dict[str,int]={}; self.failure_log_ns:dict[str,int]={}
        self.metrics={"book_failures":0,"clob_404":0,"market_prunes":0,"subscription_updates":0,
            "websocket_reconnects":0,"market_evaluations":0,"market_missed":0,"shadow_evaluations":0}

    async def get_json(self,url:str,**params:object)->dict[str,Any]:
        assert self.session
        async with self.session.get(url,params=params) as response:
            response.raise_for_status(); payload=await response.json()
            if not isinstance(payload,dict): raise ValueError(f"unexpected response from {url}")
            return payload

    def diagnostic(self,kind:str,**payload:object)->None:
        record={"utc":datetime.now(UTC).isoformat(),"kind":kind,**payload}
        path=self.runtime/"diagnostics.jsonl"
        with path.open("a",encoding="utf-8") as handle:
            handle.write(json.dumps(record,sort_keys=True,default=str)+"\n"); handle.flush(); os.fsync(handle.fileno())
        self.diagnostics_count+=1

    def status(self,state:str="running",**extra:object)->None:
        p=self.ledger.portfolio()
        now_ns=time.time_ns()
        healthy=sum(self.feed.tradeable((m.up_token,m.down_token),now_ns=now_ns)[0] for m in self.markets.values())
        shadow=self.ledger.shadow_summary()
        atomic_json(self.runtime/"status.json",{"state":state,"pid":os.getpid(),"updated_utc":datetime.now(UTC).isoformat(),
            "candidate_id":self.candidate["candidate_id"],"cash":str(p.cash),"open_cost":str(p.open_cost),
            "realized_pnl":str(p.realized_pnl),"unresolved":p.unresolved,"markets":len(self.markets),
            "active_tokens":len(self.token_market),"healthy_markets":healthy,"live_checks":self.live_checks,
            "metrics":self.metrics,"shadow":{"evaluations":shadow["evaluations"],"settlements":shadow["settlements"]},
            "diagnostics":self.diagnostics_count,**extra})

    @staticmethod
    def _market_payload(event:dict[str,Any])->dict[str,Any]|None:
        markets=event.get("markets")
        if not isinstance(markets,list) or len(markets)!=1 or not isinstance(markets[0],dict): return None
        return markets[0]

    @classmethod
    def _market_status_reason(cls,event:dict[str,Any],market_start:int,now:int)->str|None:
        if now<market_start: return "not_started"
        if now>=market_start+MARKET_SECONDS: return "expired"
        market=cls._market_payload(event)
        if market is None: return "market_unavailable"
        if event.get("closed") is True or market.get("closed") is True: return "closed"
        if event.get("archived") is True or market.get("archived") is True: return "archived"
        if event.get("active") is not True or market.get("active") is not True: return "inactive"
        if market.get("enableOrderBook") is not True or market.get("acceptingOrders") is not True: return "orders_unavailable"
        config=market.get("cryptoMarketConfig")
        if isinstance(config,dict) and (str(config.get("asset","")).lower()!="btc" or str(config.get("duration","")).lower()!="5m"):
            return "wrong_market_type"
        return None

    @classmethod
    def _item_from_event(cls,slug:str,market_start:int,event:dict[str,Any])->Market|None:
        market=cls._market_payload(event)
        if market is None: return None
        tokens=market.get("clobTokenIds")
        if isinstance(tokens,str):
            try: tokens=json.loads(tokens)
            except json.JSONDecodeError: return None
        if not isinstance(tokens,list) or len(tokens)!=2: return None
        return Market(slug,market_start,str(market.get("conditionId") or ""),str(tokens[0]),str(tokens[1]))

    def deactivate_market(self,slug:str,*,reason:str)->bool:
        market=self.markets.pop(slug,None)
        if market is None: return False
        self.finalize_market_windows(market,reason=f"market_unavailable:{reason}")
        tokens=(market.up_token,market.down_token)
        for token in tokens:
            self.token_market.pop(token,None); self.books.pop(token,None); self.local_sequence.pop(token,None)
            self.snapshot_failures.pop(token,None); self.snapshot_retry_ns.pop(token,None); self.failure_log_ns.pop(token,None); self.feed.books.pop(token,None)
        self.metrics["market_prunes"]+=1; self.resubscribe.set()
        self.diagnostic("market_pruned",slug=slug,tokens=tokens,reason=reason)
        return True

    def record_market_result(self,market:Market,result:dict[str,Any],*,seconds_left:int)->None:
        decision=str(result.get("decision") or "skip"); reason=result.get("reason")
        if self.ledger.record_market_evaluation(slug=market.slug,decision=decision,
            reason=None if reason is None else str(reason),seconds_left=seconds_left,
            ts_ns=time.time_ns(),payload=result):
            self.metrics["market_evaluations"]+=1
            if decision=="missed": self.metrics["market_missed"]+=1
            self.diagnostic("market_evaluation_complete",slug=market.slug,seconds_left=seconds_left,
                decision=decision,reason=reason)
        self.evaluated.add(market.slug)

    def finalize_market_windows(self,market:Market,*,reason:str)->None:
        for horizon in (120,60):
            if self.ledger.shadow_policies(slug=market.slug,horizon_seconds=horizon)!={"current","rescue"}:
                self.record_shadow_missed(market,horizon,self.shadow_errors.get((market.slug,horizon),reason))
        if market.slug not in self.evaluated:
            self.record_market_result(market,{"decision":"missed","reason":self.evaluation_errors.get(market.slug,reason)},seconds_left=0)

    def prune_expired(self,now:int)->None:
        for slug,market in list(self.markets.items()):
            if now>=market.start+MARKET_SECONDS or now<market.start:
                self.deactivate_market(slug,reason="expired" if now>=market.start+MARKET_SECONDS else "not_current")

    async def discover_once(self,now:int|None=None)->None:
        now=int(time.time()) if now is None else now; self.prune_expired(now)
        market_start=(now//MARKET_SECONDS)*MARKET_SECONDS; slug=f"btc-updown-5m-{market_start}"
        try:
            event=await self.get_json(GAMMA+slug); self.live_checks["gamma"]=True
            reason=self._market_status_reason(event,market_start,now)
            if reason is not None:
                self.deactivate_market(slug,reason=reason); return
            item=self._item_from_event(slug,market_start,event)
            if item is None: return
            existing=self.markets.get(slug)
            if existing==item: return
            if existing is not None: self.deactivate_market(slug,reason="token_change")
            self.markets[slug]=item
            for token in (item.up_token,item.down_token): self.token_market[token]=slug
            self.resubscribe.set(); self.diagnostic("market_discovered",slug=slug,tokens=[item.up_token,item.down_token])
        except Exception as exc:
            self.diagnostic("discovery_retry",slug=slug,error=f"{type(exc).__name__}:{exc}")

    async def discover(self)->None:
        while not self.stop_event.is_set():
            await self.discover_once(); await self._wait(2)

    async def snapshot_token(self,token:str)->None:
        payload=await self.get_json(CLOB+"/book",token_id=token)
        book=self.books.setdefault(token,PublicBook.empty(token)); book.snapshot(payload)
        features=book.features(); self.feed.connect(token); self.local_sequence[token]=0
        self.feed.snapshot(token,sequence=0,ts_ns=time.time_ns(),bid=features["bid"],ask=features["ask"],
            bid_size=features["bid_size"],ask_size=features["ask_size"],depth=len(book.bids)+len(book.asks))
        self.live_checks["clob_snapshot"]=True; self.snapshot_failures.pop(token,None); self.snapshot_retry_ns.pop(token,None)

    def _mark_book_failure(self,token:str,kind:str,exc:Exception,*,advance_retry:bool=True)->None:
        if token in self.feed.books: self.feed.disconnect(token)
        now=time.time_ns(); last=self.failure_log_ns.get(token,0)
        if advance_retry or token not in self.snapshot_failures:
            attempt=self.snapshot_failures.get(token,0); self.snapshot_failures[token]=attempt+1
            delay=bounded_backoff(attempt,base=2,cap=30)
            self.snapshot_retry_ns[token]=now+int(delay*1_000_000_000)
        else:
            delay=bounded_backoff(max(0,self.snapshot_failures.get(token,1)-1),base=2,cap=30)
        if now-last<30_000_000_000:
            return
        self.failure_log_ns[token]=now; self.metrics["book_failures"]+=1
        self.diagnostic(kind,token_id=token,retry_seconds=delay,error=f"{type(exc).__name__}:{exc}")

    async def _handle_token_404(self,token:str,exc:Exception)->None:
        self.metrics["clob_404"]+=1; slug=self.token_market.get(token)
        if slug is None: return
        market=self.markets.get(slug); reason="clob_404_unavailable"; status_error=None
        if market is not None:
            try:
                event=await self.get_json(GAMMA+slug); self.live_checks["gamma"]=True
                reason=self._market_status_reason(event,market.start,int(time.time())) or "clob_404_unavailable"
                item=self._item_from_event(slug,market.start,event)
                if item is None or token not in (item.up_token,item.down_token): reason="token_unavailable"
            except Exception as status_exc:
                status_error=f"{type(status_exc).__name__}:{status_exc}"; reason="status_unavailable_after_clob_404"
        self.diagnostic("book_token_unavailable",token_id=token,slug=slug,reason=reason,
            status_error=status_error,error=f"{type(exc).__name__}:{exc}")
        self.deactivate_market(slug,reason=reason)

    async def snapshot_isolated(self,token:str,*,source:str)->bool:
        if token not in self.token_market: return False
        try:
            await self.snapshot_token(token); return True
        except asyncio.CancelledError: raise
        except aiohttp.ClientResponseError as exc:
            if exc.status==404: await self._handle_token_404(token,exc)
            else: self._mark_book_failure(token,"book_snapshot_failed",exc)
        except (ValueError,KeyError,TypeError) as exc:
            self._mark_book_failure(token,"book_snapshot_invalid",exc)
        except Exception as exc:
            self._mark_book_failure(token,"book_snapshot_failed",exc)
        return False

    def _health_update(self,token:str)->None:
        book=self.books[token]; features=book.features(); seq=self.local_sequence.get(token,0)+1; self.local_sequence[token]=seq
        self.feed.delta(token,sequence=seq,ts_ns=time.time_ns(),bid=features["bid"],ask=features["ask"],
            bid_size=features["bid_size"],ask_size=features["ask_size"],depth=len(book.bids)+len(book.asks))

    def apply_polymarket_event(self,event:dict[str,Any])->None:
        kind=event.get("event_type")
        if kind=="book":
            token=str(event.get("asset_id") or "")
            if token not in self.token_market: return
            try:
                book=self.books.setdefault(token,PublicBook.empty(token)); book.snapshot(event); f=book.features()
                self.feed.connect(token); self.local_sequence[token]=0
                self.feed.snapshot(token,sequence=0,ts_ns=time.time_ns(),bid=f["bid"],ask=f["ask"],
                    bid_size=f["bid_size"],ask_size=f["ask_size"],depth=len(book.bids)+len(book.asks))
                self.snapshot_failures.pop(token,None); self.snapshot_retry_ns.pop(token,None)
            except (ValueError,KeyError,TypeError) as exc:
                self._mark_book_failure(token,"book_event_invalid",exc,advance_retry=False)
        elif kind=="price_change":
            source_ts=int(event.get("timestamp") or 0)
            changes=event.get("price_changes",[])
            if not isinstance(changes,list): return
            for change in changes:
                if not isinstance(change,dict): continue
                token=str(change.get("asset_id") or "")
                if token not in self.token_market or token not in self.books: continue
                try:
                    if source_ts and source_ts<self.books[token].source_ts_ms: continue
                    self.books[token].change(side=str(change.get("side")),price=change.get("price"),size=change.get("size"),source_ts_ms=source_ts)
                    self._health_update(token)
                except (ValueError,KeyError,TypeError) as exc:
                    self._mark_book_failure(token,"book_event_invalid",exc,advance_retry=False)

    async def sync_ws_subscriptions(self,ws:Any,subscribed:set[str])->set[str]:
        self.resubscribe.clear(); desired=set(self.token_market)
        removed=sorted(subscribed-desired); added=sorted(desired-subscribed)
        if removed:
            await ws.send_json({"assets_ids":removed,"operation":"unsubscribe"}); subscribed.difference_update(removed)
        if added:
            await ws.send_json({"assets_ids":added,"operation":"subscribe","custom_feature_enabled":True}); subscribed.update(added)
        if removed or added:
            self.metrics["subscription_updates"]+=1
            self.diagnostic("polymarket_subscription_update",added=added,removed=removed,subscriptions=len(subscribed))
        for token in added: await self.snapshot_isolated(token,source="subscription")
        unavailable=sorted(subscribed-set(self.token_market))
        if unavailable:
            await ws.send_json({"assets_ids":unavailable,"operation":"unsubscribe"}); subscribed.difference_update(unavailable)
            self.metrics["subscription_updates"]+=1
            self.diagnostic("polymarket_subscription_update",added=[],removed=unavailable,subscriptions=len(subscribed))
        if set(self.token_market)!=subscribed: self.resubscribe.set()
        return subscribed

    def mark_websocket_failure(self,tokens:set[str],attempt:int,exc:Exception)->None:
        self.live_checks["polymarket_ws"]=False; self.metrics["websocket_reconnects"]+=1
        for token in tokens:
            if token in self.token_market and token in self.feed.books: self.feed.disconnect(token)
        self.diagnostic("polymarket_reconnect",attempt=attempt,error=f"{type(exc).__name__}:{exc}")

    async def polymarket_feed(self)->None:
        attempt=0
        while not self.stop_event.is_set():
            tokens=sorted(self.token_market)
            if not tokens: await self._wait(1); continue
            subscribed:set[str]=set()
            try:
                assert self.session
                async with self.session.ws_connect(POLY_WS,heartbeat=10,receive_timeout=20) as ws:
                    await ws.send_json({"type":"market","assets_ids":tokens,"custom_feature_enabled":True})
                    subscribed=set(tokens); self.resubscribe.clear(); attempt=0; self.live_checks["polymarket_ws"]=True
                    self.diagnostic("polymarket_connected",subscriptions=len(subscribed))
                    for token in tokens: await self.snapshot_isolated(token,source="connect")
                    if set(self.token_market)!=subscribed: self.resubscribe.set()
                    while not self.stop_event.is_set():
                        if self.resubscribe.is_set(): subscribed=await self.sync_ws_subscriptions(ws,subscribed)
                        try: message=await asyncio.wait_for(ws.receive(),timeout=1)
                        except TimeoutError: continue
                        if message.type in {aiohttp.WSMsgType.CLOSED,aiohttp.WSMsgType.ERROR}: raise ConnectionError("Polymarket websocket closed")
                        if message.type!=aiohttp.WSMsgType.TEXT or message.data in {"PONG",""}: continue
                        try: payload=json.loads(message.data)
                        except (json.JSONDecodeError,TypeError) as exc:
                            self.diagnostic("polymarket_message_invalid",error=f"{type(exc).__name__}:{exc}"); continue
                        events=payload if isinstance(payload,list) else [payload]
                        for event in events:
                            if isinstance(event,dict): self.apply_polymarket_event(event)
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.mark_websocket_failure(subscribed or set(tokens),attempt,exc)
                await self._wait(bounded_backoff(attempt)); attempt+=1

    async def binance_feed(self)->None:
        attempt=0
        while not self.stop_event.is_set():
            try:
                assert self.session
                async with self.session.ws_connect(BINANCE_WS,heartbeat=10,receive_timeout=20) as ws:
                    attempt=0; self.live_checks["binance_ws"]=True; self.diagnostic("binance_connected")
                    async for message in ws:
                        if self.stop_event.is_set(): break
                        if message.type!=aiohttp.WSMsgType.TEXT: continue
                        payload=json.loads(message.data); stream=str(payload.get("stream","")); data=payload.get("data",{})
                        if stream.endswith("@trade"):
                            ts_ns=int(data["T"])*1_000_000; self.btc.record_trade(ts_ns=ts_ns,price=float(data["p"]),size=float(data["q"])); self.btc_trade_ns=ts_ns
                        elif "@depth" in stream:
                            bids=data.get("bids",[]); asks=data.get("asks",[])
                            if bids and asks:
                                bid=float(bids[0][0]); ask=float(asks[0][0]); bid_size=float(bids[0][1]); ask_size=float(asks[0][1])
                                bid_depth=sum(float(x[1]) for x in bids[:5]); ask_depth=sum(float(x[1]) for x in asks[:5]); total=bid_depth+ask_depth
                                ts_ns=int(data.get("E",int(time.time()*1000)))*1_000_000
                                self.btc.record_book(ts_ns=ts_ns,mid=(bid+ask)/2,spread=ask-bid,bid_size=bid_size,ask_size=ask_size,
                                    bid_depth=bid_depth,ask_depth=ask_depth,book_imbalance=(bid_depth-ask_depth)/total,microprice=(ask*bid_size+bid*ask_size)/(bid_size+ask_size)); self.binance_book_ns=ts_ns
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.diagnostic("binance_reconnect",attempt=attempt,error=f"{type(exc).__name__}:{exc}"); await self._wait(bounded_backoff(attempt)); attempt+=1

    async def refresh_stale_books(self)->None:
        """Recover quiet, stale, or faulted subscriptions from public REST snapshots."""
        while not self.stop_event.is_set():
            now=time.time_ns()
            for token in list(self.token_market):
                if self.snapshot_retry_ns.get(token,0)>now: continue
                state=self.feed.books.get(token)
                if state is not None and state.snapshot and state.fault is None and now-state.updated_ns<=6_000_000_000:
                    continue
                if await self.snapshot_isolated(token,source="refresh"):
                    self.diagnostic("book_resnapshot",token_id=token,reason="stale_or_faulted")
            await self._wait(2)

    def build_row(self,market:Market,now:int,*,seconds_left:int=60)->dict[str,float]:
        up=self.books[market.up_token].features(); down=self.books[market.down_token].features()
        current=self.btc.price_at(now); start=self.btc.price_at(market.start)
        if not math.isfinite(current) or not math.isfinite(start): raise ValueError("missing causal BTC start/current price")
        row={"seconds_left":float(seconds_left),"price_diff":current-start,"return_since_start":current/start-1,
            "momentum_15s":self.btc.momentum(now,15),"momentum_30s":self.btc.momentum(now,30),"momentum_60s":self.btc.momentum(now,60),
            "volatility_60s":self.btc.volatility(now,60),"volume_60s":self.btc.volume(now,60),
            "yes_mid":up["mid"],"yes_spread":up["spread"],"yes_bid_size":up["bid_size"],"yes_ask_size":up["ask_size"],
            "yes_book_imbalance":up["book_imbalance"],"yes_microprice":up["microprice"],
            "no_mid":down["mid"],"no_spread":down["spread"],"no_bid_size":down["bid_size"],"no_ask_size":down["ask_size"],
            "no_book_imbalance":down["book_imbalance"],"no_microprice":down["microprice"],"yes_no_ask_cost":up["ask"]+down["ask"]}
        if not all(math.isfinite(float(row[c])) for c in self.model.columns): raise ValueError("non-finite model features")
        return row

    def momentum_ok(self,row:dict[str,float],selected:str)->bool:
        mode=str(self.params["momentum"]); sign=1 if selected=="up" else -1
        values=(sign*row["momentum_15s"],sign*row["momentum_30s"])
        return mode=="none" or (mode=="aligned" and min(values)>=0) or (mode=="reject_both_opposed" and not max(values)<0)

    async def evaluation_context(self,market:Market,*,seconds_left:int)->dict[str,Any]:
        now_ns=time.time_ns(); ok,reason=self.feed.tradeable((market.up_token,market.down_token),now_ns=now_ns)
        if not ok: raise ValueError(f"feed_unhealthy:{reason}")
        if now_ns-self.btc_trade_ns>30_000_000_000: raise ValueError("stale_binance_trade")
        row=self.build_row(market,now_ns//1_000_000_000,seconds_left=seconds_left); raw=self.model.predict(row); p=calibrate(raw,self.params["calibration"])
        asks={"up":row["yes_mid"]+row["yes_spread"]/2,"down":row["no_mid"]+row["no_spread"]/2}
        selected=max(("up","down"),key=lambda x:(p if x=="up" else 1-p)-asks[x]); selected_p=p if selected=="up" else 1-p
        token=market.up_token if selected=="up" else market.down_token; book=self.books[token]
        fee_url=CLOB+"/fee-rate"; fee=fee_rate_from_payload(token,await self.get_json(fee_url,token_id=token),source_url=fee_url+"?token_id="+token)
        ask=asks[selected]; unit_fee=float(taker_fee(shares="1",price=str(ask),metadata=fee)); net_edge=selected_p-ask-unit_fee
        return {"now_ns":now_ns,"row":row,"raw":raw,"prob_up":p,"asks":asks,"selected":selected,
            "selected_probability":selected_p,"token":token,"book":book,"fee":fee,"ask":ask,
            "unit_fee":unit_fee,"net_edge":net_edge}

    def shadow_policy_decision(self,context:dict[str,Any],policy:str)->dict[str,Any]:
        row=context["row"]; ask=float(context["ask"]); selected=str(context["selected"])
        selected_p=float(context["selected_probability"]); net_edge=float(context["net_edge"]); book=context["book"]
        if policy=="current":
            min_ask=float(self.params["min_ask"]); max_ask=float(self.params["max_ask"])
            pair_limit=float(self.params["max_pair_ask_cost"]); target=Decimal("2"); require_five=False
        elif policy=="rescue":
            min_ask=.10; max_ask=.85; pair_limit=1.02
            target=max(Decimal("5"),book.min_order_size); require_five=True
        else: raise ValueError(f"unknown shadow policy: {policy}")
        reject=None
        if not min_ask<=ask<=max_ask: reject="ask_range"
        elif selected_p<.65: reject="probability"
        elif float(row["yes_no_ask_cost"])>pair_limit: reject="pair_cost"
        elif net_edge<.02: reject="net_edge"
        elif policy=="current" and not self.momentum_ok(row,selected): reject="momentum"
        best=book.best_ask(); visible=Decimal("0") if best is None else best[1]
        if reject is None and require_five and visible<Decimal("5"): reject="visible_liquidity"
        plan=None
        if reject is None:
            plan=plan_market_buy(target_shares=target,asks=[AskLevel(price,size) for price,size in book.ask_levels()],
                available_cash="200",minimum_shares=book.min_order_size,quantity_increment=book.tick_size,
                fee_metadata=context["fee"],allow_partial=policy=="current")
            if plan.executable_shares<=0 or plan.average_price is None: reject=plan.reason or "not_executable"
            elif plan.average_price-Decimal(str(ask))>Decimal("0.02"): reject="slippage"
            elif plan.cash_debit>Decimal("6"): reject="aggregate_open_risk"
        return {"decision":"skip" if reject else "enter","reason":reject,"selected":selected,
            "shares":None if plan is None or plan.executable_shares<=0 else plan.executable_shares,
            "price":None if plan is None else plan.average_price,"fee":None if plan is None else plan.fee,
            "cash_debit":None if plan is None else plan.cash_debit,"prob_up":float(context["prob_up"]),
            "net_edge":net_edge,"pair_cost":float(row["yes_no_ask_cost"]),"ask":ask,
            "selected_probability":selected_p,"visible_ask_size":str(visible),"venue_minimum":str(book.min_order_size)}

    async def evaluate_shadow_horizon(self,market:Market,horizon:int)->dict[str,dict[str,Any]]:
        context=await self.evaluation_context(market,seconds_left=horizon); results={}
        for policy in ("current","rescue"):
            result=self.shadow_policy_decision(context,policy); results[policy]=result
            if self.ledger.record_shadow(slug=market.slug,horizon_seconds=horizon,policy=policy,
                decision=str(result["decision"]),reason=result["reason"],selected=str(result["selected"]),
                shares=result["shares"],price=result["price"],fee=result["fee"],cash_debit=result["cash_debit"],
                prob_up=float(result["prob_up"]),net_edge=float(result["net_edge"]),pair_cost=float(result["pair_cost"]),
                ts_ns=int(context["now_ns"]),payload={"raw_prob_up":context["raw"],"selected_probability":result["selected_probability"],
                    "ask":result["ask"],"visible_ask_size":result["visible_ask_size"],"venue_minimum":result["venue_minimum"]}):
                self.metrics["shadow_evaluations"]+=1
        self.diagnostic("shadow_evaluation",slug=market.slug,horizon_seconds=horizon,
            policies={key:{"decision":value["decision"],"reason":value["reason"]} for key,value in results.items()})
        return results

    def record_shadow_missed(self,market:Market,horizon:int,reason:str)->None:
        inserted=0
        for policy in ("current","rescue"):
            if self.ledger.record_shadow(slug=market.slug,horizon_seconds=horizon,policy=policy,
                decision="missed",reason=reason,selected=None,shares=None,price=None,fee=None,cash_debit=None,
                prob_up=None,net_edge=None,pair_cost=None,ts_ns=time.time_ns(),payload={}): inserted+=1
        if inserted:
            self.metrics["shadow_evaluations"]+=inserted
            self.diagnostic("shadow_evaluation",slug=market.slug,horizon_seconds=horizon,
                policies={"current":{"decision":"missed","reason":reason},"rescue":{"decision":"missed","reason":reason}})

    async def evaluate_market(self,market:Market)->dict[str,Any]:
        context=await self.evaluation_context(market,seconds_left=60); row=context["row"]; p=float(context["prob_up"])
        raw=float(context["raw"]); selected=str(context["selected"]); selected_p=float(context["selected_probability"])
        token=str(context["token"]); book=context["book"]; fee=context["fee"]; ask=float(context["ask"]); net_edge=float(context["net_edge"])
        reject=None
        if ask<float(self.params["min_ask"]) or ask>float(self.params["max_ask"]): reject="ask_range"
        elif selected_p<float(self.params["min_probability"]): reject="probability"
        elif row["yes_no_ask_cost"]>float(self.params["max_pair_ask_cost"]): reject="pair_cost"
        elif net_edge<float(self.params["min_net_edge"]): reject="net_edge"
        elif not self.momentum_ok(row,selected): reject="momentum"
        if reject:
            result={"decision":"skip","reason":reject,"selected":selected,"prob_up":p,"raw_prob_up":raw,"net_edge":net_edge}
            self.diagnostic("evaluation",slug=market.slug,**result,**row); return result
        levels=[AskLevel(price,size) for price,size in book.ask_levels()]
        plan=plan_market_buy(target_shares="2",asks=levels,available_cash=self.ledger.portfolio().cash,
            minimum_shares=book.min_order_size,quantity_increment=book.tick_size,fee_metadata=fee,allow_partial=True)
        if plan.executable_shares<=0 or plan.average_price is None:
            result={"decision":"skip","reason":plan.reason,"prob_up":p,"selected":selected}
            self.diagnostic("evaluation",slug=market.slug,**result); return result
        if plan.average_price-Decimal(str(ask))>Decimal("0.02"):
            result={"decision":"skip","reason":"slippage","average":str(plan.average_price),"ask":ask}
            self.diagnostic("evaluation",slug=market.slug,**result); return result
        day=datetime.now(UTC).date().isoformat(); risk_pnl=self.ledger.daily_risk_pnl(day_utc=day)
        if risk_pnl<=Decimal("-1.2"):
            result={"decision":"skip","reason":"daily_loss_stop","risk_pnl":str(risk_pnl)}
            self.diagnostic("evaluation",slug=market.slug,**result); return result
        allowed,risk_reason=self.ledger.can_enter(proposed_debit=plan.cash_debit,max_open_risk="6")
        if not allowed:
            result={"decision":"skip","reason":risk_reason}
            self.diagnostic("evaluation",slug=market.slug,**result); return result
        fill_id="F-"+hashlib.sha256((self.candidate["candidate_id"]+market.slug).encode()).hexdigest()[:24]
        inserted=self.ledger.record_fill(fill_id=fill_id,event_id="E-"+fill_id[2:],instrument_id=token,slug=market.slug,
            outcome=selected,shares=plan.executable_shares,price=plan.average_price,fee_metadata=fee,ts_ns=int(context["now_ns"]))
        result={"decision":"fill" if inserted else "duplicate","reason":None,"fill_id":fill_id,"selected":selected,
            "shares":str(plan.executable_shares),"price":str(plan.average_price),"fee":str(plan.fee),
            "cash_debit":str(plan.cash_debit),"prob_up":p,"net_edge":net_edge}
        self.diagnostic("evaluation",slug=market.slug,**result); return result

    async def evaluator(self)->None:
        while not self.stop_event.is_set():
            now=int(time.time())
            for slug,market in list(self.markets.items()):
                left=market.start+300-now
                for horizon in (120,60):
                    key=(slug,horizon)
                    if self.ledger.shadow_policies(slug=slug,horizon_seconds=horizon)=={"current","rescue"}: continue
                    if horizon-10<=left<=horizon and time.time_ns()>=self.shadow_retry_ns.get(key,0):
                        try:
                            await self.evaluate_shadow_horizon(market,horizon)
                            self.shadow_errors.pop(key,None)
                        except Exception as exc:
                            self.shadow_errors[key]=f"{type(exc).__name__}:{exc}"
                            self.shadow_retry_ns[key]=time.time_ns()+2_000_000_000
                    elif left<horizon-10:
                        self.record_shadow_missed(market,horizon,self.shadow_errors.get(key,"evaluation_window_not_observed"))
                if slug in self.evaluated: continue
                if 45<=left<=60 and time.time_ns()>=self.evaluation_retry_ns.get(slug,0):
                    if self.smoke_seconds is not None:
                        self.evaluated.add(slug)
                    else:
                        try:
                            result=await self.evaluate_market(market)
                            self.record_market_result(market,result,seconds_left=left)
                            self.evaluation_errors.pop(slug,None)
                        except Exception as exc:
                            self.evaluation_errors[slug]=f"{type(exc).__name__}:{exc}"
                            self.evaluation_retry_ns[slug]=time.time_ns()+2_000_000_000
                elif left<45:
                    self.record_market_result(market,{"decision":"missed",
                        "reason":self.evaluation_errors.get(slug,"evaluation_window_not_observed")},seconds_left=left)
            self.status(); await self._wait(.5)

    async def settle(self)->None:
        while not self.stop_event.is_set():
            await self.settle_once()
            await self._wait(15)

    async def settle_once(self, *, ignore_clock:bool=False)->None:
        fills=self.ledger.open_fills(); fill_slugs={str(fill["slug"]) for fill in fills}
        for slug in sorted(fill_slugs|self.ledger.unsettled_shadow_slugs()):
            if not ignore_clock and int(time.time())<int(slug.rsplit("-",1)[1])+300: continue
            try:
                url=GAMMA+slug; event=await self.get_json(url); label=label_from_event(slug,event,source_url=url,retrieved_utc=datetime.now(UTC).isoformat())
                if label["status"]!="VERIFIED": continue
                official="up" if label["outcome_up"]==1 else "down"
                for fill in (item for item in fills if str(item["slug"])==slug):
                    settlement_id="S-"+hashlib.sha256((str(fill["fill_id"])+official).encode()).hexdigest()[:24]
                    if self.ledger.record_settlement(settlement_id=settlement_id,fill_id=str(fill["fill_id"]),official_outcome=official,
                        ts_ns=time.time_ns(),evidence_url=url): self.diagnostic("settlement",fill_id=fill["fill_id"],official=official)
                shadow_count=0
                for shadow in self.ledger.unsettled_shadows(slug):
                    if self.ledger.record_shadow_settlement(shadow_id=str(shadow["shadow_id"]),official_outcome=official,
                        ts_ns=time.time_ns(),evidence_url=url): shadow_count+=1
                if shadow_count: self.diagnostic("shadow_settlement",slug=slug,official=official,count=shadow_count)
            except Exception as exc:self.diagnostic("settlement_retry",slug=slug,error=f"{type(exc).__name__}:{exc}")

    async def _wait(self,seconds:float)->None:
        try: await asyncio.wait_for(self.stop_event.wait(),timeout=seconds)
        except TimeoutError: pass

    async def run(self)->int:
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGINT,signal.SIGTERM):
            with contextlib.suppress(NotImplementedError): loop.add_signal_handler(sig,self.stop_event.set)
        timeout=aiohttp.ClientTimeout(total=15,sock_read=25)
        atomic_json(self.runtime/"paper.pid.json",{"pid":os.getpid(),"started_utc":datetime.now(UTC).isoformat()})
        (self.runtime/"paper.pid").write_text(f"{os.getpid()}\n")
        async with aiohttp.ClientSession(timeout=timeout,headers={"User-Agent":"QuantOS-S199-paper/1.0"}) as session:
            self.session=session; self.status("starting")
            tasks=[asyncio.create_task(x()) for x in (self.discover,self.polymarket_feed,self.binance_feed,self.refresh_stale_books,self.evaluator,self.settle)]
            if self.smoke_seconds is not None:
                await self._wait(self.smoke_seconds); self.stop_event.set()
            else: await self.stop_event.wait()
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
        healthy=all(self.live_checks.values()) if self.smoke_seconds is not None else True
        stopped={"shutdown":"clean"}
        if self.smoke_seconds is not None: stopped["smoke_pass"]=healthy
        self.status("stopped",**stopped)
        pid_path=self.runtime/"paper.pid"
        if pid_path.exists() and pid_path.read_text().strip()==str(os.getpid()): pid_path.unlink()
        self.ledger.close(); return 0 if healthy else 3


async def run_engine(root:Path,runtime:Path,smoke_seconds:float|None=None)->int:
    return await PaperEngine(root=root,runtime=runtime,smoke_seconds=smoke_seconds).run()
