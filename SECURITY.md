# Security Policy

## Reporting a Credential Leak

If you discover that a secret, key, or credential has been exposed in this repository (in tracked files or in git history), please report it **immediately** and **privately**:

- Open a **private** GitHub security advisory (preferred): use the "Report a vulnerability" button on the repository's Security tab.
- Alternatively, email the maintainers directly. Do not open a public issue for credential leaks.

Include:
- The file path or commit SHA where the credential appears
- The type of credential (e.g., GCP service-account key, API key)
- Any evidence of exposure (scanner output, etc.)

We aim to respond within 24 hours and will revoke affected credentials as soon as possible.

---

## Credential File Policy

The following directories are **gitignored** and must never be committed to the repository:

| Directory | Contents |
|---|---|
| `gg-tts-keys/` | Google Cloud service-account JSON key files |
| `elevenlabs-keys/` | ElevenLabs API key files |
| `.env.deploy` | Deployment environment secrets |

Rules:
- Never add `*.json` credential files to the staging area or force-add them via `git add -f`.
- Never remove the relevant `.gitignore` entries without a security review.
- If you accidentally commit a credential, **treat it as compromised immediately** — revoke the key before pushing and rewrite history with `git filter-repo`.

---

## Obtaining Development Credentials

This project uses `MAKER8_CREDENTIAL_SOURCE=db` (the default). Local development requires a database URL pointing to an editor8 instance that holds credentials — no JSON key files are needed in the repo.

**Steps to get a dev environment working:**

1. Set `MAKER8_EDITOR8_DATABASE_URL` to a valid editor8 PostgreSQL connection string (obtain from a project maintainer out-of-band — never shared in public issues or chat).
2. Leave `MAKER8_CREDENTIAL_SOURCE=db` (the default) in your environment.
3. If you need a legacy env-file-based credential for a specific service, request the key from a maintainer, store it outside the repository, and point the relevant env var at the file path.

**Do not** request or share credentials in public GitHub issues, pull requests, or comments.
