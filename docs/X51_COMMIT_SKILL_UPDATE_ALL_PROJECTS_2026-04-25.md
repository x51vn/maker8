# X51 Commit Skill Update: All Projects Mode

## Summary

- Updated skill: `/home/beou/.agents/skills/x51-commit/SKILL.md`
- Date: 2026-04-25
- Request: when `project` is not specified, process all projects sequentially.

## Changes

- Replaced the old no-project behavior.
  - Before: fallback to current working directory.
  - After: discover all immediate git repositories under `/home/beou/IdeaProjects` and process them in sorted order.
- Added single-project versus all-projects behavior.
- Added rule that each repository gets its own audit, report, commit, push, and final status.
- Added clean-repo skip behavior.
- Added blocked-project behavior:
  - do not commit blocked repo
  - record the reason
  - continue to the next repo unless the failure is global
- Updated report template to include `Mode`.
- Updated final response requirements to summarize all processed, skipped, blocked, committed, and pushed projects.

## Verification

- The updated skill file was written with `apply_patch`.
- The saved skill will be checked with `cat` after this update.

## Remaining Notes

- No repository commit/push workflow was run as part of this skill update.
