# Maintenance roadmap

Priorities for October 2026 through October 2027 are reviewed in public issues and pull requests. These are intended work items, not promised dates or claims that an OpenSSF level has been awarded.

1. Establish and test the recovery arrangement described in [GOVERNANCE.md](GOVERNANCE.md), including repository administration, CI, publication and security reporting. This is an open mandatory Silver requirement.
2. Preserve correct D-Bus owner attribution and unavailable-value handling, bounded alert ingestion and verified TLS before credentials. Keep regressions for malformed input, reconnects, certificate rejection and delivery failures.
3. Add identity-bound cryptographic release signatures and recipient verification, test them against a real published release, and validate repeatability of all distributed payloads. Current checksums and immutable receipts do not prove either property.
4. Measure complete first-party application, relay, installer and release-tooling coverage without counting tests as product statements; improve uncovered paths and maintain a six-month bugfix/regression ledger. Existing partial coverage is not a project-wide 80% claim.
5. Keep dependencies, security analyses and native runtime compatibility current. Validate exact candidates on their intended systems before stable publication; preserve the distinction between source tests and operational acceptance.
6. Maintain accessible text documentation and review new CLI behavior for keyboard use, readable output and diagnostic redaction. Evaluate authenticated alert ingestion and protected MQTT/metrics deployment paths.

The scope remains observation, metrics, traces and alert forwarding. It does not include actuator control, an independent equipment safety system, tamper-proof audit storage or an unauthenticated Internet-facing collector.
