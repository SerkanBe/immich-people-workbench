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

The Face review page lists visible named and unnamed Immich people that are not
currently excluded by pending changes. Select
one to load its assigned faces into a continuous, ordered grid. More faces load
as you scroll or choose **Load more faces**; keyboard users can use that button
to focus the next batch. Only nearby tiles are rendered. Each tile is cropped to the
face. Hovering or focusing **Photo** shows a small surrounding-image preview;
clicking **Photo** opens the larger Immich preview. `P` opens it for the focused
tile too.

Click a wrong face, or use `Enter` or `Space` on its focused tile, to select it.
`Shift`+click selects a range. `Shift`+drag selects an area, or turn on **Area
select** (`A`) to drag without holding Shift. Selection has a blue border;
Reviewed faces have a green status and border. Choose **Queue selected for
Unnamed** (`Q`) to put selected wrong matches into Pending.

Use the tile's Reviewed status button or press `R` on its focused tile to toggle
one correct face. **Mark selected Reviewed** (`M`) handles a selection together.
This status is stored only in local SQLite state. The person list
shows reviewed progress, and when the number of reviewed faces reaches Immich's
current photo count for that cluster the whole person is marked Reviewed. Newly
added Immich photos increase the total and therefore reopen the person for
review. Faces queued for Unnamed count as handled while pending; after a
successful reassignment their old review marker is removed.

**Hide selected** (`H`) removes selected faces from this view without losing the
selection; **Show hidden** (`Shift+H`) restores them. The queue button includes
the number of hidden selected faces so they cannot be queued unnoticed. **Clear
selection** (`C`) deselects all faces. Hidden faces and the selection are
session-only and clear when you choose another person or reload. The **Shortcuts
?** button, or `?`, shows the full key reference.

This action is local first. The face appears on Pending as `detach-face`, where
it can be excluded, previewed, or removed from the queue. During an explicit
Pending sync the tool creates a new unnamed Immich person and reassigns that
single face to it. The new cluster then enters the normal Unnamed naming flow.
If creating the person succeeds but reassignment fails, the newly created
person ID is retained in the queue so retrying sync reuses it instead of
creating another empty person.

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
