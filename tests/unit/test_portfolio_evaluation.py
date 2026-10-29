from dataclasses import replace
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quantos.paper.research import evaluation, evaluation_signals as es
from quantos.paper.research.data import Bar, OpenInterestMetric
from quantos.paper.research.dataset import DatasetIndex
from quantos.paper.research.protocol import fingerprint
from quantos.paper.research.signals import save_signal_cache
from quantos.paper.strategies.candidate_features import ALT_SOURCE_UNIVERSE, HOUR_MS
from quantos.paper.types import Instrument


def seal():
    payload = {"plan": {"evaluation_start": "2025-01-01T00:00:00Z",
        "evaluation_end_exclusive": "2026-10-29T00:00:00Z"},
        "selection": {"primary_symbols": list(ALT_SOURCE_UNIVERSE),
                      "verified_volume_symbols": list(ALT_SOURCE_UNIVERSE)}}
    return {"payload": payload, "sha256": fingerprint(payload)}


def index():
    return DatasetIndex(bars={s: () for s in ALT_SOURCE_UNIVERSE}, marks={}, funding={},
        metrics={}, audit=(), bar_by_open_ms={}, mark_by_open_ms={},
        window=("2024-10-01", "2026-10-28"), content_hash="a"*64, unavailable_partitions=())


def instruments():
    return {s: Instrument(s, "USDT", "TRADING", D(".01"), D(".001"),
        D(".001"), D(10000), D(5)) for s in ALT_SOURCE_UNIVERSE}


class EvaluationTests(unittest.TestCase):
    def test_cohort_and_seal_must_match_before_generating(self):
        s, i = seal(), index()
        with self.assertRaises(ValueError):
            es.validate_context(i, s, "primary", ("BTCUSDT",))
        s["payload"]["selection"]["primary_symbols"].pop()
        with self.assertRaises(ValueError):
            es.validate_context(i, s, "primary", ALT_SOURCE_UNIVERSE)

    def test_evaluation_cache_roundtrip_and_record_tamper_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp)/"filters.json"
            f.write_text(json.dumps({"note": "not point-in-time"}))
            with patch.object(es, "parse_exchange_info", return_value=instruments()):
                cache = es.build_evaluation_signal_cache(index(), sealed=seal(), cohort="primary",
                    symbols=ALT_SOURCE_UNIVERSE, filter_snapshot_path=f)
            path = Path(tmp)/"signals.gz"
            save_signal_cache(cache, path)
            loaded = es.load_evaluation_signal_cache(path, sealed=seal(), cohort="primary",
                                                     index=index(), filters_path=f)
            self.assertEqual(loaded, cache)
            self.assertEqual(cache.training_window, (es.EVALUATION_START_MS, es.EVALUATION_END_MS))
            with self.assertRaises(ValueError):
                es.load_evaluation_signal_cache(path, sealed=seal(), cohort="primary",
                    index=replace(index(), content_hash="b"*64), filters_path=f)

    def test_crash_generation_is_causal_and_uses_closed_hour_boundary(self):
        i = index(); start = es.EVALUATION_START_MS
        prices = [D(10000)]*745 + list(map(D, (10000, 9900, 9800, 9600, 9000)))
        bars=[]
        for j, close in enumerate(prices):
            t=start+j*HOUR_MS
            bars.append(Bar("BTCUSDT", "1h", t, t+HOUR_MS-1,
                            close, D(10100), min(close,D(9900)), close,D(100)))
        oi=tuple(OpenInterestMetric("BTCUSDT", start+j*HOUR_MS,D(1),
            D(700) if j==749 else D(1000+j%7), ()) for j in range(751))
        i=replace(i,bars={**i.bars,"BTCUSDT":tuple(bars)},metrics={"BTCUSDT":oi})
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/"filters.json";f.write_text(json.dumps({"note":"not point-in-time"}))
            kwargs=dict(sealed=seal(),cohort="primary",symbols=ALT_SOURCE_UNIVERSE,
                        filter_snapshot_path=f,include_alt=False,include_ema=False)
            with patch.object(es,"parse_exchange_info",return_value=instruments()):
                cache=es.build_evaluation_signal_cache(i,**kwargs)
                future=replace(oi[-1],label_ms=es.EVALUATION_END_MS)
                later=es.build_evaluation_signal_cache(replace(i,metrics={"BTCUSDT":oi+(future,)}),**kwargs)
            self.assertGreater(len(cache.records),0)
            self.assertEqual(cache.records,later.records)
            self.assertTrue(all(r.decision_ms==start+750*HOUR_MS for r in cache.records))

    def test_wave_freezes_grid_and_sensitivities_without_resizing(self):
        def cell(name):
            return {"members":[{"strategy_id":name,"risk_fraction":"0.04",
                "parameters":{"holding_hours":"24","stop_fraction":"0.25"}}],"leverage":"1"}
        choices={n:cell(n) for n in ("alt_dip_reversal","crash_flush_e2","ema_level_2h")}
        s={"individual_choices":choices,"pick":{"cell":choices["alt_dip_reversal"]},
           "evaluation_grid":list(choices.values())}
        jobs=evaluation.wave_jobs(s)
        self.assertEqual(len(jobs),16)
        self.assertEqual([j["cell"] for j in jobs if j["kind"]=="base" and j["cohort"]=="primary"],s["evaluation_grid"])
        self.assertEqual(sum(j["kind"]=="stop_15pct" for j in jobs),2)
        self.assertEqual(s["pick"]["cell"]["members"][0]["parameters"]["stop_fraction"],"0.25")

    def test_source_mismatch_blocks_all_outcomes(self):
        s=seal();s["payload"]["code_sha"]="old";s["sha256"]=fingerprint(s["payload"])
        with patch.object(evaluation.experiment,"source_content_hash",return_value="new"):
            with self.assertRaisesRegex(ValueError,"source differs"):
                evaluation.run_wave(s,{}, {}, {}, "unused")
