# Contributing to Media Control for D200

This is a macOS-only project. Read the [README](README.md) for the product boundary and validation status before changing code or documentation.

## Setup

Source development and tests require macOS 13 or later, Python 3.11 or later, and Node.js 20.12.2 or later. The macOS production packager requires Python 3.13 and Xcode command-line tools.

```sh
python3 -m pip install -r requirements.txt
(cd com.arkamax404.mediacontrold200.ulanziPlugin && npm ci)
```

`requirements.txt` is the canonical Python dependency input, not a resolved lock. Plugin dependencies are locked by `package-lock.json`; use `npm ci` for routine setup.

## Safe verification

```sh
python3 -m unittest discover -s tests -v
(cd com.arkamax404.mediacontrold200.ulanziPlugin && npm test)
```

Routine verification uses mocks and local ephemeral servers. Do not start the bridge, invoke MediaRemote, control playback, change audio, launch Ulanzi Studio, or connect to a D200 unless the work explicitly authorizes that live boundary.

## Architecture boundaries

- `d200_bridge/` is the loopback-only macOS bridge for generic current media and macOS output audio.
- `com.arkamax404.mediacontrold200.ulanziPlugin/` communicates only with Ulanzi Studio and the fixed local bridge.
- MediaRemote is private. Keep its use isolated, fail closed when unavailable, and do not imply Apple support or compatibility guarantees.
- Preserve existing API response behavior and action UUIDs.
- Keep packaging local. Do not claim or add signing, notarization, marketplace, Studio, or D200 validation without separate evidence.

## Contribution expectations

- Keep changes focused and preserve local-only boundaries.
- Add or update tests with behavioral changes; run the affected suite.
- Update user-facing documentation when setup, behavior, or validation status changes.
- Explain dependency or lockfile changes explicitly; do not include incidental upgrades.
- Keep generated files, local caches, sensitive data, and machine-specific state out of contributions.

Never include private machine data, media details, sensitive logs, or screenshots in a test, issue, or review. Follow [SECURITY.md](SECURITY.md) for vulnerability reports.
