# Immich People Workbench

Local, keyboard-first review tool for people already detected by Immich. Use it
to name clusters, merge clusters belonging to the same person, investigate
difficult cases, and find faces assigned to the wrong person. It reads current
Immich data through the API.

All Immich changes first enter a persistent local Pending queue. Naming,
merging, hiding, restoring, and face reassignment reach Immich only through an
explicit Sync on the Pending page. Uncertain matches remain available for
review; the app does not automatically decide that two clusters are one person.

## Start

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
in. Use a different `--state-dir` for another server or admin. Immich installations
that disable password login cannot use this sign-in flow yet. Workbench binds
to localhost; use an SSH tunnel for remote access for now. Reverse-proxy and
Docker/NAS setup still need their own deployment work. Do not send Immich
passwords over an untrusted HTTP link.

Local progress is stored in `.people-workbench/state.sqlite3`. The database contains names,
person IDs, skipped-round counters, and pending changes. Sync reports are stored
under the same state directory's `reports/`. The state directory is excluded from Git.
It contains private data and should not be inspected, reset, or published casually.
The browser also stores canvas positions. The server binds to localhost by default.

## Pagination

Unnamed, Investigate, Pending, Named, Ignored, and the Face Review person list
use server-side pagination. The header's **Per page** selector offers 12, 24,
48, or 60 entries and stores the choice in this browser. The selected person's
face grid has its own viewport-calculated page size. Search filtering is applied
before pagination, and out-of-range pages are clamped after queue operations
remove entries. Face selections remain marked while moving between that
person's face pages.

The Merge Workbench intentionally remains unpaginated: it is one persistent
spatial canvas, so hiding most cards behind pages would break saved positions,
buckets, groups, and similarity-map context.

Each section has its own URL, such as `/merge` or `/faces`. Reloading the page
keeps the current section open, and browser Back and Forward navigate between
sections.

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
unnamed merge action forms the final section and remains visible only for a
selected person group.

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

**Review person groups** (or `V`) opens a keyboard-first modal and shows every
cluster thumbnail in one group. `Right` or `Enter` confirms the group and queues
its unnamed merge, then advances immediately. `Left` skips the group without
changing it, so it remains available for manual correction on the canvas.
`Escape` closes the review. The modal does not sync Immich; confirmed groups are
still applied only through the normal Pending sync.

## Keyboard flow

- Type a name: autocomplete searches normal fragments of existing names.
- `Tab`: accept the grey autocomplete completion when one is visible;
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

Typing an existing full name manually opens a confirmation dialog. `Enter`
confirms that a separate person with the same name is intentional; `Escape`
returns to the field. Choosing the autocomplete entry queues a merge instead.

No action changes Immich immediately. Pending operations survive restarts and
are applied only from the Pending page with the sync button. Unchecked pending
items remain in the queue.

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
one to load its assigned faces. The page size is calculated separately from the
available grid width and viewport height (with a 5×4 fallback), so complete rows
fit without body scrolling. The grid reserves the calculated height, keeping the
pagination controls in the same place even on a short final page. Each tile is cropped to the face;
the **Photo** button opens the surrounding Immich preview for context. Mark one
or more wrong matches and choose **Queue selected for Unnamed**.

Correct faces can be checked as **Reviewed** or toggled with `R` while their tile
has focus. This status is stored only in the local SQLite state. The person list
shows reviewed progress, and when the number of reviewed faces reaches Immich's
current photo count for that cluster the whole person is marked Reviewed. Newly
added Immich photos increase the total and therefore reopen the person for
review. Faces queued for Unnamed count as handled while pending; after a
successful reassignment their old review marker is removed.

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

## Test

```bash
python3 -m unittest discover -s tests -p 'test_people_workbench.py' -v
```

## License

Copyright (C) 2026 Serkan Bekdemir. People Workbench is licensed under the
[GNU Affero General Public License v3.0](LICENSE).
