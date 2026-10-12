# Sanitized QOS-07 engineering source release

Derived exclusively from tracked files at original QOS-07 commit
`d54c094c9bc8af8e0240e9d022ff5b03662b7854`.
This independent root history contains no ancestors from that original graph.
Original implementation checkpoint and historical report hashes remain provenance
references; they are not ancestors or release-commit identifiers.

Production code, tests, strategy/risk configuration, package dependencies and
executable file modes are unchanged byte-for-byte. Publication changes are limited
to portable documentation paths, omission of local paper-account identity hashes,
this release notice, the public review manifest,
a README scope notice, and expanded ignore rules. The manifest lists every other
tracked path, line count, size and SHA-256 content hash. It omits its own hash to
avoid circular self-reference; Git binds the manifest itself to the release SHA.
Use the immutable commit URL or `git rev-parse HEAD` to identify this snapshot.

The approved and tested operational scope is public-market paper simulation.
No authenticated requests, Testnet/Mainnet orders, profitability validation or
real-money authority is granted. Historical adapters/specifications and earlier
research context are retained source; they are not permission to invoke live
execution. QOS-07 engineering verification is distinct from Alpha qualification.

The original engineering baseline and this sanitized snapshot are verified with
the complete 1,257-test offline suite. A five-minute public-feed paper smoke was
performed on the byte-identical original executable; its reported zero trades
and exact replay are engineering evidence only. Raw runtime evidence, accounts,
market captures, private material and development environments are not included.

Existing public branches retain their original histories. The earlier privacy
audit identified machine-local commit identities already reachable publicly.
Publishing this clean snapshot does not remove that pre-existing exposure.
Any historical-privacy remediation requires a separate owner decision; this
release does not rewrite or delete existing refs.
