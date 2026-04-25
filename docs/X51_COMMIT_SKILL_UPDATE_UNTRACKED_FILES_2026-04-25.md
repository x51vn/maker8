# X51 Commit Skill Update: Untracked Files

## Summary

- Updated skill: `/home/<user>/.agents/skills/x51-commit/SKILL.md`
- Date: 2026-04-25
- Request: before committing, the skill must add untracked files.

## Changes

- Added `git ls-files --others --exclude-standard` to the audit commands.
- Added explicit review requirements for untracked files.
- Added rule: untracked files must not be silently skipped.
- Updated staging guidance to include all safe untracked files before commit.
- Added a pre-commit check for remaining untracked files.
- Added report section for untracked files and their decision.
- Added stop condition for untracked files that cannot be safely staged.

## Verification

- The skill file was updated with `apply_patch`.
- The saved skill is checked with `cat` after this update.

## Remaining Notes

- The rule still preserves secret safety: unsafe untracked files block that repository instead of being committed silently.
