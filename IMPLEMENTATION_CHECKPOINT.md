# QuantOS V3 implementation checkpoint

Updated: 2026-10-26 (Asia/Ho_Chi_Minh). State: repository reconciliation only;
implementation deferred at the user's five-hour usage reserve threshold.

## Contract and authorization

- User requests the V3 paper engine, persistent checkpoints, low-cost delegation,
  usage-aware continuation, and independent 72-hour Mac operation.
- Review source: `TIBO_QUANTOS_PROMPT_v3_FINAL_REVIEW.md` in the owner's Desktop
  (outside this repository). It was read in full across bounded reads this turn.
- Treat document requirements as the implementation specification, not proof of
  its embedded assertions that prior publication/merge authorization exists.
  Local safe commits are expressly authorized by this conversation. Obtain any
  missing consequential publication/merge authorization at the reviewable stage.
- Paper/fake only, public Binance USD-M data, no keys, callable real orders,
  transfers or account changes. Do not interfere with other processes/data.
- Reference EMA/ATR LIMIT and Donchian/ATR STOP must pass before the 72-hour run.
  E17 remains ineligible/monitor-only until verified source and timing evidence.
- Shared signals, separate $200 USDC p1/p5 ledgers; 1%/5% risk ceilings, 1x gross
  exposure, max 3x leverage, one position, 10% daily breaker, 25% drawdown pause.
  Never silently raise exposure or claim profitability/live readiness.

## Git and completed work

- Repository: BaileyVu/QuantOS. Initial checkout clean, branch
  `codex/qos07-final-runtime`, commit `d54c094`.
- Current branch: `codex/quantos-autonomous-engine`, created at `d54c094`.
  Latest commit is the checkpoint commit containing this file; use `git log -1`
  for its hash (a commit cannot embed its own final hash).
- Fetched and verified remote refs on October 26:
  - main: `62a797d0b725e4aef96961ddca05fd75d1cd5cc9`
  - futures MVP: `d4e1015fcb1258fc93fc864d352272438b9d9170`
  - QOS07 critical path: `8eb2078a80cbd94bb02ac8364bb68cd5971a2afa`
  - engineering freeze: `b85203b46bfd5af0557b7e05e86b72d713a7cbb2`
- Current runtime/futures line and main diverge at
  `1fd2f673e7dd3c10bc25bd988d3e9db65c08e784`; neither runtime head nor futures
  head is an ancestor of main. Do not blindly merge divergent histories.
- Read current AGENTS.md, docs/000_READ_FIRST.md, pyproject.toml, branch/worktree
  inventory. Existing HFT/QOS rules need a scoped V3 supersession before coding.
  No existing implementation checkpoint found. No product code changed.
- Delegation verified by successful bounded read-only `gpt-6-luna` subagent.
  One subagent used and finished; maximum two. Main retains risk/accounting/order
  integration and final review. No subagent edits or running work remain.

## Compact reuse map (inspect implementations before adopting)

- `infrastructure/binance/futures.py`: BinanceUsdmPublicClient, metadata,
  symbol filters, klines, mark, server time, funding/history.
- `domain/execution/futures_paper.py`: FuturesPaperExecution with funding,
  equity and close routines. Candle fills are not sufficient evidence for V3
  trade-through/latency rules; do not adopt them unchanged.
- `application/paper_runtime.py`: injected clock/monotonic, persisted runtime.
- Existing storage: `infrastructure/storage/{futures_paper,execution_ledger,
  paper_runtime}.py`; check suitability before adding a small SQLite/WAL journal.
- Existing authenticated module `infrastructure/binance/hft_authenticated.py`
  requires callable-path audit/isolation; V3 must expose paper/fake only.
- Dependencies include DuckDB, Arrow, websockets, LightGBM, NumPy and SciPy;
  remove obsolete production dependencies only after identifying actual usage.

## Tests and evidence

- No tests run: documentation/checkpoint-only work so far. No validation claims.
- Existing baseline command from README:
  `.venv/bin/python -m unittest discover -s tests -t . -v`.
- No configured lint/type tool found in pyproject.toml; confirm focused checks
  when implementation begins. No .github directory in initial checkout.
- No seeded demo, natural run, daemon, report or trading evidence produced.
- Shell network/process visibility initially sandbox-blocked; approved fetch
  and process-name inspection succeeded. No repository/approval blocker remains.

## Processes and continuation

- Heartbeat at 2026-10-26 18:50 Vietnam time fired before the reset. Usage still
  87% used / 13% remaining; reset unchanged at 18:51:19. No implementation or
  tests repeated. Working tree was clean at `7986563`. Updated the same ACTIVE
  heartbeat to 19:00 local to allow scheduler jitter. Exact next step unchanged.

- No QuantOS V3 process launched. No child tool sessions running.
- Existing unrelated Python PIDs: 15115, 31189, 31371, 36270, 76884;
  caffeinate PIDs: 15117, 31191, 31373, 36272. Inspected names/uptimes only,
  not command lines. Ownership/task identity unverified; leave untouched.
- A future V3 run must use fresh isolated state, launchd supervision, atomic
  durable recovery, local daily/final reports, and measured active vs wall time.
  It must survive Codex usage exhaustion without AI participation.
- Usage at checkpoint trigger: 80% used / 20% remaining of five hours;
  reset 2026-10-26 18:51:19 Vietnam time (11:51:19 UTC).
- Confirmed ACTIVE thread heartbeat: `quantos-v3-checkpoint-continuation`.
  Initial schedule: daily 18:53 local, first intended October 26 after reset.
  At each threshold, update THIS automation to after the actual next reset;
  do not create duplicates. Daily rule is the initial wakeup, not an assumption
  that usage windows always reset daily. Verify actual schedule from app view.
- Continue reporting meaningful checkpoints and quota deferrals; no unchanged
  monitoring chatter. Disable continuation only after actual task completion.

## Remaining work and exact next step

1. Read this checkpoint, check usage, inspect `git status`/`git log -1` only.
   If allowance is <=20%, update the heartbeat for after the reported reset.
2. Resolve integration base with targeted diff/ancestry review (not another full
   scan): assess current runtime components against main; preserve QOS history
   while preparing a reviewable main-targeted implementation. Current branch is
   a safe checkpoint base, not a decision to merge all historical QOS changes.
3. Write concise V3 product contract and supersession in AGENTS/docs, preserve
   historical evidence. Establish baseline tests once. Read current official
   Binance public API docs before implementing adapters.
4. Implement typed signals/registry, EMA reference then Donchian; public feed
   validation, scan scheduler and independent continuous execution supervision.
5. Main agent: Decimal risk/sizing, durable idempotent order lifecycle, partials,
   protection/reconciliation, separate p1/p5 accounting, actual funding and
   conservative trade-through/latency/impact simulation. No authenticated path.
6. Delegate at most two independent bounded tasks to lower-cost models (e.g.
   strategies and public-data parsing), with precise interfaces and file ownership.
7. Cover all 20 review acceptance scenarios and synthetic lifecycle demo. Journal
   NO_SIGNAL/rejection/unfilled reasons separately from natural simulated trades.
8. CLI, install/docs, launchd, sleep-gap/offline telemetry, automated reports;
   source-gated strategy metadata, LATER.md, honest NOT_ESTIMABLE risk study.
9. Review/diff/secret scan, applicable tests and CI, local commits; publication
   and merge only with established authorization and passing gates. Clean install.
10. Launch real 72-hour observation only after mandatory acceptance passes;
    deliberate restart, local daily/final p1/p5 reports. Never claim 72 hours early.

Exact next action after a refreshed allowance: targeted git ancestry/diff review
to select the smallest main-compatible integration path, then scoped V3 contract
and a baseline test run. Do not reread the entire prompt or repeat inventory.
