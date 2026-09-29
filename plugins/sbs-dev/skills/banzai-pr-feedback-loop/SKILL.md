---
name: banzai-pr-feedback-loop
description: "Monitor an open pull request for Banzai Codes bot feedback, address blocking findings on its branch, and keep polling until the current head has no blockers."
---

# Banzai PR Feedback Loop

Use this skill when the user asks to monitor a PR for Banzai Codes review feedback and fix the requested changes in a loop.

Read [the Banzai review contract](references/banzai-review-contract.md) before interpreting review results.

## Boundaries

- Work only on the identified PR's existing head branch. Never merge, close, retarget, delete, or force-push the PR.
- Follow the repository's `AGENTS.md` and normal validation instructions.
- Treat only feedback from a recognized Banzai bot as loop control. Human reviews may inform implementation, but they do not satisfy the Banzai terminal condition.
- Do not stop because a check is pending, because no feedback is visible yet, or because a transient GitHub/API request failed.
- Stop and report a real permission, authentication, missing-tool, or unresolved product decision blocker rather than guessing.

## Loop

1. Resolve the PR from the user's reference, current repository, or current branch. Record its repository, number, head branch, and current head SHA. Require `gh` authentication before making changes.
2. Read the PR diff, changed files, repository instructions, current checks, Banzai reviews, inline review comments, and issue comments. Establish a baseline of already-seen Banzai feedback IDs and the current head SHA so old feedback is not reprocessed.
3. Poll GitHub continuously at roughly 30-second intervals. Use the pull-request reviews, review-comments, issue-comments, and check/run APIs; consume paginated results. Back off briefly on rate limits or transient failures, then resume polling.
4. When new Banzai blocking feedback for the current head appears:
   - Address every actionable blocking finding that belongs to the PR.
   - Inspect surrounding code and the repository contract before editing.
   - Run the smallest relevant tests/checks, then the repository's required validation for the changed area.
   - Commit the fix on the PR branch and push normally.
   - Reset the baseline to the new head SHA and continue polling. A push is required to obtain a review for the new head.
5. If a finding is incorrect or cannot be addressed without a product decision, leave a concise PR reply explaining the evidence and stop for user direction; do not claim the Banzai loop is complete.
6. Continue polling after every push until a Banzai review for the current head explicitly reports no blocking issues. Do not accept a clean result attached to an older commit. If a newer blocking result arrives after a clean result, resume the loop.
7. When the terminal clean signal arrives, verify the PR head is unchanged, required checks are not still running, and no newer Banzai blocking feedback exists. Report the fixes, validation, final head SHA, and the clean Banzai result.

The skill is intentionally persistent: an invocation remains active while waiting for Banzai feedback. It terminates only on a current-head no-blockers signal or a genuine blocker requiring user intervention.
