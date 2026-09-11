# Media Control for D200

Media Control for D200 brings current-media information and controls from a Mac to the Ulanzi D200. It runs locally and does not require a cloud service or account.

## At a glance

| Area | Included behavior |
|---|---|
| Now Playing | Artwork, title, artist, playback state, and optional progress |
| Media controls | Previous, Play/Pause, and Next |
| System audio | macOS output volume and mute |
| Displays | Individual keys, a four-tile artwork mosaic, and the large center display |
| Privacy | Authenticated bridge traffic restricted to `127.0.0.1` |

## What's new in v2.4.0

- Choose an independent badge color for each actionable artwork tile.
- Choose the Now Playing accent used by its playback badge and filled progress bar.
- Use refreshed PNG cover and banner artwork in the plugin listing.

The configurable badge and accent behavior was manually confirmed on the user's local setup. Local v2.1.0 package, Ulanzi Studio, and physical D200 validation also succeeded on the tested setup.

> **Compatibility note:** current-media data uses Apple's private MediaRemote framework, so Apple does not guarantee compatibility. Signing, notarization, marketplace publication or acceptance, and compatibility across other macOS, Studio, or device versions remain unproven.

## Released package setup

- macOS 13 or later
- Ulanzi Studio 2.1.4 or later
- Ulanzi D200

Import the released package into Ulanzi Studio and assign actions from **Media Control for D200**. When an action needs current-media data, Ulanzi Studio starts the package's bundled local D200 bridge automatically. The bridge and plugin run on the same Mac; bridge traffic stays on `127.0.0.1`.

### Large center display

1. Add **Setup Large Display** to a normal key on the active D200 page.
2. Select **Install**, **Repair**, or **Restore original** in its property inspector, then press the assigned key.
3. Close Ulanzi Studio only when the Setup action requests it. Reopen Studio manually after the operation completes.

Setup supports the macOS `ProfilesV2` store only. It identifies the active page from the Setup action UUID and its instance `ActionID`, then modifies only center key `3_2`. Install and repair use a hashed, expiring request, a byte-for-byte backup, atomic replacement, readback, and a receipt. Restore accepts only the matching backup lineage and the bounded Studio normalization documented by the implementation; unrelated edits fail closed. The backup is the authority for restoration.

The large view shows artwork, title, artist, playback state, and timeline progress. Ulanzi Studio may retain its macOS clock overlay because Studio can ignore the action's large-view field.

### Customize media controls

1. Select Previous, Play/Pause, Next, Volume Up, Volume Down, or Mute Toggle to choose an independent icon color.
2. Select Now Playing to show or hide its 7 px bottom progress bar and choose the accent color used by the playback badge and filled progress bar.
3. Select any artwork tile to choose its badge color and **On press** action: None, Previous, Play/Pause, Next, Volume Up, Volume Down, or Mute Toggle.

Tile actions do not replace the live 2×2 artwork mosaic. Volume and mute always control the Mac's system output; macOS does not expose the Windows-only per-source selector used by the GSMTC edition.

## Source and build prerequisites

- macOS 13 or later
- Python 3.11 or later
- Node.js 20.12.2 or later for source development

The macOS production package build additionally requires Python 3.13 and Xcode command-line tools. Package preparation is described in [packaging/README.md](packaging/README.md).

## Source development quick path

1. Install Python dependencies and the plugin development dependencies:

   ```sh
   python3 -m pip install -r requirements.txt
   (cd com.arkamax404.mediacontrold200.ulanziPlugin && npm ci)
   ```

2. Start the source bridge manually when you need to exercise it:

   ```sh
   python3 -m d200_bridge
   ```

3. For a packaged build, follow [packaging/README.md](packaging/README.md). The packaged launcher starts its bundled bridge automatically when needed.

The bridge stores its token at `~/Library/Application Support/GSMTCD200Controller/bridge-token`, listens only on `http://127.0.0.1:43821`, and supports `python3 -m d200_bridge --diagnose`.

## Actions

| Action | Behavior |
|---|---|
| Now Playing | Shows current artwork, title, artist, playback badge, and optional 7 px bottom progress bar; press to toggle playback. |
| Previous / Play-Pause / Next | Sends the corresponding generic current-media transport command. |
| Volume Up / Volume Down | Changes the macOS system output volume by 5 percentage points. |
| Mute Toggle | Toggles the macOS system output mute state. |
| Track Progress | Shows progress; press to cycle remaining, elapsed, and total time. |
| Artwork Top Left / Top Right / Bottom Left / Bottom Right | Shows one artwork quadrant and can run an optional media or system-volume action when pressed. |
| Large Now Playing | Shows current playback on the large center display at key `3_2`. |
| Setup Large Display | Safely installs, repairs, or restores the center assignment. |

Artwork uses the current media source when MediaRemote provides it, with the bundled music icon as a fallback. The four artwork actions form a 2×2 mosaic when placed together. Volume and mute apply to the Mac's output, not to one application.

Previous, Play/Pause, Next, Volume Up, Volume Down, and Mute Toggle each provide an independent icon color setting. Existing profiles retain the default `#1DB954` color.

Each artwork tile can use an independent badge color and run None, Previous, Play/Pause, Next, Volume Up, Volume Down, or Mute Toggle without replacing its live artwork quadrant. Audio actions always target the Mac's system output.

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

Source development renders Large Now Playing for parity, but **Setup Large Display never mutates profiles in source mode**. Profile setup requires the packaged runtime so it can launch the bundled helper through the trusted Node executable.

For the local package projection, follow [packaging/README.md](packaging/README.md). It builds a caller-owned local runtime. Local v2.1.0 package, Studio, and physical D200 validation succeeded on the tested setup, but that does not establish signing, notarization, marketplace publication or acceptance, or cross-version compatibility.

## Contributing, security, and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for the macOS contribution workflow and safety boundaries. See [SECURITY.md](SECURITY.md) for private vulnerability reporting.

Project-owned material is MIT licensed; see [LICENSE](LICENSE). Third-party components retain their own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
