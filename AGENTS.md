# Agent notes for People Workbench

Read `README.md` before making workflow changes. If `var/backlog.md` exists,
read it for local plans and open work; `var/` is intentionally not tracked.

- Keep real photos, names, API keys, and `.people-workbench/` out of
  source inspection, tests, logs, commits, and external services. Use synthetic
  fixtures unless the user explicitly authorizes a specific private-data check.
- Keep agent plans, backlogs, handoffs, and scratch files in ignored `var/`.
- Keep all Immich writes behind explicit Pending review and Sync.
- For major interaction changes, show the proposed flow or wireframe for user
  approval first; preserve the established keyboard shortcuts and local state.
- Preserve compatibility with legacy `immichPeopleConsole.*` browser storage
  keys when changing saved settings or Merge canvas state.
