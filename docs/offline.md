# Work offline with an Immich copy

An offline workspace lets you review a prepared, local copy of selected Immich
photos while the notebook is disconnected. Workbench saves originals in its
state directory and keeps offline review separate from the connected library.

## Prepare the workspace

1. Connect to the Immich server and account you want to review.
2. Open **Offline workspace** and choose **Check photos & size**. This reads
   metadata only. Review the unique photo count and estimated storage before
   downloading; photos with unknown sizes are listed separately.
3. Choose **Download & prepare**. Keep Workbench open while it downloads. Use
   **Cancel** to stop; reopen the dialog and choose **Resume download**
   to continue later. Completed files are kept.

Photos are saved on the computer running Workbench, under its state directory
(default: `.people-workbench/offline/`). The dialog shows the configured path;
in Docker it is inside the state volume, normally `/data/offline/`. This is not
a browser download. Only one prepared workspace is shown; there is no workspace
list, ZIP download or import-back feature.

Preparation includes accessible still photos containing visible unnamed-cluster
faces, including their named face context. It skips videos, hidden, locked,
trashed, deleted, and other photos outside that selection; it does not copy the
whole library. Named-person coverage can be partial, and Face Review reports
that rather than treating the copy as a complete inventory. Immich remains
needed for unavailable photos and videos. Original image formats are kept as
downloaded; if a browser cannot display one, Workbench uses its cached preview
and offers **Open stored original**.

The optional `asset.download` API-key permission is used only to prepare or
download originals. Everyday connected features do not require it. An API key
without this permission can still connect and use normal Workbench features.

## Move to a notebook

After the workspace is ready, stop Workbench. From the source folder, export a
self-contained portable folder. Choose a new destination on the drive you will
move to the notebook:

```bash
python tools/export_workspace.py /path/to/new/PeopleWorkbench
```

On Windows PowerShell, use a Windows-local destination, for example:

```powershell
python tools/export_workspace.py E:\PeopleWorkbench
```

To export from a custom state directory, add `--state-dir PATH`. The exporter
refuses an existing destination and checks the saved snapshot before copying.
Keep Workbench stopped until export finishes. Treat the exported folder as the
one active copy: later edits to the source and exported copy are not merged.
The resulting folder contains the app, database, downloaded photos, and local
review data; treat the whole copy as private and protect or delete it as you
would the originals.

Install Python 3.10 or newer on the notebook; Workbench has no third-party
Python requirements. From the copied folder, start the app with `python
people_workbench.py` (or `py -3 people_workbench.py` in PowerShell). Choose
**Open saved offline workspace** on the login page. An explicit offline open
starts the saved snapshot; login or reconnect does not silently switch modes.
Offline opening requires a local Python instance bound to loopback (the default).
It is unavailable through remote access or a container listening on all interfaces.

## Review and reconnect

Offline mode supports photo context for unnamed clusters and available named
person context. Local drafts and review progress are saved in the copied state.
Pending changes remain local: **Sync** is unavailable offline. To apply them,
connect to the same Immich URL and account, review Pending again, then choose
**Sync** yourself.

After a confirmed Sync, prepare a fresh snapshot before your next offline trip.

Changes made on the connected server after preparation can conflict with the
saved snapshot. Reconnect to the same URL and account. Workbench blocks Sync
when included Pending work conflicts and identifies affected people or
photos. Review those changes in Immich, remove the affected items from Pending,
then review the remaining Pending before choosing **Sync**. A
copied workspace is a private working copy, not a replacement for a current
Immich backup.
