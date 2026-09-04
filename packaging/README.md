# Local macOS Ulanzi Package Projection

This project produces a local macOS-only plugin package. The build and projection
steps never launch the runtime, Ulanzi Studio, a D200, media applications, or audio.

## Build prerequisites

- macOS
- Python 3.13
- Xcode command-line tools

Build into caller-owned directories outside the repository:

```sh
runtime_root="$HOME/Library/Caches/GSMTCD200Controller/package-build"
mkdir -p "$runtime_root/build" "$runtime_root/dist" "$runtime_root/package"
sh packaging/build_ulanzi_runtime_macos.sh "$runtime_root/build" "$runtime_root/dist"
python3 packaging/prepare_ulanzi_spike.py \
  --runtime-bundle "$runtime_root/dist/runtime" \
  --output-root "$runtime_root/package"
```

The build creates its hash-pinned Python virtual environment at
`$runtime_root/build/venv`; it does not install dependencies into system Python.

The source manifest keeps `src/app.js` for development. The projected manifest uses
`src/launcher.js`, which starts its bundled local D200 bridge through the extensionless
`runtime/MediaControlRuntime` target built by `ulanzi_runtime.spec`. Preparation rejects symlinks, unexpected runtime root entries,
non-macOS binary suffixes, missing licenses, unsafe asset paths, and a changed action
UUID inventory. It copies every referenced action asset and the progress property
inspector with its required SDK scripts.

The result is a local package projection only. Signing, notarization, marketplace
submission, and Ulanzi Studio acceptance/installability remain unvalidated external
work.
