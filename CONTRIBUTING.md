# Contributing

This is an early independent research/DIY project, not a ready-made global AI service.

Read PROJECT_CHARTER.md and docs/DECISIONS_AND_OPEN_QUESTIONS.md first. Preserve the distinction between original goals, provisional prototype choices, tested behavior and unverified hypotheses. A proposal to change consensus, pricing, participant rights or recursion semantics should include an explicit design decision and counterexamples.

Use test-driven development with temporary databases and standard-library unittest. Mark simulated participants and messages as synthetic. Never include credentials, personal conversations, private reference exports or host-specific paths in a patch. Do not copy proprietary project internals.

Before submission: run tests, run the CLI against a new disposable database, verify that no existing database is overwritten, and state which functions were not tested. Contributions use the repository license only for material the contributor has the right to provide. No CLA or public contribution service is set up yet.
