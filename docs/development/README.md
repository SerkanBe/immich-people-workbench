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

Run every discovered Python and Node test so new suites are included automatically:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/*.mjs
for file in web/*.js tests/*.mjs; do node --check "$file"; done
```

These tests use synthetic data and browser DOM doubles; they do not prove layout
or focus behavior in every browser or live compatibility with every Immich
version. API-key login, required scopes, account binding, rotation and revocation
are described in [API key connection](../api-key-authentication.md). Credentials
remain in server memory. HTTP Sync checks current identity and scopes before any
upstream write. The intended Sync boundary and recovery behavior are described
in the user guide.

Deployment tests exercise real loopback HTTP servers with synthetic data:

```bash
python3 -m unittest discover -s tests -p 'test_deployment.py' -v
```

They check configured hosts/origins, proxy trust, login cookie flags, health
without Immich access, Pending/Sync boundaries, restart persistence, and Linux
SIGTERM handling. Run them on Linux to include the shutdown test. The full
discovered Python command above includes this suite.
See [Docker deployment](../docker.md) for Compose and image release details.

## Continuous integration

`.github/workflows/checks.yml` is the reusable CI gate. It discovers all
`tests/test_*.py` on Ubuntu and Windows with Python 3.10 and 3.13, and all
`tests/*.mjs` with Node 24; it also syntax-checks browser/test JavaScript and
checks tracked public paths and local links in README/docs. A Linux Docker job
starts a fresh checkout with `docker compose up -d`, checks localhost defaults,
login and authentication boundaries, and verifies state survives container
recreation. It uses a separate project and synthetic volume without connecting
to Immich. The Docker jobs run Linux containers; they do not verify Docker
Desktop credential helpers or WSL integration. CI runs with read-only
repository permissions and no Immich credentials. Docker's release
workflow calls this gate for the tagged source revision. CI success is not a live
Immich, real-browser layout, native ARM64, or production proxy test.

`tools/check_public_files.py` inspects only Git-tracked paths and local links
in README/docs. It is a focused hygiene check, not a general secret scanner.

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
