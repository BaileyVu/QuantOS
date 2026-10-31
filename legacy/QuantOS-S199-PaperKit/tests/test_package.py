import json
import tempfile
import unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]))
from doctor import ENV_MAPPING, load_key_values, verify_policy
from session_summary import summarize

class PaperKitTests(unittest.TestCase):
    def test_environment_is_static_and_has_no_credentials(self):
        d=load_key_values(Path(__file__).resolve().parents[1]/"config.env")
        self.assertEqual(d["LIVE_SANDBOX_STARTING_BALANCE"],"200")
        self.assertTrue(d["LIVE_BTC_SNAPSHOT_MODEL_PATH"].endswith("s199_cost101_daily_stop120_profile.json"))
        self.assertEqual(len([v for v in d if "KEY" in v or "SECRET" in v or "PRIVATE" in v]),0)
    def test_reject_shell_injection(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"x.env";p.write_text("X=$(touch evil)\n")
            with self.assertRaises(ValueError):load_key_values(p)
    def test_policy_exactly_matches_profile(self):
        with tempfile.TemporaryDirectory() as td:
            upstream=Path(td);p=upstream/"live/models/btc_snapshot_model_s199_cost101_daily_stop120_profile.json";p.parent.mkdir(parents=True)
            from doctor import MODEL_RELATIVE
            d=load_key_values(Path(__file__).resolve().parents[1]/"config.env")
            policy={k:(d[v] if k=="momentum_alignment" else float(d[v])) for k,v in ENV_MAPPING.items()}
            policy["snapshot_seconds"]=[60]
            p.write_text(json.dumps({"deployment_profile":{"profile_name":"s199_cost101_daily_stop120","model_source":"live/models/btc_snapshot_model_s150_ev_guarded_cached_432_summary.json","execution_policy":policy}}))
            verified,_=verify_policy(upstream,Path(__file__).resolve().parents[1]/"config.env")
            self.assertEqual(verified,len(ENV_MAPPING))
            policy["max_ask_price"]=0.99
            p.write_text(json.dumps({"deployment_profile":{"profile_name":"s199_cost101_daily_stop120","model_source":"live/models/btc_snapshot_model_s150_ev_guarded_cached_432_summary.json","execution_policy":policy}}))
            with self.assertRaisesRegex(ValueError,"max_ask_price"):
                verify_policy(upstream,Path(__file__).resolve().parents[1]/"config.env")
    def test_empty_session_is_not_a_profitable_trade(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td);(path/"session.json").write_text(json.dumps({"session_id":"test","initial_virtual_usd":200}))
            s=summarize(path)
            self.assertIsNone(s["account_equity_usd"])
            self.assertFalse(s["settlement_ledger_present"])
    def test_settlement_state_not_claimed_as_real(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td);(path/"session.json").write_text(json.dumps({"session_id":"test","initial_virtual_usd":200}))
            (path/"s199_settlements.json").write_text(json.dumps({"summary":{"synthetic_equity":"199.50"},"positions":[{"slug":"btc-updown-5m-123","settled":False}]}))
            s=summarize(path)
            self.assertFalse(s["restart_safe"]);self.assertEqual(s["unsettled_count"],1)
            self.assertEqual(s["status"],"unsettled_positions")

if __name__=="__main__":unittest.main()
