"""Build the small native MediaRemote boundary with fixed macOS inputs."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def build_helper(source, output, runner=subprocess.run):
    source, output = Path(source), Path(output)
    if source.name != "MediaRemoteHelper.m" or not source.is_file():
        raise ValueError("MediaRemote helper source is missing")
    output.parent.mkdir(parents=True, exist_ok=True)
    runner(["xcrun", "--sdk", "macosx", "clang", "-fobjc-arc", "-framework", "Foundation",
            "-F", "/System/Library/PrivateFrameworks", "-framework", "MediaRemote", "-o", str(output),
            str(source)], check=True)
    if not output.is_file():
        raise RuntimeError("MediaRemote helper build did not produce an executable")
    output.chmod(output.stat().st_mode | 0o100)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    build_helper(args.source, args.output)


if __name__ == "__main__":
    main()
