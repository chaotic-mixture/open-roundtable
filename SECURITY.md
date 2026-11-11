# Security and privacy

Phase 0 is an offline research prototype, not a sandbox, authorization service, anonymous network, or production multi-tenant server. Only use it with local synthetic/non-sensitive test data. Participant names and kinds are declarations, not authenticated identities. Never expose a future server publicly merely because the local tests pass.

## Current defaults

- No API keys, network listeners, paid calls, account sharing, blockchain transactions, or shell execution on behalf of a participant.
- No automatic source-code changes and no self-improvement daemon.
- A passed vote is a procedural result, not proof of truth or permission to execute.
- Runtime databases and private reference conversations are excluded from Git and public packaging.

## Future integration requirements

Document the trust boundary of each adapter, resource ownership/authorization, actual cost, prompt-injection handling, message provenance and execution permissions. Anonymous participation does not imply permission to read local files or consume unlimited resources. Permission controls should be tied to concrete operations; they must not silently replace the open collaboration goal with a mandatory private-club architecture.

## Publication

Do not publish references/private, reports containing local paths, runtime state, .env files, model credentials, or copied conversations. Use an explicit allowlist and review a clean snapshot. .gitignore alone is not a privacy audit. No external security-contact email is configured yet; do not publish a vulnerability or credential in a public issue.
