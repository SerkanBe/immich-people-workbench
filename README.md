# Immich People Workbench

People Workbench is a keyboard-first, local tool for reviewing people and faces
already detected by [Immich](https://immich.app/).

The goal of this workbench is to streamline the whole process of naming faces, merging them and removing wrongly
assigned faces from people.

In this workbench the main task of naming people is done with the keyboard:
- Type in name
- Instantly get names suggested
- hit Tab to use autocomplete to select the first name
- use up/down keys to select other suggested names
- hit Enter to confirm the name
- hit Tab to move to the next face
- alt+i to ignore a face
- alt+d to mark it for further investigation later on (with more context/the whole image)

Only if you type the whole name yourself and it already exists as a person, only then you will be
asked if you're sure you want to create a new person with the same name.
The immich way is to always ask you if you want to merge... which is basically always the case.

Immich changes stay in a local Pending queue until you review and explicitly Sync them.

Yeah. That's it. I hope this helps people as much as it did help me.

## Current scope

Right now it runs with python and allows you to sign in only with your immich admin account.

You can:
- Name people quickly
- Create new people (by typing a name that doesn't exist yet)
- Merge people (by selecting the name or using a fancy canvas... which is experimental at best)
- Review persons to see if there is any wrong face in there (still needs work though)

Future plans:
- Add ways to run this tool in Docker, with its own Compose file or as a service in Immich's Compose stack
- Allow other immich accounts to login and do their naming
- Fix the issues/experimentals mentioned above... at some point, if people want that.

## Documentation

- [Getting started](docs/getting-started.md) — run locally, sign in, and understand local state.
- [User guide](docs/user-guide.md) — naming, Merge, Investigate, Face review, and Pending.
- [Development](docs/development.md) — source layout and tests.


## AI Disclaimer

This whole thing is written by AI. I just gave direction and told him what we need and what not.
It's nowhere perfect, and everyone probably needs a different tool anyway. But here we are.

I just can hope the AI didn't put a `rm -rf /` in there somewhere, that triggers on a specific date.
I developed and tested the features with my own library, and didn't lose any images... yet :D

Just make sure you have Backup(s) of your image library.


## License

Copyright (C) 2026 Serkan Bekdemir. Licensed under the
[GNU Affero General Public License v3.0](LICENSE).
