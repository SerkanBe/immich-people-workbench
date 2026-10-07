# Development

The Python server is in `people_workbench.py`, the browser app is in `web/`,
and tests use synthetic data.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_people_workbench.py' -v
```

With Node.js installed, run the synthetic browser interaction tests with
`node --test tests/*.mjs`. Check changed browser modules with `node --check`.
