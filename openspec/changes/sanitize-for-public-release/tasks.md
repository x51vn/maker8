## 1. Pre-steps (Ops — must complete before any git rewrite)

- [ ] 1.1 Identify all 14 GCP project IDs from the committed SA JSON files and list them
- [ ] 1.2 Revoke / disable each service-account key in Google Cloud Console for every project (myenglishapp, plucky-command, science52, science53, vanhoalichsu53, and all 9 quarantine files)
- [ ] 1.3 Confirm replacement credentials for the production worker are provisioned via `MAKER8_CREDENTIAL_SOURCE=db` path (no new JSON files needed in the repo)
- [ ] 1.4 Notify all repo collaborators that a history rewrite is imminent; ask them to pause work until the force-push is complete

## 2. Install git-filter-repo

- [ ] 2.1 Install `git-filter-repo`: `pip install git-filter-repo` (or `pip3 install git-filter-repo`)
- [ ] 2.2 Verify: `git filter-repo --version` exits without error

## 3. Rewrite git history to remove credential files

- [ ] 3.1 Create a full local backup of the repo: `cp -r . ../maker8-backup-$(date +%Y%m%d)`
- [ ] 3.2 Run filter-repo to excise `gg-tts-keys/` from all history: `git filter-repo --path gg-tts-keys/ --invert-paths --force`
- [ ] 3.3 Verify no JSON credential files remain in history: `git log --all -- "gg-tts-keys/*.json"` must return empty
- [ ] 3.4 Verify `gg-tts-keys/quarantine-2026-04-13/` is also absent from history: `git log --all -- "gg-tts-keys/quarantine-2026-04-13/"` must return empty
- [ ] 3.5 Confirm the `gg-tts-keys/README.md` file is still present in HEAD (filter-repo only removed `.json` files if `--path gg-tts-keys/` was used — adjust to use `--path-glob` if needed to keep the README)

> **Note on step 3.5:** If the entire `gg-tts-keys/` dir was removed, re-add just the README: `git checkout HEAD~1 -- gg-tts-keys/README.md && git add gg-tts-keys/README.md && git commit -m "restore: gg-tts-keys/README.md after history rewrite"`

## 4. Strengthen .gitignore

- [x] 4.1 Add `gg-tts-keys/**/*.json` to `.gitignore` (covers nested subdirectories, replaces the existing `gg-tts-keys/*.json` rule)
- [x] 4.2 Add `.playwright-mcp/` to `.gitignore`
- [x] 4.3 Add `.serena/memories/` to `.gitignore`
- [x] 4.4 Add `.env.deploy` to `.gitignore`
- [x] 4.5 Add `deployment/server2/` to `.gitignore`
- [x] 4.6 Commit the updated `.gitignore`: `git add .gitignore && git commit -m "chore: strengthen .gitignore for public release"`

## 5. Untrack agent-tooling state files

- [x] 5.1 Untrack `.playwright-mcp/` from git index: `git rm -r --cached .playwright-mcp/`
- [x] 5.2 Untrack `.serena/memories/` from git index: `git rm -r --cached .serena/memories/`
- [x] 5.3 Commit: `git commit -m "chore: untrack agent-tooling state (.playwright-mcp, .serena/memories)"`
- [x] 5.4 Verify: `git ls-files .playwright-mcp/ .serena/memories/` returns no output

## 6. Remove internal-only docs from the tracked tree

- [x] 6.1 Remove internal PRDs and editor8-specific docs:
  `git rm docs/EDITOR8_UI_KEY_MANAGEMENT_PLAIN_TEXT_DB_PRD_2026-04-10.md docs/CENTRALIZED_KEY_MANAGEMENT_EXECUTION_PLAN_2026-04-11.md docs/EDITOR8_PUBLISH_TIMEOUT_INVESTIGATION_AND_FIX_GUIDE_2026-04-11.md`
- [x] 6.2 Remove agent-skill update notes: `git rm docs/X51_COMMIT_SKILL_UPDATE_*.md`
- [x] 6.3 Remove internal commit reports: `git rm docs/COMMIT_REPORT_*.md`
- [x] 6.4 Remove unrelated install guides: `git rm docs/OPENCLAW_INSTALL_GUIDE_2026-04-11.md docs/OPENCLAW_USE_CASES_2026-04-11.md docs/SEARXNG_UBUNTU_INSTALL_OPTIMIZED_2026-04-11.md`
- [x] 6.5 Remove `deployment/server2/` subtree: `git rm -r deployment/server2/`
- [x] 6.6 Review `docs/OPERATIONS_RUNBOOK.md` and `docs/MAKER8_GOLIVE_INVESTIGATION_GUIDE.md` for private hostnames; remove or sanitize as needed
- [x] 6.7 Commit: `git commit -m "chore: remove internal-only docs and deployment configs for public release"`

## 7. Sanitize private IP and hostname references in kept docs

- [x] 7.1 In `docs/QUICK_REFERENCE.md`, replace all occurrences of `10.113.213.9` with `<kafka-host>` (keep the port)
- [x] 7.2 Verify no private IPs remain in tracked docs: `git grep "10\.113\.213\." -- docs/` must return empty
- [x] 7.3 Commit: `git commit -m "chore: replace private IPs with placeholders in QUICK_REFERENCE.md"`

## 8. Parameterize deploy scripts

- [x] 8.1 Create `.env.deploy.example` at repo root with placeholder variables:
  `DEPLOYMENT_HOST=<deployment-host-ip>`, `DEPLOYMENT_USER=<ssh-user>`, `SSH_KEY=<path-to-ssh-key>`, `COMPOSE_DIR=<remote-compose-dir>`, `REGISTRY=<docker-registry>`, `MAKER8_TAG=<image-tag>`
- [x] 8.2 Update `deploy-direct.sh` to source `.env.deploy` instead of hardcoding values; replace `DEPLOYMENT_HOST`, `DEPLOYMENT_USER`, `SSH_KEY`, `COMPOSE_DIR` with variable references
- [x] 8.3 Update `deploy-production.sh` similarly: replace `REGISTRY`, `DEPLOYMENT_HOST`, `DEPLOYMENT_USER`, `SSH_KEY`, `COMPOSE_DIR`, and the tag variables with variable references sourced from `.env.deploy`
- [x] 8.4 Remove hardcoded public-facing service URLs from the echo block at the end of both scripts (or replace with a generic placeholder)
- [x] 8.5 Test that `deploy-direct.sh` and `deploy-production.sh` still parse correctly with `bash -n`
- [x] 8.6 Commit: `git commit -m "chore: parameterize deploy scripts via .env.deploy for public release"`

## 9. Add SECURITY.md

- [x] 9.1 Create `SECURITY.md` at repo root with three sections: (1) Reporting a credential leak (email or issue), (2) Credential file policy (`gg-tts-keys/` and `elevenlabs-keys/` are gitignored — never commit), (3) How to obtain dev credentials (use `MAKER8_CREDENTIAL_SOURCE=db` with a provided `MAKER8_EDITOR8_DATABASE_URL`, or request keys out-of-band)
- [x] 9.2 Commit: `git commit -m "docs: add SECURITY.md for public release"`

## 10. Final verification and publication

- [ ] 10.1 Run `git log --all -- "gg-tts-keys/*.json"` — must return empty
- [ ] 10.2 Run `git ls-files .playwright-mcp/ .serena/memories/ deployment/server2/ docs/EDITOR8_UI_KEY_MANAGEMENT*.md` — must return empty
- [ ] 10.3 Run `git grep "10\.113\.213\."` — must return empty (or only in ignored files)
- [ ] 10.4 Run `git grep -l "private_key"` across tracked files — must return empty
- [ ] 10.5 Optionally run `trufflesecurity/trufflehog` or `gitleaks` against the full history to confirm no secrets remain
- [ ] 10.6 Force-push the rewritten history: `git push --force-with-lease origin main`
- [ ] 10.7 Publish the repository (set visibility to public on GitHub/GitLab)
