# Review an offline copy

Create a portable copy in the source Workbench, review it on another computer,
then return saved changes for local import and explicit Pending Sync. The copy
contains private photos, names and review data, without Immich credentials.

## Create and download a copy

1. Connect to the Immich server and account you want to review. Under **Offline**,
   open **Create offline copy** and choose **Find photos**.
2. Review the photo count and size. **Size unknown** means metadata did not give
   a usable estimate; a partial estimate identifies photos with unknown sizes.
   Enter a **Copy name** if you want a label other than **Offline copy**.
3. Choose **Create offline copy**. This one action saves the selected photos on
   the Workbench server and creates the portable ZIP. You can use other pages
   while it runs. The page shows the current step, saved count and bytes.
4. When ready, choose **Download offline copy**. **Exports** appears in navigation
   when an export exists and lets you download a ready copy again.

**Cancel** stops the current work. Completed originals are retained. Return to
creation and choose **Resume** when offered. After a reload or lost response,
refresh the status, then choose **Continue** or **Resume** explicitly; the browser
never replays a transfer automatically. Prior ready exports remain available.

**Storage details** shows the server path. In Docker this is inside Workbench's
state volume, not a browser download directory. Only **Download offline copy**
saves the ZIP through your browser. The storage drive must have sufficient space;
Workbench keeps a 64 MiB reserve.

Find photos reads metadata without downloading media. It requires the optional
`asset.download` API-key permission, as does downloading originals. Preparation
includes accessible still photos containing unnamed people and other faces in
those photos for context. Videos and photos outside that selection are excluded;
named people can have partial coverage. The page's **?** help explains selection,
size estimates and copy names.

Errors identify the failed step and an HTTP status when Immich provides one.
Checking metadata does not test later original, preview or portrait downloads.
Resume keeps completed originals, including originals whose previews failed.

## Review on another computer

Extract the ZIP into a new folder. Install Python 3.10 or newer; no extra packages
are needed. From that folder, run:

```sh
python people_workbench.py
```

On Windows, use `python people_workbench.py` or `py -3 people_workbench.py`.
Choose **Open saved offline workspace**. No Immich connection, account or
credentials are needed on that computer.

Use Naming, Group photos, Investigate and available Face Review. Saved progress
persists across restart. Unavailable photos and incomplete face inventories stay
unavailable or partial. Workbench can show cached previews for original formats
that the browser cannot display and offer **Open stored original**.

The header shows **Working offline** and **Download changes**. Wait for saves to
finish, then download the small results ZIP and return it to the original
Workbench. It contains saved review changes, not photos or application code.
Keep your portable copy until its returned work is accepted at the source.
The **?** link opens Help while preserving your current workflow.

## Import offline changes

1. Open the exact Workbench that created the copy, using its original Immich
   account. Under **Offline**, choose **Import offline changes**.
2. Choose the returned changes file. Review **Ready**, **Conflict** and
   **Already imported** groups, including any additional pages. Ready groups
   start selected; exclude any you do not want to import.
3. Choose **Import selected changes**. Workbench saves accepted changes locally
   and takes a state snapshot first. It never Syncs automatically.
4. If proposed Immich changes are queued, choose **Review Pending**. Check them
   alongside existing work, then explicitly **Sync checked changes to Immich**.
   Only checked actions are applied to Immich. Accepted local reviews, progress
   and eligible preferences can be saved without creating a Pending action.

Help visits preserve the selected file, preview and exclusions until expiry or a
context change. After an expired or stale preview, review the file again. If an
import response is lost, explicitly review the same file to reconcile its saved
outcome before another import.

Another Workbench cannot import the result, even when connected to the same
Immich account. Already imported changes are not applied twice. Conflict reasons
explain the current problem; excluding a conflict does not resolve it. Reconcile
at the source or create a fresh export where required. Keep a portable copy with
unsupported overlapping continuation changes until its work is reconciled.

## State snapshots

Open **State snapshots** from the local-data link below the main navigation.
Save, list and download snapshots there. Workbench also takes one before each
actual nonempty import. Snapshots contain saved Workbench progress and media
references, not duplicated photos or credentials. The page's **?** explains their
scope.

There is no restore or delete action. Snapshots cannot undo changes already
Synced to Immich and are not photo-library backups. Unsubmitted browser text and
failed saves are not part of a snapshot.

## Existing folder copies

Older unmarked folder copies retain their original reconnect workflow. To make
one, stop Workbench and run:

```sh
python tools/export_workspace.py /path/to/new/PeopleWorkbench
```

In PowerShell, use a Windows-local destination such as `E:\PeopleWorkbench`.
Use `--state-dir PATH` for a custom source state directory. The destination must
not already exist.

Run `python people_workbench.py` from the copy and choose **Open saved offline
workspace**. To apply its Pending changes, reconnect that copy to the same
Immich server and account, review Pending and explicitly Sync. If Sync reports
conflicts, check affected people or photos in Immich, remove those items from
Pending, then review the remaining selection. Prepare a fresh snapshot after a
confirmed Sync before another trip.

The folder's progress does not import automatically into the original Workbench;
independently edited copies are not merged automatically. Keep it private: it
contains the app, photos and local review data.
