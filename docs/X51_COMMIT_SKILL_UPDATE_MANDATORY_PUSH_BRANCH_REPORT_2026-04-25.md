# X51 Commit Skill Update: Mandatory Push, Branching, Detailed Reports

## Summary

- Updated skill: `/home/<user>/.agents/skills/x51-commit/SKILL.md`
- Date: 2026-04-25
- Request:
  1. Always commit and push.
  2. For many/large/high-risk changes, create a new branch, add, commit, and push.
  3. Write a detailed markdown report describing changes, content, affected flows, and impact.

## Changes

- Added non-negotiable commit policy:
  - selected repositories must produce a commit and push
  - clean repositories create a report-only commit
  - skipped/failed verification must be reported, not used to skip commit/push
- Added branch decision section:
  - small coherent changes can commit on current branch
  - large, mixed, risky, generated, migration, auth/security, deployment, deletion, binary, lockfile, or unclear-impact changes require a new branch
- Added branch naming pattern:
  - `x51-commit/YYYYMMDD-HHMMSS-<short-topic>`
- Changed staging rule to `git add -A` so all tracked, deleted, and untracked files are included.
- Expanded report requirements:
  - branch decision and reason
  - detailed changes by file/group
  - change content summary
  - affected runtime/user/API/background/deployment/dev flows
  - impact and risk analysis
- Narrowed stop conditions to technical impossibilities:
  - invalid project/repo
  - corrupt repo or missing git author identity
  - push blocked by auth, permission, protected branch policy, or non-fast-forward that cannot be fixed non-destructively

## Verification

- The skill file was updated with `apply_patch`.
- The saved skill is checked with `cat` after this update.

## Remaining Notes

- No commit/push workflow was run as part of this skill update.
