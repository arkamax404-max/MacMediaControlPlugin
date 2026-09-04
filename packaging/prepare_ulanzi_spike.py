import argparse
import json
import shutil
import stat
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath


PLUGIN_FOLDER = "com.arkamax404.mediacontrold200.ulanziPlugin"
PACKAGE_NAME = "media-control-for-d200"
PORTED_ACTION_SUFFIXES = (
    "nowplaying", "previous", "toggle", "next", "volume-up", "volume-down",
    "mute-toggle", "progress", "artwork-top-left", "artwork-top-right",
    "artwork-bottom-left", "artwork-bottom-right",
)
RUNTIME_ASSET_FILES = ("assets/music.svg", "assets/offline.svg")
PROPERTY_INSPECTOR_FILES = (
    "property-inspector/progress/inspector.html",
    "property-inspector/progress/inspector.js",
)
PROPERTY_INSPECTOR_VENDOR_FILES = (
    "vendor/ulanzi-sdk/html/js/constants.js",
    "vendor/ulanzi-sdk/html/js/eventEmitter.js",
    "vendor/ulanzi-sdk/html/js/timers.js",
    "vendor/ulanzi-sdk/html/js/utils.js",
    "vendor/ulanzi-sdk/html/js/ulanziApi.js",
)
REQUIRED_RUNTIME_FILES = (
    "MediaControlRuntime",
    "MediaRemoteHelper",
    "_internal/licenses/project/LICENSE",
    "_internal/licenses/project/THIRD_PARTY_NOTICES.md",
    "_internal/licenses/cpython/LICENSE.txt",
    "_internal/licenses/pyinstaller/COPYING.txt",
    "_internal/licenses/plugin-common-python/LICENSE",
    "_internal/licenses/websocket-client/LICENSE",
)
RUNTIME_ROOT_ENTRIES = frozenset(("MediaControlRuntime", "MediaRemoteHelper", "_internal"))
WINDOWS_RUNTIME_SUFFIXES = (".exe", ".dll", ".pyd")
# PyInstaller's macOS framework payload requires one of these closed, relative
# symlink sets. Python 3.9 names its framework and executable Python3; Python
# 3.13 names both Python. No other runtime symlinks are permitted.
PYINSTALLER_MACOS_PYTHON_SYMLINKS = {
    "_internal/Python3": ("Python3.framework/Versions/3.9/Python3", True),
    "_internal/Python3.framework/Python3": ("Versions/Current/Python3", True),
    "_internal/Python3.framework/Resources": ("Versions/Current/Resources", False),
    "_internal/Python3.framework/Versions/Current": ("3.9", False),
    "_internal/Python": ("Python.framework/Versions/3.13/Python", True),
    "_internal/Python.framework/Python": ("Versions/Current/Python", True),
    "_internal/Python.framework/Resources": ("Versions/Current/Resources", False),
    "_internal/Python.framework/Versions/Current": ("3.13", False),
}


def is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def exact_source_path(root, reference, prefix):
    path = PurePosixPath(reference) if isinstance(reference, str) else None
    if (path is None or path.is_absolute() or not path.parts
            or path.parts[0] != prefix or ".." in path.parts):
        raise ValueError(f"Experimental manifest contains an unsafe {prefix} path")
    current = root
    for part in path.parts:
        matches = [item for item in current.iterdir() if item.name == part]
        if len(matches) != 1:
            raise ValueError(f"Package path is missing or has incorrect case: {reference}")
        current = matches[0]
        if current.is_symlink():
            raise ValueError(f"Package path must not be a symbolic link: {reference}")
    if not current.is_file():
        raise ValueError(f"Package path is not a file: {reference}")
    return current


def validate_runtime_symlink(root, candidate, relative):
    expected = PYINSTALLER_MACOS_PYTHON_SYMLINKS.get(relative)
    if expected is None:
        raise ValueError(f"Inventory contains a symbolic link: {relative}")
    target, expects_file = expected
    if candidate.readlink().as_posix() != target:
        raise ValueError(f"PyInstaller symbolic link has an unexpected target: {relative}")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"PyInstaller symbolic link is broken: {relative}") from error
    if not is_within(resolved, root):
        raise ValueError("PyInstaller symbolic link escapes the runtime root")
    if expects_file and not resolved.is_file():
        raise ValueError(f"PyInstaller symbolic link must resolve to a file: {relative}")
    if not expects_file and not resolved.is_dir():
        raise ValueError(f"PyInstaller symbolic link must resolve to a directory: {relative}")


def inventory_files(root):
    root = root.resolve()
    files = []
    for candidate in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = candidate.relative_to(root).as_posix()
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            validate_runtime_symlink(root, candidate, relative)
            continue
        if candidate.is_file():
            files.append(relative)
        elif not candidate.is_dir():
            raise ValueError(f"Inventory contains a nonregular path: {relative}")
    return tuple(files)


def validate_runtime_bundle(runtime_bundle):
    if not runtime_bundle.is_dir() or runtime_bundle.is_symlink():
        raise ValueError("Runtime bundle must be a real directory")
    entries = {entry.name for entry in runtime_bundle.iterdir()}
    if entries != RUNTIME_ROOT_ENTRIES:
        raise ValueError("Runtime bundle must use the macOS MediaControlRuntime layout")
    files = inventory_files(runtime_bundle)
    missing = [name for name in REQUIRED_RUNTIME_FILES if name not in files]
    if missing:
        raise ValueError(f"Runtime bundle is incomplete: {', '.join(missing)}")
    executable = runtime_bundle / "MediaControlRuntime"
    if not executable.is_file() or not executable.stat().st_mode & stat.S_IXUSR:
        raise ValueError("MediaControlRuntime must be an executable macOS runtime")
    helper = runtime_bundle / "MediaRemoteHelper"
    if not helper.is_file() or not helper.stat().st_mode & stat.S_IXUSR:
        raise ValueError("MediaRemoteHelper must be an executable macOS helper")
    windows_files = [name for name in files if name.lower().endswith(WINDOWS_RUNTIME_SUFFIXES)]
    if windows_files:
        raise ValueError("Runtime bundle contains Windows binaries")
    return files


def validate_source_manifest(manifest):
    if manifest.get("CodePath") != "src/app.js":
        raise ValueError("Source manifest must retain the functional Node entrypoint")
    platforms = tuple(item.get("Platform") for item in manifest.get("OS", [])
                      if isinstance(item, dict))
    if platforms != ("macos",):
        raise ValueError("Source manifest must declare only macOS")
    expected = tuple(f"{manifest.get('UUID')}.{suffix}" for suffix in PORTED_ACTION_SUFFIXES)
    actual = tuple(action.get("UUID") for action in manifest.get("Actions", []))
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError("Source manifest action UUID inventory is not approved")
    return expected


class _ScriptReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attributes):
        if tag.lower() == "script":
            source = dict(attributes).get("src")
            if source:
                self.sources.append(source)


def prepare_package(plugin_source, runtime_bundle, output_root, repo_root):
    plugin_source = Path(plugin_source).resolve()
    runtime_bundle = Path(runtime_bundle).resolve()
    output_root = Path(output_root).resolve()
    repo_root = Path(repo_root).resolve()
    if is_within(output_root, repo_root) or is_within(repo_root, output_root):
        raise ValueError("Output root must be outside the repository")
    if not output_root.is_dir() or any(output_root.iterdir()):
        raise ValueError("Output root must be an existing empty directory")
    if plugin_source.name != PLUGIN_FOLDER:
        raise ValueError("Unexpected plugin source folder")
    validate_runtime_bundle(runtime_bundle)

    manifest_path = plugin_source / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    approved_uuids = validate_source_manifest(manifest)
    if not (plugin_source / "src" / "launcher.js").is_file():
        raise ValueError("Launcher spike is missing")
    package = json.loads((plugin_source / "package.json").read_text("utf-8"))
    if package.get("name") != PACKAGE_NAME or package.get("type") != "module":
        raise ValueError("Source package metadata does not match the launcher identity")
    minimal_package = {
        key: package[key]
        for key in ("name", "version", "private", "type", "engines")
        if key in package
    }
    manifest["CodePath"] = "src/launcher.js"
    ported_order = {f"{manifest['UUID']}.{suffix}": index
                     for index, suffix in enumerate(PORTED_ACTION_SUFFIXES)}
    manifest["Actions"] = sorted(manifest["Actions"], key=lambda action: ported_order[action["UUID"]])
    if tuple(action["UUID"] for action in manifest["Actions"]) != approved_uuids:
        raise ValueError("External projection must preserve all action UUIDs")
    progress = next(action for action in manifest["Actions"]
                    if action.get("UUID") == f"{manifest['UUID']}.progress")
    if progress.get("PropertyInspectorPath") != PROPERTY_INSPECTOR_FILES[0]:
        raise ValueError("Progress property inspector path is missing")
    asset_references = {
        manifest.get("Icon"),
        manifest.get("CategoryIcon"),
        *RUNTIME_ASSET_FILES,
        *(action.get("Icon") for action in manifest["Actions"]),
        *(state.get("Image") for action in manifest["Actions"]
          for state in action.get("States", []) if isinstance(state, dict)),
    }
    for reference in asset_references:
        exact_source_path(plugin_source, reference, "assets")

    inspector = exact_source_path(plugin_source, PROPERTY_INSPECTOR_FILES[0],
                                  "property-inspector")
    parser = _ScriptReferences()
    parser.feed(inspector.read_text("utf-8"))
    resolved_scripts = []
    inspector_parent = PurePosixPath(PROPERTY_INSPECTOR_FILES[0]).parent
    for source in parser.sources:
        source_path = PurePosixPath(source)
        if source_path.is_absolute():
            raise ValueError("Property inspector contains an unsafe script path")
        parts = []
        for part in (*inspector_parent.parts, *source_path.parts):
            if part == "..":
                if not parts:
                    raise ValueError("Property inspector script escapes the package")
                parts.pop()
            elif part not in ("", "."):
                parts.append(part)
        resolved_scripts.append(PurePosixPath(*parts).as_posix())
    expected_scripts = (*PROPERTY_INSPECTOR_VENDOR_FILES, PROPERTY_INSPECTOR_FILES[1])
    if tuple(resolved_scripts) != expected_scripts:
        raise ValueError("Property inspector script inventory is not approved")
    for reference in (*PROPERTY_INSPECTOR_FILES, *PROPERTY_INSPECTOR_VENDOR_FILES):
        exact_source_path(plugin_source, reference, reference.split("/", 1)[0])

    target = output_root / PLUGIN_FOLDER
    target.mkdir()
    for reference in sorted(asset_references):
        path = PurePosixPath(reference)
        destination = target.joinpath(*path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin_source.joinpath(*path.parts), destination)
    for reference in (*PROPERTY_INSPECTOR_FILES, *PROPERTY_INSPECTOR_VENDOR_FILES):
        path = PurePosixPath(reference)
        destination = target.joinpath(*path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin_source.joinpath(*path.parts), destination)
    (target / "src").mkdir()
    shutil.copy2(plugin_source / "src" / "launcher.js", target / "src" / "launcher.js")
    shutil.copytree(runtime_bundle, target / "runtime", symlinks=True)
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", "utf-8"
    )
    (target / "package.json").write_text(
        json.dumps(minimal_package, indent=2, ensure_ascii=True) + "\n", "utf-8"
    )
    return target


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-bundle", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    target = prepare_package(
        root / PLUGIN_FOLDER,
        args.runtime_bundle,
        args.output_root,
        root,
    )
    print(target)


if __name__ == "__main__":
    main()
