# X51 Commit Skill Update

## Summary

- Updated skill: `/home/<user>/.agents/skills/x51-commit/SKILL.md`
- Date: 2026-04-25
- Request: add project-aware commit workflow, uncommitted audit summary in commit comments, commit, push, and markdown report generation under `docs/`.

## Changes

- Replaced placeholder skill content with a concrete `x51-commit` workflow.
- Added `project` argument handling:
  - absolute path
  - relative path
  - repo name resolved under `/home/<user>/IdeaProjects/<repo-name>`
  - current working directory fallback when no project is provided
- Added required uncommitted-change audit before staging:
  - status
  - diff stats
  - staged/unstaged file lists
  - high-risk file review guidance
- Added safety rules for secrets, unrelated changes, generated files, and push failures.
- Added required `docs/COMMIT_REPORT_YYYYMMDD_HHMMSS.md` report output for future runs of the skill.
- Added commit message guidance so the audit summary is used as the commit body/comments.
- Added final Vietnamese response requirements after commit and push.

## Verification

- Reviewed updated `SKILL.md` content after patching.
- Ensured markdown report template uses tilde fences so nested code blocks remain valid.

## Remaining Notes

- No commit or push was run for this skill update.
- This report is written in the current project `docs/` folder as requested.
