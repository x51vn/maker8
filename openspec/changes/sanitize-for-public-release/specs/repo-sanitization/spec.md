## ADDED Requirements

### Requirement: Agent-tooling state directories are untracked and gitignored
The `.playwright-mcp/` and `.serena/memories/` directories SHALL NOT be tracked by git. Their contents SHALL be listed in `.gitignore` so they cannot be re-added without an explicit `--force` flag.

#### Scenario: playwright-mcp files are not tracked after cleanup
- **WHEN** `git ls-files .playwright-mcp/` is run after the change
- **THEN** the command returns no output

#### Scenario: serena memories are not tracked after cleanup
- **WHEN** `git ls-files .serena/memories/` is run after the change
- **THEN** the command returns no output

#### Scenario: gitignore prevents re-adding tooling state
- **WHEN** a new file is created in `.playwright-mcp/` or `.serena/memories/`
- **THEN** `git status` does NOT show that file as untracked or staged

### Requirement: Hardcoded private infrastructure references are removed from deploy scripts
`deploy-direct.sh` and `deploy-production.sh` SHALL NOT contain hardcoded values for: deployment host IP, SSH key path, registry hostname, deployment username, or compose directory. These values SHALL be sourced from a `.env.deploy` file that is gitignored.

#### Scenario: Deploy scripts source external config
- **WHEN** `deploy-direct.sh` or `deploy-production.sh` is inspected
- **THEN** no line contains a literal IP address matching `10.113.213.9`
- **THEN** no line contains the literal path `/home/beou/`
- **THEN** the script sources or reads from `.env.deploy`

#### Scenario: .env.deploy.example is committed with placeholders
- **WHEN** `git ls-files .env.deploy.example` is run
- **THEN** the file is tracked
- **WHEN** `.env.deploy.example` is read
- **THEN** it contains placeholder values (e.g., `<deployment-host>`, `<ssh-key-path>`) not real IP addresses

#### Scenario: .env.deploy is gitignored
- **WHEN** a file `.env.deploy` is created in the repo root
- **THEN** `git status` does NOT list it as untracked

### Requirement: Internal-only docs are removed from the tracked tree
The following files SHALL NOT appear in `git ls-files` after the change:
- `docs/EDITOR8_UI_KEY_MANAGEMENT_PLAIN_TEXT_DB_PRD_2026-04-10.md`
- `docs/CENTRALIZED_KEY_MANAGEMENT_EXECUTION_PLAN_2026-04-11.md`
- `docs/OPENCLAW_INSTALL_GUIDE_2026-04-11.md`
- `docs/OPENCLAW_USE_CASES_2026-04-11.md`
- `docs/SEARXNG_UBUNTU_INSTALL_OPTIMIZED_2026-04-11.md`
- `docs/X51_COMMIT_SKILL_UPDATE_*.md` (all variants)
- `docs/COMMIT_REPORT_*.md` (all variants)
- `docs/EDITOR8_PUBLISH_TIMEOUT_INVESTIGATION_AND_FIX_GUIDE_2026-04-11.md`
- `deployment/server2/` (entire subtree)
- `.playwright-mcp/` (entire subtree — also covered by credential-scrub; gitignore here)

#### Scenario: Internal docs are absent after cleanup
- **WHEN** `git ls-files docs/EDITOR8_UI_KEY_MANAGEMENT_PLAIN_TEXT_DB_PRD_2026-04-10.md` is run
- **THEN** the command returns no output

#### Scenario: deployment/server2 is absent
- **WHEN** `git ls-files deployment/server2/` is run
- **THEN** the command returns no output

### Requirement: Public-facing docs retain accurate content without private IPs
Docs that are kept in the repo (`docs/QUICK_REFERENCE.md`, `docs/OPERATIONS_RUNBOOK.md`, etc.) SHALL NOT contain the literal string `10.113.213.9`. Where a Kafka bootstrap server example is needed, the placeholder `<kafka-host>:9094` SHALL be used.

#### Scenario: No private IPs in tracked docs
- **WHEN** `git grep "10\.113\.213\." -- docs/` is run
- **THEN** the command returns no output

#### Scenario: QUICK_REFERENCE.md retains usable examples
- **WHEN** `docs/QUICK_REFERENCE.md` is read
- **THEN** it contains Kafka configuration examples using placeholder hostnames
- **THEN** the examples are syntactically valid (correct `MAKER8_KAFKA_BOOTSTRAP_SERVERS=` format)
