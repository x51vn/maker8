# X51 Commit Skill Update: Current Workspace Mode

## Summary

- Updated skill: `/home/<user>/.agents/skills/x51-commit/SKILL.md`
- Date: 2026-04-25
- Request: when `project` is not specified, process all projects in the current workspace sequentially.

## Changes

- Replaced the hardcoded `/home/<user>/IdeaProjects` all-project discovery rule.
- Added current workspace discovery:
  - use `*.code-workspace` folders when present
  - otherwise use immediate child git repositories under the current working directory
- Kept stable sorted processing order.
- Clarified that only resolved git repositories are selected.
- Removed a duplicated warning line about not committing from a parent directory.

## Verification

- The updated skill file was written with `apply_patch`.
- The saved skill is checked with `cat` after this update.

## Remaining Notes

- No commit/push workflow was run as part of this skill update.
