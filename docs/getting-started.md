# Getting started

People Workbench runs locally with Python or in its own Docker container. This
guide starts the Python version; see [Run with Docker](docker.md) for Compose.

## What you need

- Python 3.10 or newer. The app has no third-party Python dependencies.
- An Immich API key with the [required
  permissions](api-key-authentication.md), or an Immich administrator account
  with password login enabled.
- Git to clone the source. You can use a ZIP download instead.

## Download and launch

Clone the repository and enter its folder:

```bash
git clone https://github.com/SerkanBe/immich-people-workbench.git
cd immich-people-workbench
```

Or download the ZIP, extract it, and open a terminal in the folder containing
`people_workbench.py` and `web/`.

On Linux, macOS, or WSL:

```bash
python3 people_workbench.py
```

On Windows PowerShell:

```powershell
python people_workbench.py
```

If Windows selects the wrong Python, try `py -3 people_workbench.py`. Open
[http://127.0.0.1:8766/](http://127.0.0.1:8766/) if the browser does not open
automatically.

## Connect to Immich

The first connection offers `http://localhost:2283`; a saved URL takes
precedence. To prefill another address, launch with
`python3 people_workbench.py --immich-url https://photos.example.com`, or set
`PEOPLE_WORKBENCH_IMMICH_URL`. This does not connect automatically.

Choose **API key** (recommended) and enter your Immich server URL and key. An
Immich administrator can instead choose **Admin login** if password login is
enabled. Workbench checks the account and permissions before connecting. See
[Create an API key](api-key-authentication.md) if you need one.

The key or password stays in server memory. The browser saves only the Immich
URL. The local login ends after eight hours, when you log out, or when Workbench
restarts; connect again to continue.

## Try a first action

Open **Naming → Clusters**, type a name, and press `Enter`. To merge the cluster
into an existing person, choose that person from the suggestions. The action
appears in **Pending** and has not changed Immich. Review the checked items and
choose **Sync** only when you are ready to apply them.

For other tasks, see the [User guide](user-guide.md) or the [illustrated
tour](visual-tour.md).

## Keep progress and connect again

Workbench stores names, drafts, review progress, and Pending in
`.people-workbench/` beside the app by default. The state directory is bound to
the normalized Immich URL and account ID. Use the same directory and account to
continue; choose a separate `--state-dir` for another account or server. A new
state directory starts fresh and does not import another directory's progress.

Keep SQLite state on storage local to the machine running Workbench. For
example, Windows Python launched from a WSL source path should use a
Windows-local state path:

```powershell
python people_workbench.py --state-dir "$env:LOCALAPPDATA\PeopleWorkbench\home"
```

Credentials are not saved, so reconnect after a restart. Local drafts and
Pending remain in the state directory. Review Pending before Sync after
reconnecting.

Workbench listens on localhost by default. To use it remotely, connect through
an SSH tunnel or configure Docker with HTTPS and a trusted proxy as described in
[Run with Docker](docker.md). Restrict access to your private network; do not
send credentials over untrusted HTTP.
