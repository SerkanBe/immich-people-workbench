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

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_people_workbench.py' -v
```

With Node.js installed, run the synthetic browser interaction tests with
`node --test tests/*.mjs`. Check changed browser modules with `node --check`.
