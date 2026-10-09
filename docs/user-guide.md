# User guide

For an illustrated walkthrough of each main view, see the [Visual tour](visual-tour.md).

Start with [Getting started](getting-started.md). The usual flow is to review
unnamed people in Naming, use Group photos or Investigate for difficult cases,
check faces, then review Pending before Sync. Every Immich change requires
that explicit Sync.

## Naming and keyboard flow

**Naming → Clusters** is the former Unnamed view. `/naming/clusters` is its
current URL; existing `/unnamed` bookmarks and history still open Clusters.
Its cluster cards, sort order, and shortcuts work as before.

- Type a name: autocomplete searches normal fragments of existing names.
- `Tab`: accept the visible autocomplete completion when one is shown;
  otherwise move normally to the next name field.
- `Up` / `Down`: navigate suggestions. Moving above the first suggestion
  restores the unfinished text.
- `Enter`: queue the visible name or selected existing person and focus the next
  cluster.
- `Page Up` / `Page Down`: browse representative faces. The visible sample is
  queued as the new feature photo.
- `Alt+S`: skip the current cluster and move it into the next round.
- `Alt+I`: queue the current cluster as ignored.
- `Alt+D`: move the current cluster into the persistent Investigate view.

The hint below each name field shows the main completion and submission keys.
Suggestion rows wrap long names and show their photo count underneath.

Typing an existing full name manually opens a confirmation dialog. `Enter`
confirms that a separate person with the same name is intentional; `Escape`
returns to the field. Choosing the autocomplete entry queues a merge instead.

No action changes Immich immediately. Pending operations survive restarts and
are applied only from the Pending page with the sync button. Unchecked pending
items remain in the queue. While Sync runs, the button shows that it is busy
and cannot start another Sync. A concurrent Sync request is rejected; failed
items stay Pending for review and retry.

### Group photos

**Naming → Group photos** opens `/naming/group-photos`. It searches photos
linked to visible unnamed clusters, including Investigate, and shows eligible
photos as the background scan finds them. The minimum is five detected faces
by default, adjustable from 2 to 20; archived photos are included by default.
Group photos has its own order setting. Workbench reads Immich's face records
and does not run face detection.

Click a face marker to inspect it. `Tab` and `Shift+Tab` cycle through nameable
faces and **Submit Names**, skipping named, Pending, ignored, and unassigned
faces. Type names or choose an existing person. Typing `ignore` offers an
explicit **Ignore this cluster** action; choosing it displays that text in the
field. `H` toggles the face rectangles outside a text field.

**Submit Names** or `Ctrl+Enter` (`⌘+Enter` on macOS) queues every entered name
and ignore action in Pending, then opens the next photo. Blank nameable faces
defer the photo. After the unseen pass ends, **Review deferred photos** starts
a separate pass; it never loops automatically. The current photo, drafts,
settings, and pass progress survive reloads and restarts. Only explicit Sync
on Pending writes to Immich.

See [Name faces with Group photos](tour/group-photos.md) for the full walkthrough,
shortcuts, scan filters, and help when no photos appear.

## Merging people

To merge an unnamed cluster into an existing person, choose that person
from name autocomplete. Review the proposed source and destination in
Pending, then explicitly Sync.

## Investigate difficult clusters

The Investigate view is a separate persistent list for cases that need more
context. Each entry has a larger person portrait with the usual representative
face slider (`Page Up` / `Page Down`). Beside it, up to 16 Immich photo previews
appear in a large 500–720 px viewer with previous/next controls and a thumbnail
strip. Clicking the large preview opens an even larger modal; this still uses
Immich's preview rendition rather than downloading the original. Returning an
entry puts it back into the naming queue. A button in the view heading returns
all Investigate entries to Unnamed at once after confirmation. The name field uses the same fragment
autocomplete, existing-person merge selection, duplicate-name confirmation,
and Enter-to-queue behavior as the main naming view. Queuing a rename or merge
automatically removes the entry from Investigate and moves it to Pending.

The filenames and previews in this view come from the current Immich API.

## Face review

The Face review page lists visible named and unnamed Immich people, including
people with a name or merge already waiting in Pending. A Pending label beside
the person shows that context. The sidebar list scrolls independently; its
controls fit in one row above it. A person with some submitted faces remains
listed while other faces still need review. The completed-person filter
resets to showing everyone after a reload. Changing sidebar pages
or searching starts its list at the top. The order control sorts the sidebar by
Immich photo count or update time before pagination. Each person shows Immich's
photo count and any locally saved checked-face count. While Face review is open, Workbench
counts faces in the background with read-only Immich requests. Progress appears
above the sidebar; large libraries can take time, and a failed count can be
retried. The exact face total appears after all of that person's photo pages
have been counted. Partial counts
survive a reload, so the scan can resume later. Select a person to
load their assigned faces into a continuous, ordered grid. More faces load as
you scroll or choose
**Load more faces**; keyboard users can use that button to focus the next batch.
Only nearby tiles are rendered, and each tile is cropped to the face.
**Compact** shows small crops without tile text; **Detail** enlarges them
and shows filenames. The chosen size is saved in this browser. Hover over a
compact tile for a small surrounding-image preview. In Detail mode, click the
face crop to open the larger Immich preview. Press `Space` on a focused tile in
either mode to open it, and press `Space` again to close it. The rectangle in
that preview maps Immich's face coordinates onto the preview, even when their
image dimensions differ. If the preview has a different orientation, use
**Rotate box** (`R`) to turn the
rectangle 90° clockwise at a time until it lines up. The rotation resets for
each photo and does not change Immich data. Use **Hide box** (`H`) when it covers
a small face. Click the photo to zoom in or return to fit, or use Ctrl+mouse-wheel
to adjust zoom around the pointer. Drag the enlarged photo to pan. The `+` and `-`
keys adjust zoom, and `0` resets it to 100%.

Inside that photo preview, **Accept face** (`A`) and **Dismiss face** (`D`)
change only the current face's local draft decision, then advance to the next
visible face. Use Left to return to a face and press the same decision again to
reset it. Left/Right moves through the currently visible faces; `S` skips to
the next one without changing a decision. The lightbox fetches the next page
as needed and stays on the last face when there is no next face. If it cannot
load that page, the marked photo stays open and Right retries. Closing the
lightbox returns grid focus to its current face.
Hidden faces and faces already queued in Pending are outside this sequence.

Use the arrow keys to move focus between face tiles; moving focus clears any
multi-face selection. Hold `Shift` with an arrow key to extend or shrink a
selection from its starting face. **Accept face** (`A`) marks the focused
face green; **Dismiss face** (`D`) marks it red. Press the same decision again
to reset it. You can also click tiles or use `Enter` to select a
focused tile, and `Shift`+click to select a range. The
same actions apply to the selected faces when a selection exists; selection has
a blue outline. Rapid key presses are saved in order, even if you choose another
person before saving finishes. Pending Sync waits for local decisions to finish
saving. These decisions are drafts saved in local SQLite; once saving finishes,
you can reload without losing them. They do not change Immich or enter Pending
until you choose **Submit review**. Submission takes one click, or press
`Ctrl`+`Enter` (`⌘`+`Enter` on macOS) while working in Face review.

Submission saves accepted faces as locally reviewed and adds each dismissed face's
reassignment to Pending. Dismissed faces then leave this person's Face review grid;
the current assignment still exists in Immich until Sync. The Pending list holds
the *change request*, where you can preview, exclude, or remove it. Removing the
request brings the face back into Face review. The counts distinguish accepted
faces, dismissed faces waiting for submission, changes awaiting Sync, and untouched
faces. Progress counts distinct assigned faces, even when several appear in one
photo. The exact total is available after all of that person's photo pages have
loaded; until then the sidebar keeps the person in the review list. The local
face count is reused on later visits and invalidated when Immich reports a
changed photo count or person update. Opening a person also detects new faces
on loaded photos. On first use after this counting change, saved decisions remain,
but each person's photo pages must load once before they can appear completed.
New Immich photos reopen a person for review.

The magnifying glass opens a search field. While a query is entered, the field
stays visible alongside other controls; clear the query, then close the field
with the icon or `Escape`. The funnel icon opens the All / Named / Unnamed
filter, and the order icon opens the sort choices. The green check is a separate
toggle for completed people, so it works with any name filter. Active icons
show when a query, filter, or nondefault order is in effect. `Escape` closes
an open choice panel; it leaves an active search visible. Named means they
already have a name in Immich; a name only
queued in Pending remains unnamed here. Search and pagination use the selected
group. Changing filters leaves the person currently open on screen. The green
check removes fully reviewed people from the sidebar until pressed again.
**Reopen review** returns a completed
person to the active list while keeping accepted faces and Pending requests.
**Finish review** marks that person complete again when no faces remain
untouched or awaiting submission.

Submitted accepted faces are hidden from the working grid by default. New draft
decisions stay visible until **Submit review**; submitted dismissals remain in
Pending. **Show checked** reveals submitted accepted faces, and **Hide checked**
returns to the working grid. **Hide decided** (`F`) can also hide current green
and red drafts. It does not hide a face when you mark it after pressing `F`:
press `F` again when you are ready to hide new decisions. **Show hidden**
(`Shift+F`) brings manually hidden faces back. These are
view-only actions; they neither submit drafts nor change Immich. **Clear
selection** removes the blue outline. Hidden faces stay hidden if you switch
people and return, but reset on a page reload or after Pending changes refresh
the face grid. Selection is session-only; draft decisions are durable. The
**Shortcuts** button, or `?`, opens a dialog with the key reference; press
`Escape` to close it.

At explicit Pending Sync, dismissed faces are reassigned before a pending name or
merge for their source person is applied. If a wrong-face correction fails, is
excluded from Sync, or is still an unsubmitted draft, the dependent person
change stays Pending rather than carrying the wrong face into its destination.
Successful reassignment creates a new unnamed Immich person for that face, which
then enters the usual Unnamed naming flow. If creation succeeds but reassignment
fails, the new person ID is retained so a retry does not create another empty
person.

The Pending page also has **Discard all local changes and reload**. After an
explicit confirmation it deletes every unsynced pending rename, merge, hide,
restore, or face-detach operation. It keeps skip rounds and settings, clears
cached face samples and Group photos drafts, restarts the Group photos pass,
then reloads the current people and names from Immich. This does not undo
changes that were already synced to Immich.

## Pagination and navigation

Clusters, Investigate, Pending, Named, Ignored, and the Face Review person list
use server-side pagination. The header's **Per page** selector offers 12, 24,
48, or 60 entries and stores the choice in this browser. The selected person's
face grid loads sequential server batches as you scroll. Search filtering is
applied before person-list pagination, and out-of-range pages are clamped after
queue operations remove entries.

The Unnamed, Investigate, Face review, Named, and Ignored people lists each have
an **Order** selector with most or fewest photos first, plus update-time and
random options. The selection carries between views and is applied before
pagination. Pending is a list of queued operations, so it keeps its own review
order.

Each section has its own URL, such as `/naming/clusters`, `/naming/group-photos`,
or `/faces`. Reloading the page
keeps the current section open, and browser Back and Forward navigate between
sections.
