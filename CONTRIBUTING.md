# Contributing

Implement repeatable tasks with well-defined inputs and outputs as directly callable commands or scripts. Keep the business rules in testable functions and document the command, input format and failure behavior. Reserve skills for workflows that require judgment across steps.

This is an early independent research/DIY project, not a ready-made global AI service.

Read PROJECT_CHARTER.md and docs/DECISIONS_AND_OPEN_QUESTIONS.md first. Preserve the distinction between original goals, provisional prototype choices, tested behavior and unverified hypotheses. A proposal to change consensus, pricing, participant rights or recursion semantics should include an explicit design decision and counterexamples.

Use test-driven development with temporary databases and standard-library unittest. Mark simulated participants and messages as synthetic. Never include credentials, personal conversations, private reference exports or host-specific paths in a patch. Do not copy proprietary project internals.

Before submission: run tests, run the CLI against a new disposable database, verify that no existing database is overwritten, and state which functions were not tested. Contributions use the repository license only for material the contributor has the right to provide. No CLA or public contribution service is set up yet.

## Check a working copy

Use Python 3.11 or later and run the following command from the repository root. It runs the unit tests and synthetic CLI demo, reads the resulting SQLite database independently, then checks that repeating the demo preserves the existing database.

```sh
python tests/verify_core.py
```

Read `reports/core_verification.md` for failures and skipped tests. The peer integration tests start temporary loopback services. To include certificate-based tests, put OpenSSL on PATH or set `ROUNDTABLE_TEST_OPENSSL` to its executable path.

## Check a commit for publication

Review the proposed file list, licenses and data provenance against the [public release checklist (Chinese)](docs/PUBLIC_RELEASE_CHECKLIST.md). Keep local evidence under `reports/`. Once the intended changes are committed, verify the exact revision in a fresh directory.

```sh
python tests/verify_snapshot.py reports/snapshot-review --revision HEAD --openssl openssl
```

The verifier compares the archive inventory with the Git tree, runs the core checks in the extracted snapshot, and exercises registration and withdrawal over mutual TLS. A successful run writes the commit ID, archive SHA-256 and file count to `reports/snapshot-review/verified.json`. Use a new output directory for each run.
