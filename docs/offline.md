# Work offline with portable photos

Use an **Offline workspace** to prepare selected Immich photos on the Workbench
server, review them on another computer, then return your saved changes to the
original Workbench. A portable ZIP contains private photos, names, and review
data, but no Immich credentials. Share and store it with care.

## Prepare photos and create a portable ZIP

1. Connect to the Immich server and account you want to review.
2. Open **Offline workspace** and choose **Check photos & size**. Review the
   unique photo count, estimated size, and the save path shown for the Workbench
   server. This check reads metadata; it does not download photos. An API key
   needs the `asset.download` permission even for this check. See [API key
   connection](api-key-authentication.md).
3. Choose **Download & prepare**. The Workbench saves the selected originals on
   its server. In Docker, the path is inside the Workbench state volume; it is
   not a folder your browser downloads to. Keep the Workbench available while
   preparation runs. Choose **Cancel** to stop a download and **Resume download**
   to continue it later. Completed files are kept.
4. When the workspace is ready, enter a **Workspace name**, choose **Create
   portable ZIP**, wait for it to finish, then choose **Download portable ZIP**.
   You can download a ready ZIP later from **Portable exports**. This browser
   download contains the app, selected photos, and saved review state. It is
   private but contains no credentials.

If preparation fails, its message identifies the step (original, preview,
thumbnail, or local storage) and includes an HTTP status when Immich supplies
one. Checking metadata does not test those later downloads. Original downloads
require `asset.download`; an individual item can still be unavailable. A missing
size estimate means the check could not obtain usable size metadata.
Space checks use the drive containing Workbench's state directory and retain a
64 MiB reserve. **Resume download** keeps completed originals, including an
original whose preview failed; the saved count includes those originals even
while the workspace is incomplete.

Preparation includes selected still photos with unnamed detected people and
other faces in those photos for context. It excludes videos and photos outside
the selection. A named person may have only partial photo coverage, so
unavailable photos and incomplete face inventories stay unavailable or marked
partial. If a browser cannot display an original image format, Workbench can
show its cached preview and offer **Open stored original**.

## Review on another computer

Extract the ZIP into a new local folder. Install Python 3.10 or newer; no extra
packages are required. From that folder, run:

```sh
python people_workbench.py
```

On Windows, use `python people_workbench.py` or `py -3 people_workbench.py`.
Choose **Open saved offline workspace**. It opens for a browser on that
computer; the computer does not need Immich, an Immich account, or credentials.

Use Naming, Group photos, Investigate, and available Face Review. Your saved
progress stays in this portable copy across restarts. Wait for saves to finish
before creating a results file: **Only saved changes are included**. Videos,
unselected photos, and unavailable people or faces still require the original
Immich library.

When ready to return your work, choose **Download offline changes** and send
that small results ZIP back to the person using the original Workbench. The
results file contains review changes, not photos or the app.

After returning results, some overlapping edits, such as undoing or regrouping
previously returned work, require a fresh export. Workbench keeps your saved
portable work and explains when it cannot return a continuation. Keep that
copy until you have reconciled the work at the source.

## Import results at the original Workbench

Open the exact Workbench that created the portable ZIP, using its original
Immich account. Under **Bring back offline changes**, choose the results file
in **Result ZIP**. Review the groups labeled **Ready**, **Conflict**, or
**Already imported**. Conflicts stay unchanged; their reason explains what
needs attention. Reconcile the issue at the source or prepare a fresh export
before returning further changes. Already imported work is not applied twice.

Select the ready changes you want and choose **Import selected changes**. This
saves them locally and records a state snapshot before import. It does not
change Immich. Review the imported items with any other work in **Pending
changes**, then choose **Sync checked changes to Immich** when you are ready.
Only checked items apply to Immich.

The result belongs to its originating Workbench and account. Another Workbench,
even if connected to the same Immich account, cannot import it. If you exclude
a conflict, that change remains unresolved in the portable results; import does
not choose a winner for you.

## State snapshots

Workbench saves a state snapshot before every import. To save another one, open
**State snapshots** and choose **Save state snapshot**. You can list and
download saved snapshots there. They contain Workbench progress, not photos or
credentials. Snapshots cannot be restored from this screen and do not undo
changes already synced to Immich; they are not a photo-library backup.

## Existing folder copies

An older offline folder made with `tools/export_workspace.py` is separate from the portable ZIP workflow above. To make a folder copy, stop Workbench and run:

```sh
python tools/export_workspace.py /path/to/new/PeopleWorkbench
```

In PowerShell, use a Windows-local destination, such as `E:\PeopleWorkbench`.
Add `--state-dir PATH` when the source uses a custom state directory. The
destination must not already exist.

To use an existing unmarked folder copy, run `python people_workbench.py`
from that folder and choose **Open saved offline workspace**. To apply its
Pending changes, reconnect that copy to the same Immich server and account,
review Pending, and explicitly Sync. If Sync reports conflicts, check the
affected people or photos in Immich, remove those items from Pending, then
review and Sync the remaining items. After a confirmed Sync, prepare a fresh
snapshot before another trip. The folder's local progress does not import into
the original Workbench, and changes in the source and copy are not merged
automatically. Keep the folder private; it contains the app, photos, and local
review data. It is not a replacement for an Immich backup.
