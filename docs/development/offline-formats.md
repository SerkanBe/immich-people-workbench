# Offline transfer state and formats

The transfer helpers use the Python standard library. They take an explicit
SQLite connection or destination path; importing them does not open user state
or contact Immich. Source baselines and the import ledger belong to the original
Workbench. A returned file never supplies trusted SQL, schema or before-values.

## State transactions

`Store.workflow_transaction(kind, origin)` owns one connection and one write
transaction. Nested Store helpers borrow that connection and cannot commit part
of a logical action. Workflow changes, revision triggers, action generations and
the event record commit or roll back together. Upstream reads and writes stay
outside SQLite write transactions. Sync checkpoints remain durable individual
transactions before the corresponding remote request.

`workspace_state.install_schema(db)` adds identity, revisions, tombstones,
export records, snapshots, events and acceptance records without deleting
existing workflow state. `capture_state(db)` reads one consistent view. It
includes only supported workflow tables and an explicit preference allowlist;
connection settings, arbitrary settings, error text and inventory/completeness
caches are excluded. Deletion versions remain available to detect changes that
were later reversed or requeued.

State captures use `format: people-workbench.state`, `formatVersion: 1`, a
persistent local `instanceId`, owner, state/group revisions and logical rows.
Each row has `entity`, `key`, `revision`, `deleted` and `value`. Those internal
entity names are not permission for a returned file to select database tables.
The module's fixed `SPECS` mapping defines the supported domain operation types.
Browser-only Merge canvas arrangements and unsaved input are outside server
captures; their existing browser-storage compatibility is preserved.

## Export baselines and portable state

In a protected transaction, capture state and call
`reserve_export(db, name, capture, media, inventory=...)`. The record binds a
random export ID to the originating instance, exact owner URL/user ID, media
identity and immutable state/inventory baseline. `read_known_export` verifies a
single stored baseline; `list_exports` returns metadata without materializing
all baseline payloads. `publish_export` accepts only the registry's generated
archive reference after the caller's publication checks.

`materialize_portable(capture, export, destination)` creates a fresh allowlisted
SQLite database at a new path, with a distinct local identity and immutable
origin metadata. It does not copy source free pages, unrelated settings,
registries, old snapshots or credentials. Its returned descriptor must accompany
the package. Package mode must be enforced by the server, including when the
root descriptor or matching database marker is damaged or missing.

## Results and permanent acceptance

Results use `format: people-workbench.results`, `formatVersion: 1`,
`sourceInstanceId`, `exportId`, `portableInstanceId`, `baselineSha256`,
`mediaSnapshotId` and `events`. An event has `id`, `kind` and `operations`.
Each operation has `id`, a fixed domain `type`, `key` and `value` (an object or
null for removal). Values never contain SQL or incoming Sync checkpoints.

`compute_changes(db)` derives stable changed operations and their event
identity from saved portable state. `note_results_emitted(db, result)` records
an emitted result without clearing review work. If a later change reverses an
already emitted intent back to its original baseline, the helper refuses the
unsupported overlapping continuation instead of silently dropping it. Preserve
local work and use a fresh export/review for that case.

`group_results(result, baseline)` derives atomic dependency components and the
immutable event identity for each operation. A component can change as related
new work is added; that does not make an unchanged accepted operation new.
`accepted_operation` binds its ID to canonical payload and immutable event ID.
`acceptance_status` distinguishes applied, ready and mixed conflicting work.
Exact reimports remain applied after later source edits, removal or Sync;
changed payloads under an accepted ID are rejected.

The results ZIP contains exactly one regular, stored `changes.json` member.
Canonical seekable output has 122 bytes of ZIP overhead. The complete file is
at most 16 MiB, so its UTF-8 JSON payload is at most 16 MiB minus 122 bytes.
Results have no media, runtime code or SQLite files. Producer/parser structural
bounds are separate from source inventory, domain and conflict validation.

## Snapshots, guards and import audit

`create_snapshot(db, name, reason)` stores a bounded logical capture, checksum
and media references. Reasons are `manual` and `pre-import`. Listing/downloading
snapshots does not restore state or undo Immich writes. Quota exhaustion refuses
new captures rather than deleting existing history.

For a nonempty selected import, the caller must perform final local validation,
pre-import snapshot, domain operations, guard binding and `record_import` in one
protected transaction. The helper does not apply arbitrary operations itself.
`last_import` supplies a durable receipt for reconciling a lost response; it does
not authorize replaying a mutation.

Action generations preserve imported provenance through inclusion/error changes
and source-derived edits. Explicit source removal retires the queue action but
not permanent acceptance. A later explicit native requeue has a new generation;
unverified dependencies block upstream work until explicit source verification.
`guard_token_rows` lets Pending review tokens bind the guard state without
exposing the baseline. `record_effect` records only verified own remote effects.
Per-item imported guards must be checked again before explicit selected Sync;
a successful local import is not permission to write upstream.
