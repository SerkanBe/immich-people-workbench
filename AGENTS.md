# Agent notes for People Workbench

## Start here

- Read `README.md` and the relevant files in `docs/` before changing a workflow.
  If `var/backlog.md` exists, read it for local plans and open work. `var/` is
  intentionally ignored by Git.
- Check `git status` before editing. Preserve unrelated work already in the tree.
- Keep agent plans, backlogs, handoffs, and scratch files in `var/`.

## Privacy and Immich safety

- Keep real photos, names, API keys, and `.people-workbench/` out of source
  inspection, tests, logs, commits, and external services. Use synthetic
  fixtures unless the user explicitly authorizes a specific private-data check.
- Treat `.people-workbench/state.sqlite3`, its reports, and saved browser
  settings as private user state. Do not inspect, reset, or publish them as part
  of routine development.
- Keep every Immich write behind explicit Pending review and Sync. Queueing an
  action must not call an Immich write endpoint; reading, arranging, grouping,
  and reviewing must not sync implicitly.

## Product behavior

- For major interaction changes, show the proposed flow or wireframe for user
  approval before implementation. Preserve established keyboard shortcuts and
  local state unless the approved flow changes them.
- Preserve compatibility with legacy `immichPeopleConsole.*` browser storage
  keys when changing saved settings or Merge canvas state.
- Keep Merge canvas groups and buckets local organizational state. Only a
  confirmed merge enters Pending; only explicit Sync applies it to Immich.

## Code and verification

- `people_workbench.py` contains the local server, state store, and Immich API
  operations. `web/` contains the browser interface. `tests/test_people_workbench.py`
  uses synthetic data for server and workflow tests.
- For Python or server workflow changes, run
  `python3 -m unittest discover -s tests -p 'test_people_workbench.py' -v`.
  Add or adjust synthetic tests for changed behavior, especially Pending and
  Sync boundaries, persistence, and failure or retry paths.
- For JavaScript changes, run `node --check` on each changed `web/*.js` file.
  Check affected browser behavior with synthetic data when practical. For
  changes to a review flow, check queueing, Pending review, and the explicit
  Sync boundary. For canvas or settings changes, check save and reload; for
  keyboard changes, check the affected shortcuts and focus behavior. Syntax
  checks alone do not verify these interactions. State any checks that could
  not be run in the handoff.
- For documentation-only changes, `git diff --check` and review of the rendered
  instructions are sufficient. Before finishing any task, review the diff and
  `git status` for private state and unrelated edits.

## Documentation

- Keep `README.md` focused as the public entry page. Update the relevant file in
  `docs/` when setup, permissions, storage, workflow, or shortcuts change, and
  adjust the README overview when the project's scope changes.
- Put durable agent workflow rules in this file. Put temporary plans and
  handoffs in ignored `var/`; do not turn them into public documentation.
- Document important limitations or manual verification gaps in the final
  handoff. Avoid copying private examples into documentation.

## Review checklist

- For each affected path, check that no Immich write occurs before explicit
  Pending review and Sync.
- Check that no real names, photos, API keys, or local state enter code, tests,
  logs, documentation, or the staged diff.
- If browser storage or Merge state changed, check compatibility with legacy
  `immichPeopleConsole.*` keys. If interaction code changed, check relevant
  shortcuts and preserved local state.

## Working with other agents

- When multiple agents are assigned, agree on file ownership before editing
  shared files. Tell the others when an interface or shared behavior changes.
- Give a handoff with changed files, decisions, checks run, and unresolved
  issues. Do not overwrite another agent's edits or silently take over their
  files; coordinate a shared change first.

## Git changes

- Work on a separate task branch, preferably in an isolated worktree. Start
  from the latest available `main`, fetching `origin/main` when it exists.
  Preserve the user's checkout and any unrelated changes. Never commit to or
  push `main`.
- Keep each commit focused on one task. Stage explicit files or hunks, then
  inspect the staged diff and status before committing. Exclude private state,
  `var/`, and unrelated working-tree changes.
- Complete the agreed scope and relevant checks, then push the task branch and
  open a pull request against `main`. Describe the behavior, changed files,
  checks, and any material limitations in the PR. The pull request is the
  user's review point; separate approval before each task-branch commit is not
  required. Leave PR review and merging to the user.
- If branch push or PR creation is unavailable, keep the work on the task
  branch and report what is needed. Do not put the changes on `main` instead.
- Do not amend, reset, rebase, force-push, or discard another person's work
  unless the user specifically asks for that operation.
