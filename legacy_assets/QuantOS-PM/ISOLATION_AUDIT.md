# Isolation audit — QuantOS-PM V0

## Boundary and scope

All created/modified project files, the Git repository, virtual environment,
SQLite journals, writer locks, public captures, fixtures, tests and reports live
under `/Users/bailey12/Desktop/PythonProject/QuantOS-PM`.
The Git repository was initialized independently on `codex/quantos-pm-v0`.
It is not a QuantOS worktree. No remote was added, commit made, or push performed.

The existing QuantOS repository was used only for these owner-authorized
read-only design references:

- `QuantOS/AGENTS.md`
- `QuantOS/README.md`
- `QuantOS/docs/000_READ_FIRST.md`
- `QuantOS/docs/002_SYSTEM_ARCHITECTURE.md`
- `QuantOS/docs/008_IMPLEMENTATION_GUIDE.md`

A first workspace-wide **filename** discovery listed matching README, AGENTS and
package-manifest paths, including unrelated sibling names; it did not read those
other files. Subsequent inspection was restricted to the design files above and
the new project. This audit discloses that initial broad filename discovery;
it does not claim that only the new directory's names were ever enumerated.

## Runtime separation

- No S199 process enumeration, signal, stop, restart, deployment or connection.
- No inspection of its runtime files, logs, databases, locks, ports, credentials,
  exchange accounts or capital.
- No QuantOS executable imports, virtual environment reuse, shared configuration,
  state root or account client.
- No modifications or Git operations against QuantOS, its main branch or frozen
  specifications. The original repository was never used as a working directory
  for a mutation or application command.
- The new `.venv` uses the system Homebrew Python 3.13 executable. Its optional
  dependencies are websockets 16.0 and pyarrow 23.0.1, installed in this `.venv`.
- Paper/shadow public transport targets are fixed Polymarket data hosts. Their
  HTTP is GET-only with a path allowlist. A separate research-only Polygon
  PublicNode client sends JSON-RPC POSTs restricted to chain ID, receipt and
  canonical-block reads; transaction/signing methods are rejected. Redirects
  are rejected, environment proxies ignored, and no authentication header
  is supplied. WebSocket messages are public subscriptions/PING.
- No wallet signing dependency, order client, order endpoint, blockchain
  transaction sender, private key loader or dotenv loader exists in the source.
- Every research/live feature flag rejects `true`; live activation is impossible
  through the supported V0 interface.

## Executed checks

The automated suite checks that offline replay makes no socket connection,
project file reads/writes remain under this root, sibling/symlink paths are
rejected, live flags fail, private/order endpoints are denied, prohibited imports
and signing/order methods are absent, and restart cannot duplicate the ledger.
The paper CLI was actually run for $200/$350/$500. Public shadow sessions and
read-only wallet research were actually run from this independent project.
See `evidence/tests.txt`, `evidence/PAPER_RUNS.md`, and the recorded command reports.

## What this establishes

This is an action-scope and source/control audit, supported by the tool history
and tests. It establishes that this implementation has no configured integration
with S199 and that our mutations targeted the new project. It is **not** an
independent host-wide filesystem/process attestation: reading S199 to compare
its runtime, credentials or logs would itself violate the mandate. We did not
perform such an inspection or claim a byte-for-byte runtime comparison.
