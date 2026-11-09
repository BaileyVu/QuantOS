# QuantOS finish v6 — 2026-11-09
Budget70% weekly start;47% left(23points spent), max50 total,wrap22/floor20. Zero resets. Five-hour36% left; reset verified, next Nov9 19:54:40Vietnam. MAX ONE helper(idle). Owner alone merges/deploys/services. v6 prompt Desktop/TIBO_PROMPT_FINISH_v6.md. Do not read archived history or repeat passing checks.

PR branches (codex/ prefix):
47 v4-infra-contract 2c52b60
48 infra-stressed-stop 63caef7
49 infra-resolver-gaps 9282e07
50 infra-exchange-protection dae73e8
51 infra-clean-deploys 4bf6102
52 infra-shadow-counterfactual a1942e8
53 infra-registry-manifests a003409(local),989770c(remote)
54 infra-replay-integration 965168b
55 infra-sentibot-integration 788aa97
56 infra-timesfm25-integration eca49f9 PARKED draft; runner772217c WIP DO NOT RUN in QuantOS-infra-timesfm. No research/exports/downloads until after launch and only weekly>35%. Width failed; drift unrun.
57 infra-release source72c8cd71d19ed8f64944f4c7b61874d9a6896491, pushed draft https://github.com/BaileyVu/QuantOS/pull/57. No merges.

Done: gzip passive capture/runtime, consumed sentiment/ml1h bytes, sanitized exchange/user events, original committed model fills, start/stop journal snapshots; capture errors/status, no pruning. No InputArchive/no postlaunch reportingCLI. Launcher407c6a1 reviewed/fixed72c8cd7: clean_snapshot returns src; rollback CLI72h; stop pair beforestart; reject local unresolved orders. No service or existingstate changes.
Tests: full suite ONCE508PASS4skip before finalstream/launcherchanges. Then runtime16PASS,newstreamclient1PASS,changedlauncher6PASS. Prior4006decision proof retained.
Snapshot QOS/app-v4/<fullSHA>/source; QOS=~/Library/Application Support/QuantOS. Review plan QOS/release-plans/demo-nonstop-<fullSHA>; Sentibotshadow plist there, isolated state QOS/sentibot-reach-shadow-72c8cd7. Neither service installed/started. Previous snapshot5d8342d4e90613783a93a08a58bbc662906236b4 verified; rollback finite72h metadata required.
First attempt /private/tmp/quantos-v6-soak-72c8cd7 completed650.89s,exit0,0journalerrors,0fills,1703capture events,0captureerrors. FAILED clean acceptance: stale Sentibot warning then natural recovery08:01UTC. No source/service/input changes.
Fresh retry PASS650.56s,exit0,0errors/alerts/captureerrors,1344records,both streamsconnected,0fills. Replay719inputs/22scans/allcontextcomplete:6414/6414decisions EXACT. Prior4006proof retained. Durable evidence ../QuantOS-release-evidence/<fullSHA>/quantos-v6-soak-72c8cd7-fresh; failedattempt beside it. No activeprocesses/helperidle. Code/scripts/tests unchanged from tested72c8cd7; laterdocs only.
NEXT ownerlaunch: merge47..55then57;56parked. Exact commands docs/DEMO_RELEASE_HANDOFF.md. Preparedplan above. Owneraloneexecutes; never change service/state. No repeats whilewaiting.
AFTERownerlaunch: fill_report first20fillsOR3days,24hshadow72hSentibot; actualevidence pending. PreservealtML/ml5m,sealed2025+,mainnetguards,costs.
