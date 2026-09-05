# Testbed reliability and documentation

Approved scope: preserve the working single-PC EPC/IMS/eNB topology; fix SMSC provisional responses and duplicate MO handling, provisioning failure reporting/IDs, and eNB source mutation. Document the imported baseline and both subsequent commits, operations, configuration boundaries, and validation evidence.

No component upgrades, new runtime dependencies, database migrations, host changes, or radio transmission are part of this work. Implementation was reviewed on `codex/testbed-polish`; the maintainer subsequently authorized publishing directly to `main`.

## Execution

- [x] SMSC: reproduce `100 Trying → 200 OK`, duplicate pending/completed MO, timeout replay and expiry in existing handler tests. Keep provisional responses pending; cache transaction results briefly and replay without sending another MT. Retain multi-Via behavior.
- [x] Provisioning: reproduce Mongo/API failures and non-default database IDs. Verify PyHSS 1.0.2 API contracts; use returned IDs, reject failed writes, honor AMF and preserve SQN on updates. Test with external I/O replaced; do not contact live databases.
- [x] eNB: run initialization in an unprivileged Linux container with a stub `srsenb`. Prove runtime values are rendered and the source files remain identical. Copy all companion config files into a temporary directory, edit there, execute from that directory. Mount source read-only.
- [x] Documentation: inspect `cabd17a`, `5f6a374`, and `9cacfbd`; write architecture/history, operational runbook, and testing/limits. Keep Korean and English entrypoints consistent. Separate maintainer-reported end-to-end success from local automated verification.
- [x] Review all changes; run Python suite, Linux initialization test, shell syntax, Compose configuration validation and Markdown link checks. Record results and remaining hardware validation in the testing guide.

## Ownership and integration

SMSC worker owns its handler/tests; provisioning worker owns its script/tests. Main owns eNB, task definitions, documentation and final integration. The newly fetched checkout had no user edits and the baseline SMSC suite passed all 31 tests. Shared interface changes must be reflected in the runbook before completion.

## Completion evidence

- Baseline: 31 SMSC tests passed at 9cacfbd.
- SMSC and provisioning workers used failing regression cases before implementation.
- Linux eNB check failed against original initialization, then both mounted and fallback paths passed with read-only source.
- Final suite with optional containers enabled: 65 passed. Compose config and 23 Bash syntax checks passed.
- Independent code review approved. Documentation review identified overstated test coverage; API edge checks and source-vs-runtime distinctions were corrected.
- Existing single-PC hardware success is maintainer-reported. No full EPC/IMS rebuild, live provisioning, host setup or radio activation was performed.
