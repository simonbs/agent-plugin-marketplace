# Banzai review contract

Use these exact visible markers when classifying Banzai review output. The
hidden launcher marker, when present, precedes the visible marker and must be
preserved in any reply.

## Blocking signals

- `🔍 **Review:** ⛔ Blocking —`
- `📋 **Acceptance:** ⛔ Not implemented as specified —`

Treat a blocking summary as actionable feedback. Also inspect all inline review
comments and review comments associated with the same current head.

## Terminal clean signals

- `🔍 **Review:** ✅ No blocking issues`
- `📋 **Acceptance:** ✅ Implements the linked issue(s)`

A clean signal is terminal only when its review `commit_id` matches the PR's
current head SHA and no newer Banzai blocking feedback exists.

## GitHub data sources

Use the authenticated `gh` CLI and paginate each source:

```sh
gh api --paginate repos/OWNER/REPO/pulls/PR/reviews
gh api --paginate repos/OWNER/REPO/pulls/PR/comments
gh api --paginate repos/OWNER/REPO/issues/PR/comments
gh pr checks PR --repo OWNER/REPO
```

Prefer the bot login(s) supplied through `BANZAI_BOT_LOGINS`, a comma-separated
environment variable. When it is unset, require both a GitHub bot author and
one of the exact Banzai visible markers; never treat an ordinary human comment
as Banzai feedback solely because it contains the word “blocking”.

Review records carry `commit_id`; use it to associate summaries and inline
reviews with a head. Issue comments do not reliably carry a commit SHA, so use
their creation/update time and feedback ID relative to the recorded baseline,
then confirm the current-head review before declaring success.
