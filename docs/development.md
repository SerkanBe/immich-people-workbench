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
transaction; the scan and submission never write to Immich.

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
