# Immich People Workbench

People Workbench is a keyboard-first, local tool for reviewing people and faces
already detected by [Immich](https://immich.app/). It helps you work through
large photo libraries without making automatic identity decisions.

Name unnamed people, merge clusters that belong together, investigate uncertain
matches with photo context, and review faces assigned to the wrong person. A
spatial Merge canvas helps organize unnamed clusters.

**Your changes stay local until you approve them.** Naming, merging, hiding,
restoring, and face reassignment enter a persistent Pending queue. Immich is
changed only when you explicitly review and Sync that queue.

## Current scope

People Workbench runs with Python and signs in through an Immich admin account.
It stores review progress locally. Docker/NAS packaging and access for non-admin
Immich users are planned, but are not available yet.

## Documentation

- [Getting started](docs/getting-started.md) — run locally, sign in, and understand local state.
- [User guide](docs/user-guide.md) — naming, Merge, Investigate, Face review, and Pending.
- [Development](docs/development.md) — source layout and tests.

## License

Copyright (C) 2026 Serkan Bekdemir. Licensed under the
[GNU Affero General Public License v3.0](LICENSE).
