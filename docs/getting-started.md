# Getting started

People Workbench currently runs as a local Python server. It needs Python 3.10
or newer and has no third-party Python dependencies.

## Requirements and tested version

- Python 3.10 or newer.
- An Immich admin account with password login enabled.
- Git, if you use the clone instructions below. ZIP installation does not need Git.

The maintainer manually tested Workbench with **Immich v3.2.1** for the first
release. Automated tests and the synthetic walkthrough also passed, using the
mock rather than a real Immich server. Other Immich versions have not been
verified through this release's manual testing.

Pillow is needed only for the synthetic demo and screenshots; Node.js is needed
only for the JavaScript tests. Neither is required to run the app.

## Download the source

With Git installed:

```bash
git clone https://github.com/SerkanBe/immich-people-workbench.git
cd immich-people-workbench
```

Alternatively, download the repository as a ZIP and extract it. Open a terminal
in the extracted folder containing `people_workbench.py` and `web/`.

## Run locally

On Linux, macOS, or WSL:

```bash
python3 people_workbench.py
```

On Windows, use PowerShell:

```powershell
python people_workbench.py
```

If `python` does not select your installed Python 3.10 or newer, use
`py -3 people_workbench.py` if the Python launcher is installed. Otherwise,
invoke your Python executable explicitly. For example, adjust this path to
match your installation:

```powershell
& 'C:\Program Files\Python310\python.exe' people_workbench.py
```

On Windows, `python3` can point to a Microsoft Store alias instead of an
installed interpreter. Check your selected interpreter with `python --version`,
`py -3 --version`, or the same explicit executable path followed by `--version`.

Open `http://127.0.0.1:8766/` if the browser does not open automatically.
Enter the Immich URL and sign in with an Immich admin account. Workbench uses
the Immich session token for API calls and keeps it in server memory only. The
password is not stored; the browser saves only the Immich URL for the next
sign-in. Existing saved connection settings are migrated to the URL and their
browser-stored API key is removed when the login page opens. The Workbench login
expires after eight hours or when the server restarts. Log out from the header
to end the current Workbench session.

Only one admin session can use a local state directory at a time. The first
successful sign-in binds that directory to an Immich server and admin account;
existing state from the API-key version is claimed by the first admin who signs
in. Use a different `--state-dir` for another server or admin, as shown below.
Immich installations that disable password login cannot use this sign-in flow
yet. Workbench binds to localhost; use an SSH tunnel for remote access for now.
Reverse-proxy and Docker/NAS setup still need their own deployment work. Do not send Immich
passwords over an untrusted HTTP link.

## Choose a state directory

By default, Workbench saves state in `.people-workbench/` next to the app.
To keep another server or account separate, choose its own directory and use
the same path whenever you launch that instance.

On Linux, macOS, or WSL:

```bash
python3 people_workbench.py --state-dir "$HOME/.local/share/people-workbench/second-server"
```

On Windows PowerShell:

```powershell
python people_workbench.py --state-dir "$env:LOCALAPPDATA\PeopleWorkbench\second-server"
```

Use storage local to the machine running Python for SQLite state. In particular,
when Windows Python reads the source from a WSL UNC path, set `--state-dir` to a
Windows-local path as above; SQLite on the UNC share can cause locking errors.
When running Python inside WSL, use a path in that distribution's Linux filesystem.

Run only one Workbench process per state directory. If another instance is
already listening on the default port, give the new instance another port too:

```powershell
python people_workbench.py --port 8767 --state-dir "$env:LOCALAPPDATA\PeopleWorkbench\second-server"
```

Open `http://127.0.0.1:8767/` for that example. Changing the state path starts with
that directory's own local queue and progress; it does not migrate existing state.

Local progress is stored in `state.sqlite3` inside the selected state directory
(`.people-workbench/` by default). The database contains names, person IDs,
skipped-round counters, Group photos settings,
current photo, deferred pass, local name drafts, and pending changes. Sync
reports are stored under the same state directory's `reports/`. The default state
directory is excluded from Git. It contains private data and should not be
inspected, reset, or published casually. The browser also stores list settings
and Group photos rectangle visibility; existing
`immichPeopleConsole.*` keys remain compatible. Group photos rebuilds its
temporary candidate index after a server restart or Immich reload. The server
binds to localhost by default.

For a walkthrough of the main views, see the [Visual tour](visual-tour.md).
For naming with photo context, see [Group photos](tour/group-photos.md).
For the full workflow and shortcuts, see the [User guide](user-guide.md).
