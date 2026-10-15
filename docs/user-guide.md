# User guide

The usual flow is to name clusters, use photo context when needed, review faces,
then check Pending. **Every Immich change waits for your review and explicit
Sync.** For illustrated steps, see the [Visual tour](visual-tour.md).

## Name people

Open **Naming → Clusters**. Type a name and press `Enter` to queue it and move
to the next cluster. Choose an existing person from autocomplete to queue a
merge. If you type an existing full name without selecting its suggestion,
Workbench asks whether you mean to create a separate person with the same name.

- `Tab` accepts the visible completion; `Up` / `Down` move through
  suggestions.
- `Page Up` / `Page Down` change the representative face.
- `Alt+S` skips the cluster to a later round.
- `Alt+I` queues it to be hidden in Immich.
- `Alt+D` moves it to **Investigate** for later.

See [Naming people](tour/naming.md) for screenshots and details.

## Use Group photos or Investigate

**Group photos** finds photos with known and unnamed faces together. Select an
unnamed cluster, enter a name or choose an existing person, then choose **Submit
Names**. Submitted choices appear in Pending. Leave uncertain faces blank;
Workbench defers that photo for another pass. Drafts and pass progress are saved
locally.

**Investigate** keeps difficult clusters in a separate list while you inspect
their Immich photo previews. Return a cluster to Naming when ready, or name it
directly from the view.

See [Group photos](tour/group-photos.md) and [Investigate](tour/investigate.md)
for the step-by-step walkthroughs.

## Review assigned faces

Open **Face review** and choose a person. Press `A` to accept a face or `D` to
dismiss it; these are local drafts. Choose **Submit review** or press
`Ctrl+Enter` (`⌘+Enter` on macOS) to save the review. Accepted faces stay
locally checked. Dismissed faces become correction requests in Pending; they are
still assigned as before in Immich until Sync.

Submit focuses the next untouched face, or opens the next person when this
person's review is complete.

To continue to another person, choose **Next person · N** or press `N` outside
fields and dialogs. Workbench waits for queued draft saves. If a save fails, it
stays on the person. Skipping does not submit drafts or mark the person
complete; saved drafts remain when you return.

See [Face review](tour/face-review.md) for selection, photo previews, filters,
completion, and all shortcuts.

## Review Pending and Sync

Open **Pending** to inspect queued names, merges, hide/restore actions, and face
corrections. Uncheck an item to leave it queued for later. Choose **Sync** and
confirm **Sync to Immich** to apply checked changes across Pending.

If a merge result is uncertain, check both people in Immich before removing or
retrying the request. Removing Pending does not undo a change already applied.
**Discard all local changes and reload** clears unsynced requests after
confirmation; it cannot undo changes already synced to Immich.

See [Pending and Sync](tour/pending.md) for the confirmation flow and recovery
details.

## Keyboard and navigation

The navigation links Clusters, Group photos, Investigate, Face review, Pending,
Named, and Ignored. Use **Menu** on a narrow screen. Each section has its own
URL; browser Back and Forward move between sections.

Press `?` or choose **Shortcuts** in Face review for its key reference. Group
photos and the main naming fields show their primary shortcuts beside the
controls.
