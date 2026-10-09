# Development

The Python server is in `people_workbench.py`, the browser app is in `web/`,
and tests use synthetic data.

Face Review stores the assigned face IDs discovered as a person's photo pages
load in local SQLite. Completion requires the last page and a submitted decision
for every assigned face. Immich's person statistics count photos, so they cannot
serve as the face total. Successful face reassignment or merging invalidates the
affected local inventory. While Face Review is open, a bounded read-only scan
fills missing inventories one photo page at a time; it resumes partial scans
and skips failed people until the user retries. Submitted accepted faces are
hidden in the browser by default, while drafts remain visible until submission.

Group photos keeps candidate asset metadata in memory while a bounded background
scan reads each photo's faces. SQLite saves its settings, active photo, draft
names, and initial/deferred pass progress. Reload and Sync rebuild the candidate
index. Group photo submission saves Pending changes and pass progress in one
transaction; the scan and submission never write to Immich. Failed local draft
saves remain in an in-memory snapshot map; explicit Submit Names retries those
saves before advancing. Refreshing the face context does not overwrite unsaved
names. Only successfully saved drafts survive a browser reload.

## Sync and mutation safety

Sync holds the Store's shared mutation lock until completion, including its
people refresh. Pending/review/group edits use the same reentrant lock and
reject competing requests with HTTP 409; they are not silently replayed after
a remote write. Read-only face scans and people/Pending/media reads remain
available. Group-photo view preparation also holds the lock because it may
repair progress and delete resolved drafts. Retry that view after a busy
response. These locks coordinate request threads in one Workbench process;
run one process per state directory.

A Pending response includes a `reviewToken` computed from its entire person and
face Pending snapshot in one SQLite read transaction, including operation
payloads, inclusion and checkpoints. HTTP Sync requires this token and compares
it after acquiring both Sync and mutation locks, before any upstream write. A
missing or stale token returns 409 and the browser reloads Pending for another
explicit confirmation. Pagination shares the token for the whole queue. Internal
AppState test/helper callers can omit it; the HTTP route never bypasses it.

The browser's in-app Sync dialog captures the token when it opens. Its explicit
Apply uses that captured token even if a later refresh changes Pending. Cancel
and Escape make no Sync request. Confirmation never retries automatically.

Each pending merge has a durable `merge_step`: ready, inflight, uncertain, or
complete. Persist inflight before the upstream request and complete after
validating its result. A confirmed merge's failed photo update resumes from
complete; inflight after a restart and uncertain outcomes require human
reconciliation, never blind replay. Inclusion toggles preserve that checkpoint;
a started merge cannot be overwritten by a different queued operation.

The transport accepts the source-only legacy merge response for known Immich
versions before 3.3.0, and requires both source and target for 3.3.0 onward or an
unknown version. Only a source NO_PERMISSION with a successful intended target
(or the separately checked legacy target) is classified as a retryable refusal.
NOT_FOUND and failed-target outcomes remain uncertain because partial effects
can occur without a success result. It rejects duplicate/unrelated/missing IDs,
malformed success flags, explicit failures, and inconsistent success/error
rows. Contracts were
checked against official 2.0.0, 3.0.0, 3.2.0, 3.2.1, 3.3.0 and 3.3.1 source;
this source inspection and synthetic coverage do not establish live compatibility
with every version. The guide mock uses the current two-result contract.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_people_workbench.py' -v
node --test tests/test_group_photos.mjs
for file in web/*.js tests/test_group_photos.mjs; do node --check "$file"; done
```

With Node.js installed, run the synthetic browser interaction tests with
`node --test tests/*.mjs`. Check changed browser modules with `node --check`.

## Guides and synthetic screenshots

See [Writing guides and taking screenshots](writing-guides.md) for the
reusable loopback mock, fresh runtime, capture recipes, privacy rules, and
rendered-guide review. The documentation tools use Pillow; the app itself
remains dependency-free.

## Codex commit identity and local files

All Codex commits must use the bot as both author and committer:

`people-workbench-agent[bot] <335216742+people-workbench-agent[bot]@users.noreply.github.com>`

For each clone, from any worktree:

```bash
python3 tools/agent_git.py configure
```

This installs a conditional repository include for `codex/**` branches and
shared pre-commit/pre-merge-commit guards. Every worktree in that clone uses
the same configuration. Human branches keep their existing identity. Existing
custom hooks or core.hooksPath are not overwritten; integrate the guard with
them deliberately before agent commits.

After reviewing and staging explicit files on a task branch:

```bash
python3 tools/agent_git.py commit -m "Describe the change"
git show -s --format='%an <%ae> | %cn <%ce>' HEAD
```

The helper explicitly sets author and committer, rejects author overrides,
refuses main, checks staged var paths, and verifies the resulting identity.
The installed hooks also reject a wrong identity on Codex branches and any
staged additions/modifications under var. Do not bypass them.

`var/` is for local plans, captures, harness state, and scratch files. It must
never be committed, even with git add -f. Check with `git ls-files -- var`.
If a local file is already tracked, remove only its index entry with
`git rm --cached -- path/to/file`; keep its contents. Rewriting already
published history needs explicit user authorization.

Bot metadata identifies a commit's author; it does not authenticate a GitHub
account or provide permission to push. Follow the repository's separate
confirmation-before-push policy.
