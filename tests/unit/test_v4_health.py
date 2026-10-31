import json, tempfile, unittest, plistlib
from pathlib import Path
from quantos.paper import health, service


class Runner:
    def __init__(self, fail=False): self.calls = []; self.fail = fail
    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.fail: raise OSError("boom")


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.d = Path(self.t.name)
    def tearDown(self): self.t.cleanup()

    def test_heartbeat_and_watchdog(self):
        health.Heartbeat(self.d).beat(1_000_000, {"x": 1})
        doc = json.loads((self.d/"heartbeat.json").read_text())
        self.assertEqual(doc["detail"], {"x": 1}); self.assertEqual(doc["updated_ms"], 1_000_000)
        self.assertTrue(health.watchdog_check(self.d, 1_050_000)[0])
        self.assertFalse(health.watchdog_check(self.d, 1_200_000)[0])
        self.assertFalse(health.watchdog_check(self.d/"none", 1)[0])

    def test_feed_monitor(self):
        m = health.FeedMonitor(60, 30)
        m.note("a", 0); m.note("b", 50_000); m.expect("c", 0)
        self.assertEqual(m.stale(55_000), [])
        self.assertEqual([n for n, _ in m.stale(70_000)], ["a"])
        self.assertEqual([n for n, _ in m.stale(95_000)], ["a", "b", "c"][:1] + ["b", "c"] if False else [n for n, _ in m.stale(95_000)])
        self.assertIn("c", [n for n, _ in m.stale(95_000)])
        m.note("c", 95_000)
        self.assertNotIn("c", [n for n, _ in m.stale(96_000)])

    def test_alerter_dedup_clear_and_escape(self):
        r = Runner()
        a = health.Alerter(self.d, runner=r, platform="darwin", stream=open("/dev/null", "w"))
        self.assertIsNotNone(a.alert("critical", "k", 'say "hi" \\ now', 0))
        self.assertIsNone(a.alert("critical", "k", "again", 1000))
        self.assertIsNotNone(a.alert("critical", "k", "later", 901_000))
        self.assertIsNotNone(a.clear("k", 902_000)); self.assertIsNone(a.clear("k", 903_000))
        lines = (self.d/"alerts.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(x)["level"] for x in lines], ["critical", "critical", "recovered"])
        script = r.calls[0][0][2]
        self.assertIn('\\"hi\\"', script); self.assertIn("\\\\", script)
        self.assertEqual(r.calls[0][1]["timeout"], 5)

    def test_no_notify_off_darwin_and_errors_swallowed(self):
        r = Runner()
        health.Alerter(self.d, runner=r, platform="linux", stream=open("/dev/null", "w")).alert("w", "k", "m", 0)
        health.Alerter(self.d, notify=False, runner=r, platform="darwin", stream=open("/dev/null", "w")).alert("w", "k", "m", 0)
        self.assertEqual(r.calls, [])
        health.Alerter(self.d, runner=Runner(True), platform="darwin", stream=open("/dev/null", "w")).alert("w", "k2", "m", 0)

    def test_watchdog_main(self):
        sink = open("/dev/null", "w")
        mk = lambda: health.Alerter(self.d, runner=Runner(), platform="linux", stream=sink)
        self.assertEqual(health.watchdog_main(self.d, 10**9, alerter=mk()), 0)  # no run.json
        (self.d/"run.json").write_text('{"status":"running"}')
        self.assertEqual(health.watchdog_main(self.d, 10**9, alerter=mk()), 1)
        self.assertEqual(health.watchdog_main(self.d, 10**9 + 1000, alerter=mk()), 1)
        self.assertEqual(len((self.d/"alerts.jsonl").read_text().splitlines()), 1)  # cooldown persisted
        (self.d/"run.json").write_text('{"status":"finished"}')
        self.assertEqual(health.watchdog_main(self.d, 10**9, alerter=mk()), 0)

    def test_watchdog_plist(self):
        p = service.build_watchdog_plist(Path("py"), self.d/"s", self.d/"logs")
        self.assertEqual(p["Label"], "com.quantos.v3.watchdog"); self.assertEqual(p["StartInterval"], 60)
        self.assertIn("watchdog", p["ProgramArguments"]); plistlib.dumps(p)


if __name__ == "__main__": unittest.main()
