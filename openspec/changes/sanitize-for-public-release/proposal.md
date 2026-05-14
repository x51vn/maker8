## Why

Maker8 is ready to be published as an open-source repo, but the current git history and working tree contain real Google Cloud service-account private keys, internal infrastructure details, and agent-tooling state files that must not be exposed publicly. Publishing without remediation would leak live credentials and internal network topology.

## What Changes

- **Remove all 14 Google Cloud SA JSON files** from git history using `git filter-repo`; revoke and rotate any keys that appear in history before publication
- **Strengthen `.gitignore`** so credential directories (`gg-tts-keys/`, `elevenlabs-keys/`) and agent-tooling state (`.playwright-mcp/`, `.serena/`) are fully excluded from future commits
- **Untrack agent-tooling state files** already in the index (`.playwright-mcp/*`, `.serena/memories/`) via `git rm --cached`
- **Remove or sanitize sensitive docs** in `docs/` — internal commit reports, agent-skill update notes, internal-hostname runbooks, and Dropbox/editor8-specific internal guides that reference private infrastructure
- **Sanitize deploy scripts** — replace hardcoded private IP (`10.113.213.9`), registry hostname (`docker.x51.vn`), and local SSH paths with documented placeholder variables or move them to a `.env`-style config not committed to the repo
- **Remove `deployment/server2/`** directory (internal litellm service config unrelated to maker8 itself)
- **Add `SECURITY.md`** explaining how to report credential leaks and how to obtain credentials for local dev

## Capabilities

### New Capabilities

- `credential-scrub`: Purge all Google Cloud SA JSON key files and their entire git history; verify zero credential material remains via secret-scanning; add `.gitignore` rules and README guidance so keys are never committed again
- `repo-sanitization`: Remove or replace all tracked files that contain internal hostnames, private IPs, personal paths, agent-tooling state, and non-public internal documentation; produce a clean, safe working tree ready for `git push --public`

### Modified Capabilities

- (none — no existing spec-level behavior changes)

## Impact

- **Git history rewrite** — all consumers of the repo must `git clone` fresh after publication; any forks must be notified
- **`gg-tts-keys/*.json` files** — must be revoked in Google Cloud Console before the rewritten history is force-pushed; replacement keys provisioned out-of-band
- **`.gitignore`** — new glob rules added; existing `gg-tts-keys/*.json` rule was already present but the files had already been committed before it was added
- **`docs/`** — several internal-only markdown files removed from the tracked tree; the public-facing docs (`README.md`, `docs/ARCHITECTURE.md`, API schema docs) are kept and remain accurate
- **`deploy-direct.sh` / `deploy-production.sh`** — operational scripts are kept but parameterized to avoid embedding private infrastructure details; they remain functional for internal use via a companion `.env.deploy` (gitignored)
- **No source-code changes** — `src/`, `tests/`, `config/`, `scripts/` are unaffected
