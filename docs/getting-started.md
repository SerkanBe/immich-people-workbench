# Getting started

People Workbench currently runs as a local Python server. It needs Python 3.10
or newer and has no third-party Python dependencies.

## Run locally

```bash
cd people-workbench
python3 people_workbench.py
```

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
in. Use a different `--state-dir` for another server or admin. Immich
installations that disable password login cannot use this sign-in flow yet. Workbench binds
to localhost; use an SSH tunnel for remote access for now. Reverse-proxy and
Docker/NAS setup still need their own deployment work. Do not send Immich
passwords over an untrusted HTTP link.

Local progress is stored in `.people-workbench/state.sqlite3`. The database
contains names, person IDs, skipped-round counters, and pending changes. Sync
reports are stored under the same state directory's `reports/`. The state
directory is excluded from Git. It contains private data and should not be
inspected, reset, or published casually. The browser also stores canvas
positions. The server binds to localhost by default.

For the review workflow and shortcuts, see the [User guide](user-guide.md).
