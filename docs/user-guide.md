# User guide

For an illustrated walkthrough of each main view, see the [Visual tour](visual-tour.md).

Start with [Getting started](getting-started.md). The usual flow is to review
unnamed people, use Merge or Investigate for difficult cases, check faces, and
then review Pending before Sync. Every Immich change requires that explicit
Sync.

## Naming and keyboard flow

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

## Merge Workbench

The Merge page shows every currently available unnamed Immich cluster on a
large spatial canvas. Drag cards to arrange a personal map, drag empty space to
pan, and use the toolbar or `Ctrl`+mouse wheel to zoom. The canvas grows when
cards are added or dragged beyond its current edge; **Fit** can show the whole
layout even for hundreds of clusters. The zoom range extends down to 0.5%.
**Zen** turns the browser content into a full-window canvas, **Hide tray** gives
it the complete width, and `Escape` leaves Zen mode. Canvas positions, zoom,
world size, and viewport location are stored in this browser. Clicking a card or
pressing `Space` only marks or unmarks it; dragging one of several marked cards
moves that selection together. Arrow keys move focus, and `Page Up` / `Page
Down` cycle through additional face samples.

`Shift` + drag on empty canvas space draws a selection rectangle; `A` keeps
area-selection mode active without holding Shift. Numbered canvas buckets are
a separate, purely organizational layer for categories such as families,
workplaces, acquaintances, or random people. Hover a face and press `1`–`9` to
assign it to that bucket; pressing the same number again removes it, while a
different number moves it directly. `0` removes it from any bucket. The tray
shows every used bucket in its own **Buckets** tab with sample faces, a count,
and an editable local label.
Clicking a bucket creates a bucket selection for moving, packing, or removing
its faces. The canvas fits that bucket's complete bounds and briefly highlights
every member, even when they are spread far apart. Merge controls remain hidden
because a bucket is not a person group. In that bucket selection, the small `×` beside a face removes it
from the bucket itself rather than merely hiding it from the selection.

Person groups remain a second, independent organizational layer. `G` explicitly
turns the current marked set into a person group. Every group appears in the
tray's separate **Person groups** tab; clicking it marks all of its faces. Only
an explicitly selected person group reveals its unnamed merge action. Selecting
the group also fits and highlights all of its canvas cards. The small `×` then
removes that face from the person group. `U`
removes all marked faces from their person groups. A face can belong to both one person group and one numbered
bucket. `P` gathers the selection into a compact local grid. Neither kind of
membership controls manual movement:
dragging an unselected card moves only that card, while dragging one of several
selected cards moves exactly the current selection. Person groups and buckets
both protect cards from automatic layouts and remain stored with the browser
canvas layout, but never become Immich operations themselves.

The collection tabs and their lists stay at the top of the tray. The faces in
the current loose selection, bucket, or person group appear below them. The
tray explains what each selection means: loose markings are temporary, buckets
are local sorting labels, and person groups are saved in this browser. The
unnamed merge action forms the final section and remains visible only for a
selected person group. It shows the surviving cluster beside **Queue this group
to Pending**. Queueing does not assign a name or change Immich; review the
operation in Pending before an explicit Sync. The `×` next to a selected face
unmarks it, removes it from the bucket, or removes it from the person group,
depending on the active selection.

The main canvas actions have direct hotkeys whenever focus is not inside an
input, select, or dialog: `A` toggles area selection, `G` groups the selection,
`V` starts person-group review,
`U` ungroups it, `P` packs it, `R` arranges similar faces around the
focus, `M` builds the global similarity map, `L` grids unbucketed cards, `F`
fits the full layout, `C` clears the current selection, `H` toggles the tray,
and `Z` toggles Zen mode. `+` and `-` zoom. The `?` button shows the complete
shortcut reference without permanently filling the toolbar with action buttons.
A short click on empty canvas space clears the current selection; dragging the
same space still pans. `Escape` also clears a selection first and leaves Zen
mode when pressed again. Neither action changes person groups or buckets.

`Arrange similar nearby` asks Immich to rank available clusters by similarity
to the focused cluster and places the selected number of results around it.
This changes only the local canvas layout: it neither selects nor merges those
clusters. Cards already assigned to a person group or bucket are skipped and
keep their position. Immich currently provides the ranking order through the People API,
not a similarity score, so the tool does not invent or display one.

**Similarity map** performs the same read-only face-ranking request for every
available cluster and combines the results into a global k-nearest-neighbor
graph. Reciprocal neighbors receive more weight, then a force-directed solver
places all otherwise-unassigned clusters so strongly connected faces tend to be nearby.
Person groups and buckets remain fixed and can act as anchors for neighboring
unassigned faces. A collision pass then moves only unassigned cards to their
nearest free positions, so map cards do not cover each other or a fixed group
or bucket.
The map operation does not change the current viewport or zoom; use `F` when
you explicitly want to fit the complete result. Because Immich exposes ranks rather than numeric embedding
distances, this is an approximation rather than a metric projection. The first
run makes roughly one local API request per available cluster and can take a
while; the server caches the graph until the Immich people list is reloaded.

`L` returns cards that belong to neither layer to a collision-free grid while
leaving person groups and buckets exactly where they are.

When a person group is confirmed, its cluster with the most photos becomes the
surviving unnamed Immich person; the other group members are queued to merge
into it. No name is assigned. The survivor is kept out of the naming queue while
those merge operations are Pending. After sync reloads Immich, the merged
unnamed person returns to the established naming flow.

**Review all person groups** (or `V`) opens a keyboard-first modal and shows every
cluster thumbnail in one group. `Right` or `Enter` confirms the group and queues
its unnamed merge, then advances immediately. `Left` skips the group without
changing it, so it remains available for manual correction on the canvas.
`Escape` closes the review. The modal does not sync Immich; confirmed groups are
still applied only through the normal Pending sync.

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
the person shows that context. The sidebar list scrolls independently; use
**Hide completed people** to show only people with faces still to review, and
**Show completed people** to bring completed people back. A person with some
submitted faces remains listed while other faces still need review. This view-only filter
resets to showing everyone after a reload. Changing sidebar pages
or searching starts its list at the top. Select a person to load their assigned
faces into a continuous, ordered grid. More faces load as you scroll or choose
**Load more faces**; keyboard users can use that button to focus the next batch.
Only nearby tiles are rendered, and each tile is cropped to the face.
**Compact** shows small crops without tile text; **Detail** enlarges them
and shows filenames. The chosen size is saved in this browser. Hover over a
compact tile for a small surrounding-image preview. In Detail mode, click the
face crop to open the larger Immich preview. Press `Space` on a focused tile in
either mode to open it. The rectangle in that preview maps Immich's face
coordinates onto the preview, even when their image dimensions differ. If the
preview has a different orientation, use **Rotate box** (`R`) to turn the
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
until you choose **Submit review**. Submission takes one click.

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

Use **Show people** to switch the Face review sidebar between all, named, and
unnamed people. Named means they already have a name in Immich; a name only
queued in Pending remains unnamed here. Search and pagination use the selected
group. Changing filters leaves the person currently open on screen.
**Hide completed people** removes fully reviewed people from the sidebar until
you choose **Show completed people**. **Reopen review** returns a completed
person to the active list while keeping accepted faces and Pending requests.
**Finish review** marks that person complete again when no faces remain
untouched or awaiting submission.

**Hide decided** (`F`) hides green and red faces, including already decided
faces loaded on later pages. It does not hide a face when you mark it after
pressing `F`: press `F` again when you are ready to hide new decisions.
**Show hidden** (`Shift+F`) brings hidden faces back. These are
view-only actions; they neither submit drafts nor change Immich. **Clear
selection** removes the blue outline. Hidden faces stay hidden if you switch
people and return, but reset on a page reload or after Pending changes refresh
the face grid. Selection is session-only; draft decisions are durable. The
**Shortcuts** button, or `?`, shows the key reference.

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
restore, or face-detach operation, keeps skip rounds and settings, clears cached face samples,
and reloads the current people and names from Immich. This does not undo
changes that were already synced to Immich.

## Pagination and navigation

Unnamed, Investigate, Pending, Named, Ignored, and the Face Review person list
use server-side pagination. The header's **Per page** selector offers 12, 24,
48, or 60 entries and stores the choice in this browser. The selected person's
face grid loads sequential server batches as you scroll. Search filtering is
applied before person-list pagination, and out-of-range pages are clamped after
queue operations remove entries.

The Merge Workbench intentionally remains unpaginated: it is one persistent
spatial canvas, so hiding most cards behind pages would break saved positions,
buckets, groups, and similarity-map context.

Each section has its own URL, such as `/merge` or `/faces`. Reloading the page
keeps the current section open, and browser Back and Forward navigate between
sections.
