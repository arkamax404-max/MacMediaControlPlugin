#!/usr/bin/env sh
set -eu

# Builds only a caller-owned local runtime; it never launches the runtime or devices.
if [ "$(uname -s)" != "Darwin" ]; then
    printf '%s\n' 'macOS runtime builds require Darwin' >&2
    exit 1
fi

python3.13 -I -s -m venv "$1/venv"
PYTHON="$1/venv/bin/python"

"$PYTHON" -I -s -m pip install --require-hashes \
    -r packaging/requirements-ulanzi-bootstrap.lock
"$PYTHON" -I -s -m pip install --require-hashes --no-build-isolation \
    -r packaging/requirements-ulanzi-runtime.lock
"$PYTHON" -I -s packaging/build_mediaremote_helper.py \
    --source d200_bridge/native/MediaRemoteHelper.m --output "$1/MediaRemoteHelper"
MEDIAREMOTE_HELPER="$1/MediaRemoteHelper" \
"$PYTHON" -I -s -m PyInstaller --noconfirm --clean \
    --workpath "$1/pyinstaller" --distpath "$2" packaging/ulanzi_runtime.spec
mv "$2/runtime/_internal/MediaRemoteHelper" "$2/runtime/MediaRemoteHelper"
