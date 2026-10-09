# Writing guides and taking screenshots

User walkthroughs live in `docs/tour/`; the [Visual tour](visual-tour.md)
indexes them. Keep each page focused on a user's task: where to start, the
decision to make, its local result, and when Immich changes.

Use the actual app with synthetic fixtures for screenshots. Label diagrams
and design studies as illustrations; do not present them as application
screenshots.

## Start the isolated demo

The tracked `tools/tour_demo.py` launcher copies the current
`people_workbench.py` and `web/` unchanged into a fresh runtime, then starts
the app and `tools/tour_mock.py` on loopback. It refuses occupied ports before
starting and refuses a runtime that already contains state.

The documentation tools need Pillow; the app itself still needs no third-party
packages. Use an existing Python environment with Pillow, or prepare a separate
documentation environment:

```bash
python3 -m venv var/tour-venv
var/tour-venv/bin/python -m pip install Pillow
var/tour-venv/bin/python tools/tour_demo.py
```

PowerShell uses `var/tour-venv/Scripts/python.exe` instead of the POSIX
`bin/python` path. With an existing suitable Python:

```powershell
python tools/tour_demo.py
```

Open the printed app address, normally `http://127.0.0.1:18776/`, and sign in:

| Field | Synthetic value |
| --- | --- |
| Immich URL | `http://127.0.0.1:18777` |
| Email | `review@example.test` |
| Password | `synthetic-review` |

These invented credentials work only in the mock. Do not enter real credentials.
The mock has no upstream client or forwarding path, and only fictional assets.

Keep the launcher running while capturing. `Ctrl+C` stops only its own app
and mock children. Logs, the copied source, and synthetic state remain in the
printed runtime directory.

To avoid another review instance, choose unused ports:

```bash
python3 tools/tour_demo.py --app-port 18876 --mock-port 18877
```

Enter that mock port on the sign-in page. For a reproducible named runtime,
supply `--runtime-dir` with a fresh directory. On Windows, use a local drive
or the default temporary directory; SQLite state on a WSL UNC share has caused
locking errors. Never point the launcher at `.people-workbench/` or a live
state directory.

## Prepare useful states

Use a fresh launcher/runtime for a fresh app and library. Restarting the mock
resets its in-memory library; it is not a persistence emulator.

### Face review capture recipe

1. Select Mira Solis. Wait for background face counts and photo loading.
2. Accept the face from `garden-day-01-001.jpg`; dismiss the visibly different
   person in `garden-day-01-007.jpg`. Capture the draft grid.
3. Open that dismissed face with Space. Capture the photo, rectangle, and
   lightbox controls, then close it.
4. Open Search, type `Mira`, and open the name filter. Capture the compact
   controls with Named people selected.
5. Submit once, enable Show checked, and capture the local accepted face plus
   the correction awaiting Sync.

The fixture has multiple faces assigned to the same person on some photos, so
photo and face counts intentionally differ.

### Group photos capture recipe

1. Open Naming → Group photos and wait for the scan. Three composite group
   photos qualify at the default five-face minimum; one is archived.
2. Select a matching unnamed cluster, type a fragment such as `Do`, and
   capture its existing-person suggestion.
3. Accept the suggestion. On another unnamed face, type `ignore` and capture
   the explicit Ignore this cluster suggestion, then select it.
4. Leave other entries blank, submit, then submit the remaining photos blank
   to reach Pass complete. Capture the deferred count and deliberate next pass.

The first displayed photo can depend on scan arrival order. Use its visible
portraits to choose the matching person rather than relying on marker numbers.
Some UI settings live in browser storage; use a separate browser profile or
clear only this synthetic origin's settings when an exact starting view matters.

## Capture and review

- Use a consistent desktop viewport with controls readable. Record viewport
  and any relevant settings in an ignored local handoff.
- Wait for images, scan progress, and save/submission operations to settle.
- Capture app content, including enough context to identify the page and
  result. Focus can scroll a field into view: return to the top and let scrolling
  settle, or capture the full page/panel so headings and controls stay visible.
  Avoid account credentials, browser chrome, and unrelated tabs.
- Save final images under `docs/images/visual-tour/` with descriptive names.
  Browser screenshot tools that return JPEG must use `.jpg`, not `.png`.
- Browser developer tools offer a page screenshot command. Codex should use
  its supported browser screenshot API, save the returned bytes, and visually
  inspect the result. Do not fabricate controls or alter screenshot content.
- Add meaningful alt text. Place each image next to the instructions it supports.
- In HTML-aware Markdown previews, target at most 840 px wide and 600 px
  tall while keeping the aspect ratio. Set the HTML image width to the integer rounded
  down from `min(840, 600 * original_width / original_height, original_width)`,
  inside a link to its original file, as the tour pages do. This is a display
  size; the original file remains full resolution for inspecting small controls.
  Allow images to shrink on narrow screens; never stretch smaller captures.
- Check the exact file open in the editor: a primary-checkout guide can differ
  from its worktree copy. In PyCharm, compare the Markdown preview with its
  image width attribute. Clicking a screenshot opens the full-resolution file
  in the image editor, where the Markdown display size does not apply. If the
  updated Markdown preview still ignores the width, check its custom CSS and
  renderer settings; do not infer a file-size limit from the HTML attribute.
- Preview rendered Markdown, check all links/images, and check actual button
  labels and shortcuts against the running app.
- Verify the mock audit still shows zero writes after drafting/submitting,
  before any explicit Sync. `/__review__/audit` on the mock shows requests
  and successful fictional writes.

For Codex browser capture, after reading the available browser API documentation,
the basic save operation is:

```javascript
const bytes = await tab.getScreenshot(); // supported CUA tab binding
const fs = await import("node:fs/promises");
await fs.writeFile(absoluteScreenshotPath, bytes);
```

Use only a confirmed synthetic tab. Keep agent scratch, process records, and
capture notes under ignored `var/`; commit only the reusable tools, fixture
assets, guides, and approved screenshots.

## Fixture scope and provenance

The six portraits in `tools/tour-fixtures/` were cropped from the repository's
existing, explicitly synthetic visual-tour images. They depict fictional,
AI-generated people. Names, identifiers, counts, dates, and filenames are
invented. Group photos are composites of those portraits, not real gatherings.

The mock covers only the API subset needed by these walkthroughs. Ranking uses
fictional identity ordering; it does not validate recognition quality or
Immich compatibility. Repeated portraits and composites are layout examples,
not evidence about real crops or face detection.

It supports an optional deliberate failure mode through the mock's
`POST /__review__/settings` with `{"failWrites": true}` for separate Pending
recovery illustrations. This never affects another Immich instance.

Do not change the production app to stage a screenshot. If a workflow changes,
update the fixture or the capture recipe, then recapture the affected guide.