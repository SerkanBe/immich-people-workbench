# Immich People Workbench

Name people faster and review incorrect face assignments in your
[Immich](https://immich.app/) library, using keyboard shortcuts and photo context.

[Getting started](#getting-started) · [User guide](docs/user-guide.md) ·
[Visual tour](docs/visual-tour.md) · [Report a bug](docs/reporting-bugs.md)

<a href="docs/images/visual-tour/name-suggestions.jpg">
  <img src="docs/images/visual-tour/name-suggestions.jpg" alt="Naming clusters with autocomplete in People Workbench" width="840">
</a>

*Naming with autocomplete. Screenshot uses fictional demo data.*

## Features

- **Fast naming:** type a name or choose an existing person with autocomplete.
- **Photo context:** identify unnamed faces in Group photos, or set difficult
  clusters aside in Investigate.
- **Face review:** accept correct faces, separate mistakes, and continue with
  the keyboard.
- **Pending review:** check queued names, merges and corrections before applying
  them to Immich.

**Changes stay local until you review Pending and explicitly Sync.** Keep a
backup of your library before applying changes.

## Getting started

You need a running Immich server and either Docker Compose or Python 3.10+.
Download the source:

```sh
git clone https://github.com/SerkanBe/immich-people-workbench.git
cd immich-people-workbench
```

Or download and extract the repository ZIP.

Choose one of these three setups:

### 1. Standalone Python

No extra Python packages are required:

```sh
python3 people_workbench.py
```

On Windows, use `python people_workbench.py` or `py -3 people_workbench.py`.
Open [http://127.0.0.1:8766/](http://127.0.0.1:8766/) if the browser does not open.
Connect to Immich using its domain/IP, or `http://localhost:2283` if it runs on
the same machine and is reachable at that address.

### 2. Standalone Docker with remote Immich

Run Workbench locally without changing your remote Immich installation:

```sh
docker compose up -d
```

The first start builds the image. Open
[http://localhost:8766/](http://localhost:8766/) and enter your **remote Immich
URL**, such as `https://photos.example.com`. Inside the container, `localhost`
means Workbench itself. For Immich on the same computer with Docker Desktop,
use `http://host.docker.internal:2283` or choose setup 3.
See [Run with Docker](docs/docker.md) for settings, updates and storage.

### 3. Add Workbench to Immich's Compose file

On the Docker host running Immich, build the image from the Workbench source
folder:

```sh
docker compose build
```

Add the [Workbench service](docs/docker.md#add-workbench-to-immichs-compose-file)
to your existing Immich Compose file, then run this from the **Immich folder**:

```sh
docker compose up -d people-workbench
```

Open [http://localhost:8766/](http://localhost:8766/) and connect to
`http://immich-server:2283` using your actual Immich service name. Each container
has its own `localhost`, even when both services share the same network.

### Connect and name a person

Connect with an [Immich API key](docs/api-key-authentication.md) (recommended),
or use an administrator account with password login enabled.
Open **Naming → Clusters**, enter a name and press `Enter`. Review the change
in **Pending**, then choose **Sync** when you are ready to apply it.

## Documentation

| I want to… | Guide |
| --- | --- |
| Set up Workbench and keep my progress | [Getting started](docs/getting-started.md) |
| Create an API key with the right permissions | [API key connection](docs/api-key-authentication.md) |
| Learn the workflows and keyboard shortcuts | [User guide](docs/user-guide.md) |
| Follow illustrated examples | [Visual tour](docs/visual-tour.md) |
| Report a problem | [Reporting bugs](docs/reporting-bugs.md) |

## Contributing

Bug reports and improvements are welcome. See [Development](docs/development/README.md)
for contributing and running checks. This project is developed with AI assistance.

## License

[AGPL-3.0](LICENSE).
