# Media Control for D200

Media Control for D200 is a macOS-only, local companion and Ulanzi Studio plugin for a D200. It displays the current media title, artist, artwork, and progress; sends generic media transport commands; and controls the macOS output volume and mute state. All bridge traffic stays on `127.0.0.1`; no cloud service, account, remote binding, or device discovery is used.

> **Validation status:** the implementation uses macOS MediaRemote for current-media data. MediaRemote is a private macOS framework, so compatibility is not guaranteed by Apple. Local runtime, package, Ulanzi Studio, and D200 validation is still pending. This repository makes no signing, notarization, release, or marketplace availability claim.

## Requirements

- macOS 13 or later
- Python 3.11 or later
- Node.js 20.12.2 or later for source development
- Ulanzi Studio 2.1.4 or later and a D200 for eventual device use

The bridge and plugin run on the same Mac. The plugin needs the local bridge to show current media; until then, keys show their offline or setup fallback.

## Quick path

1. Install Python dependencies and the plugin development dependencies:

   ```sh
   python3 -m pip install -r requirements.txt
   (cd com.arkamax404.mediacontrold200.ulanziPlugin && npm ci)
   ```

2. Start the bridge manually when you are ready to use it:

   ```sh
   python3 -m d200_bridge
   ```

3. Import the prepared plugin folder into Ulanzi Studio, assign actions from **Media Control for D200**, and keep the bridge running. Package preparation is described in [packaging/README.md](packaging/README.md).

The bridge stores its token at `~/Library/Application Support/GSMTCD200Controller/bridge-token`, listens only on `http://127.0.0.1:43821`, and supports `python3 -m d200_bridge --stop` and `python3 -m d200_bridge --diagnose`.

## Actions

| Action | Behavior |
|---|---|
| Now Playing | Shows current artwork, title, and artist; press to toggle playback. |
| Previous / Play-Pause / Next | Sends the corresponding generic current-media transport command. |
| Volume Up / Volume Down | Changes the macOS system output volume by 5 percentage points. |
| Mute Toggle | Toggles the macOS system output mute state. |
| Track Progress | Shows progress; press to cycle remaining, elapsed, and total time. |
| Artwork Top Left / Top Right / Bottom Left / Bottom Right | Shows one display-only quadrant of the current artwork. |

Artwork uses the current media source when MediaRemote provides it, with the bundled music icon as a fallback. The four artwork actions form a 2×2 mosaic when placed together. Volume and mute apply to the Mac's output, not to one application.

## Architecture

```text
macOS current media -> MediaRemote (private) -> Python bridge (127.0.0.1:43821)
                                                     ^
                                                     | authenticated local polling
Ulanzi D200 <- Ulanzi Studio <- plugin (Node launcher + local Python runtime)
```

The bridge exposes `GET /health`, `GET /state`, `GET /artwork/{artwork_id}`, `POST /command/{previous,toggle,next,volume-up,volume-down,mute-toggle}`, and `POST /lifecycle/stop`. Every route except health requires the per-user bearer token. Existing API behavior and action UUIDs are retained.

## Development and verification

Run mocked and local test suites only:

```sh
python3 -m unittest discover -s tests -v
(cd com.arkamax404.mediacontrold200.ulanziPlugin && npm test)
```

These suites do not start the bridge, use MediaRemote, control media playback, change audio, launch Ulanzi Studio, or connect to a D200. The [macOS CI workflow](.github/workflows/ci.yml) runs the same suites.

For the local package projection, follow [packaging/README.md](packaging/README.md). It builds a caller-owned local runtime and does not establish package launch, Studio acceptance, signing, notarization, or marketplace readiness.

## Contributing, security, and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for the macOS contribution workflow and safety boundaries. See [SECURITY.md](SECURITY.md) for private vulnerability reporting.

Project-owned material is MIT licensed; see [LICENSE](LICENSE). Third-party components retain their own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
