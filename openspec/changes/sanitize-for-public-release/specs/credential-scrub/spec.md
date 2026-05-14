## ADDED Requirements

### Requirement: Credential files are absent from git history and working tree
The system's git repository SHALL contain no Google Cloud service-account JSON files in any commit reachable from any ref, including tags and remote-tracking branches.
After remediation, running `git log --all -- "gg-tts-keys/*.json"` MUST return no output.
The working tree SHALL also contain no `*.json` files under `gg-tts-keys/` (active or quarantine subdirectory).

#### Scenario: History rewrite removes all credential commits
- **WHEN** `git filter-repo --path gg-tts-keys/ --invert-paths` is executed
- **THEN** `git log --all -- "gg-tts-keys/*.json"` returns empty
- **THEN** `git log --all -- "gg-tts-keys/quarantine-2026-04-13/*.json"` returns empty

#### Scenario: No credential files remain in working tree
- **WHEN** the rewrite and cleanup are complete
- **THEN** `ls gg-tts-keys/*.json 2>/dev/null` returns no files
- **THEN** `ls gg-tts-keys/quarantine-2026-04-13/*.json 2>/dev/null` returns no files

### Requirement: .gitignore prevents future credential commits
The `.gitignore` SHALL contain patterns that prevent all credential file types from being staged or committed, covering both current and plausible future credential formats.

#### Scenario: JSON key files are ignored
- **WHEN** a new `*.json` file is placed in `gg-tts-keys/`
- **THEN** `git status` does NOT list the file as untracked or staged

#### Scenario: ElevenLabs key files are ignored
- **WHEN** a new `.txt` or `.key` file is placed in `elevenlabs-keys/`
- **THEN** `git status` does NOT list the file as untracked or staged

#### Scenario: Entire gg-tts-keys subtree is covered
- **WHEN** a `*.json` file is placed in `gg-tts-keys/quarantine-2026-04-13/` or any nested subdirectory
- **THEN** `git status` does NOT list the file as untracked or staged

### Requirement: Revocation precedes publication
All 14 Google Cloud service-account keys that appeared in the git history SHALL be revoked in Google Cloud Console before the rewritten history is force-pushed to the public remote. Evidence of revocation (screenshot or Cloud Console audit log) MUST be obtained before the publication step.

#### Scenario: Keys are disabled before force-push
- **WHEN** the force-push step in the migration plan is reached
- **THEN** all previously committed service-account keys have status DISABLED or DELETED in their respective GCP projects

### Requirement: SECURITY.md documents credential policy
A `SECURITY.md` file SHALL exist at the repo root explaining: (1) how to report a discovered credential leak, (2) that `gg-tts-keys/` and `elevenlabs-keys/` are never committed, and (3) how contributors obtain credentials for local development.

#### Scenario: SECURITY.md is present and tracked
- **WHEN** the repo is published
- **THEN** `git ls-files SECURITY.md` returns `SECURITY.md`

#### Scenario: SECURITY.md covers all three required topics
- **WHEN** SECURITY.md is read
- **THEN** it contains a section on reporting credential leaks
- **THEN** it contains a section explaining the gitignored credential directories
- **THEN** it contains a section on how to obtain dev credentials
