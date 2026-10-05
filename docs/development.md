# Development

The Python server is in `people_workbench.py`, the browser app is in `web/`,
and tests use synthetic data.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_people_workbench.py' -v
```

With Node.js installed, check the photo-preview rectangle calculations and
controls with `node --test tests/test_asset_preview.mjs`.
