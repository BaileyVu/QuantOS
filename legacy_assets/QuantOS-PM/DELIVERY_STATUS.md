# QuantOS-PM V0 delivery status

The isolated offline MVP is implemented and demonstrated. Public mainnet data
capture and replay work. Strategy promotion, valid live quoting and full wallet
PnL reconstruction remain blocked by the evidence gaps below.

| Deliverable | Actual status |
| --- | --- |
| Independent repository/environment/storage | Created in QuantOS-PM; no QuantOS worktree or runtime reuse |
| Offline paper CLI | Demonstrated at $200/$350/$500 without network |
| Six business boundaries / Decimal accounting | Implemented |
| Risk, queue, partial fills, cancellation, reconciliation | Implemented and tested |
| Replay / append-only audit / restart idempotency | Implemented and tested |
| Execution sensitivity comparison | All nine capital/model combinations run |
| Automated tests | 60 passed, zero failed; includes 500 randomized settlement scenarios |
| Public shadow | Actual public capture: 23,781 messages, 45,630 canonical events; zero virtual orders |
| Final engine replay of capture | Recorded in evidence/shadow-replay.json |
| Wallet API extraction | Cursor exhaustion: 175,821 activity and 151,835 trade rows |
| April/May/June | Zero API activity in April/May; 4,823 rows in June; profile join date in May |
| Polygon sample | 2/12 receipt samples verified; 10 provider HTTP 403 responses |
| Exact wallet replica / full attribution | Not established; redemption basis and incentive discrepancies remain |
| Live execution | Absent; flag rejected; no signing or authenticated order client |

The pessimistic synthetic PnL is −$3.51960799 / −$6.68977140 / −$9.25.
Maximum drawdown is 2.000000% / 2.000000% / 1.958433%, respectively.
These are virtual mechanics results, not real earnings or an estimated market edge.

## Outstanding evidence gates

- The captured public books produced integrity/eligibility stops. The public feed
  provides no exchange sequence proof, and aggressor/virtual queue attribution
  remains unverified. The engine retains raw events and refuses new exposure.
- Settlement/TWAP rules and the legacy fee-field discrepancy need independent
  validation. No forward-trained, validated live calibration is supplied.
- Full transaction receipt/burn decoding and cross-source cash/PnL reconciliation
  are incomplete. A large unexplained attribution residual and incentive-total
  differences are documented, not assigned to strategy profit.
- There is no empirical conditional reversal model for early loser exits.
- No profitable edge, shadow-quoting readiness or live approval is asserted.

## Evidence and commands

- [README and runnable commands](README.md)
- [Actual paper runs and manifests](evidence/PAPER_RUNS.md)
- [Test output](evidence/tests.txt)
- [Public shadow evidence](evidence/SHADOW_RUN.md)
- [Wallet evidence and discrepancies](evidence/WALLET_RESEARCH.md)
- [Isolation audit](ISOLATION_AUDIT.md)
- [Promotion gates](PROMOTION_GATES.md)

All changes are confined to this sibling project. The isolation audit documents
the allowed original-design reads and the initial filename scan; it does not
claim an impermissible inspection of S199's runtime state.
