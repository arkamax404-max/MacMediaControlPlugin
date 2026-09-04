import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).parents[1]
PACKAGING = ROOT / "packaging"


def load_preparer():
    path = PACKAGING / "prepare_ulanzi_spike.py"
    spec = importlib.util.spec_from_file_location("prepare_ulanzi_package", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_runtime(root, preparer):
    for relative in preparer.REQUIRED_RUNTIME_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"runtime" if relative in {"MediaControlRuntime", "MediaRemoteHelper"} else b"license")
    for name in ("MediaControlRuntime", "MediaRemoteHelper"):
        executable = root / name
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)


def add_pyinstaller_macos_python_symlinks(root, name="Python3", version="3.9"):
    framework = root / "_internal" / f"{name}.framework"
    python = framework / "Versions" / version / name
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    (framework / "Versions" / version / "Resources").mkdir()
    os.symlink(version, framework / "Versions" / "Current")
    os.symlink(f"Versions/Current/{name}", framework / name)
    os.symlink("Versions/Current/Resources", framework / "Resources")
    os.symlink(f"{name}.framework/Versions/{version}/{name}", root / "_internal" / name)


class PackagingContractTests(unittest.TestCase):
    def test_macos_build_contract_uses_extensionless_launcher_runtime(self):
        build = (PACKAGING / "build_ulanzi_runtime_macos.sh").read_text("utf-8")
        spec = (PACKAGING / "ulanzi_runtime.spec").read_text("utf-8")
        lock = (PACKAGING / "requirements-ulanzi-runtime.lock").read_text("utf-8")
        launcher = (ROOT / "com.arkamax404.mediacontrold200.ulanziPlugin" / "src" /
                    "launcher.js").read_text("utf-8")

        self.assertIn("Darwin", build)
        self.assertIn("--require-hashes", build)
        self.assertIn("--workpath \"$1/pyinstaller\" --distpath \"$2\"", build)
        self.assertIn('name="MediaControlRuntime"', spec)
        self.assertIn('name="runtime"', spec)
        self.assertIn('MEDIAREMOTE_HELPER', build)
        self.assertIn('build_mediaremote_helper.py', build)
        self.assertIn('mv "$2/runtime/_internal/MediaRemoteHelper" "$2/runtime/MediaRemoteHelper"', build)
        self.assertIn('(str(mediaremote_helper), ".")', spec)
        self.assertIn('packaging" / "licenses" / "cpython" / "LICENSE.txt"', spec)
        self.assertNotIn("sys.base_prefix", spec)
        self.assertIn('"MediaControlRuntime"', launcher)
        self.assertNotIn("MediaControlRuntime.exe", launcher)
        for forbidden in ("pefile", "pywin32", "winrt", "pycaw"):
            self.assertNotIn(forbidden, lock.lower())

    def test_mediaremote_helper_builder_uses_fixed_native_inputs(self):
        path = PACKAGING / "build_mediaremote_helper.py"
        spec = importlib.util.spec_from_file_location("build_mediaremote_helper", path)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        source = ROOT / "d200_bridge" / "native" / "MediaRemoteHelper.m"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "MediaRemoteHelper"
            runner = Mock(side_effect=lambda args, check: output.write_bytes(b"native"))
            builder.build_helper(source, output, runner=runner)
        command = runner.call_args.args[0]
        self.assertEqual(command[:7], ["xcrun", "--sdk", "macosx", "clang", "-fobjc-arc", "-framework", "Foundation"])
        self.assertIn("MediaRemote", command)
        self.assertNotIn("osascript", " ".join(command))

    def test_cpython_runtime_license_is_repository_contained_and_attributed(self):
        license_path = PACKAGING / "licenses" / "cpython" / "LICENSE.txt"
        content = license_path.read_bytes()

        self.assertEqual(
            sha256(content).hexdigest(),
            "599826df92bfdcd2702eac691072498bb096c55af04ee984cf90f70ed77b5a70",
        )
        self.assertIn(
            b"Copyright (c) 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010,",
            content,
        )
        self.assertIn(b"2021 Python Software Foundation;", content)

    def test_sdk_verifier_uses_the_repository_contained_offline_sdk_surface(self):
        result = subprocess.run(
            [sys.executable, "-B", str(PACKAGING / "verify_ulanzi_sdk.py")],
            cwd=ROOT, capture_output=True, text=True, timeout=10, check=True,
        )
        verification = json.loads(result.stdout)
        self.assertEqual(verification["sdk"], "0.1.0")
        self.assertEqual(verification["websocket_client"], "1.8.0")
        self.assertTrue(verification["callbacks_nonblocking"])

    def test_vendored_node_sdk_is_macos_only(self):
        utils = (ROOT / "com.arkamax404.mediacontrold200.ulanziPlugin" / "vendor" /
                 "ulanzi-sdk" / "libs" / "utils.js").read_text("utf-8")
        self.assertNotIn("process.platform === 'win32'", utils)
        self.assertNotIn("windows", utils.lower())
        self.assertNotIn("\\\\", utils)
        self.assertIn("return 'mac';", utils)

    def test_runtime_inventory_is_deterministic_and_macos_only(self):
        preparer = load_preparer()
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            create_runtime(runtime, preparer)
            first = preparer.validate_runtime_bundle(runtime)
            self.assertEqual(first, preparer.validate_runtime_bundle(runtime))
            self.assertEqual(first, tuple(sorted(preparer.REQUIRED_RUNTIME_FILES)))
            (runtime / "_internal" / "legacy.dll").write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "Windows binaries"):
                preparer.validate_runtime_bundle(runtime)

    def test_runtime_inventory_rejects_unsafe_layouts(self):
        preparer = load_preparer()
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            create_runtime(runtime, preparer)
            (runtime / "unexpected").mkdir()
            with self.assertRaisesRegex(ValueError, "macOS MediaControlRuntime layout"):
                preparer.validate_runtime_bundle(runtime)
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            create_runtime(runtime, preparer)
            os.unlink(runtime / "_internal" / "licenses" / "project" / "LICENSE")
            os.symlink("../THIRD_PARTY_NOTICES.md",
                       runtime / "_internal" / "licenses" / "project" / "LICENSE")
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                preparer.validate_runtime_bundle(runtime)

    def test_runtime_inventory_allows_only_contained_pyinstaller_python_symlink_closures(self):
        preparer = load_preparer()
        for name, version in (("Python3", "3.9"), ("Python", "3.13")):
            with self.subTest(name=name, version=version), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory)
                create_runtime(runtime, preparer)
                add_pyinstaller_macos_python_symlinks(runtime, name, version)
                self.assertEqual(
                    preparer.validate_runtime_bundle(runtime),
                    tuple(sorted(preparer.REQUIRED_RUNTIME_FILES + (
                        f"_internal/{name}.framework/Versions/{version}/{name}",))),
                )
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            create_runtime(runtime, preparer)
            os.symlink("../MediaControlRuntime", runtime / "_internal" / "unexpected-link")
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                preparer.validate_runtime_bundle(runtime)
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            create_runtime(runtime, preparer)
            add_pyinstaller_macos_python_symlinks(runtime)
            os.unlink(runtime / "_internal" / "Python3.framework" / "Python3")
            os.symlink("../../MediaControlRuntime",
                       runtime / "_internal" / "Python3.framework" / "Python3")
            with self.assertRaisesRegex(ValueError, "unexpected target"):
                preparer.validate_runtime_bundle(runtime)
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            create_runtime(runtime, preparer)
            (runtime.parent / "outside").write_bytes(b"outside")
            add_pyinstaller_macos_python_symlinks(runtime)
            python = runtime / "_internal" / "Python3.framework" / "Versions" / "3.9" / "Python3"
            python.unlink()
            os.symlink("../../../../../outside", python)
            with self.assertRaisesRegex(ValueError, "escapes the runtime root"):
                preparer.validate_runtime_bundle(runtime)
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            create_runtime(runtime, preparer)
            add_pyinstaller_macos_python_symlinks(runtime, "Python", "3.13")
            (runtime / "_internal" / "Python.framework" / "Versions" / "3.13" / "Python").unlink()
            with self.assertRaisesRegex(ValueError, "symbolic link is broken"):
                preparer.validate_runtime_bundle(runtime)
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            create_runtime(runtime, preparer)
            add_pyinstaller_macos_python_symlinks(runtime, "Python", "3.13")
            python = runtime / "_internal" / "Python.framework" / "Versions" / "3.13" / "Python"
            python.unlink()
            python.mkdir()
            with self.assertRaisesRegex(ValueError, "must resolve to a file"):
                preparer.validate_runtime_bundle(runtime)
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            create_runtime(runtime, preparer)
            add_pyinstaller_macos_python_symlinks(runtime, "Python", "3.13")
            os.unlink(runtime / "_internal" / "Python")
            os.symlink("Python.framework/Versions/3.13/Python.exe", runtime / "_internal" / "Python")
            with self.assertRaisesRegex(ValueError, "unexpected target"):
                preparer.validate_runtime_bundle(runtime)

    def test_projection_preserves_manifest_actions_assets_and_inspector(self):
        preparer = load_preparer()
        plugin = ROOT / preparer.PLUGIN_FOLDER
        protected = {name: (plugin / name).read_bytes() for name in
                     ("manifest.json", "package.json", "src/app.js", "src/plugin.js")}
        with tempfile.TemporaryDirectory() as runtime_dir, tempfile.TemporaryDirectory() as output_dir:
            runtime = Path(runtime_dir)
            create_runtime(runtime, preparer)
            add_pyinstaller_macos_python_symlinks(runtime)
            target = preparer.prepare_package(plugin, runtime, Path(output_dir), ROOT)
            manifest = json.loads((target / "manifest.json").read_text("utf-8"))
            source_manifest = json.loads(protected["manifest.json"])

            self.assertEqual(manifest["CodePath"], "src/launcher.js")
            self.assertEqual(manifest["OS"], source_manifest["OS"])
            self.assertEqual([action["UUID"] for action in manifest["Actions"]],
                             [f"{source_manifest['UUID']}.{suffix}"
                              for suffix in preparer.PORTED_ACTION_SUFFIXES])
            self.assertEqual(len(manifest["Actions"]), len(preparer.PORTED_ACTION_SUFFIXES))
            self.assertTrue(all((target / reference).is_file()
                                for reference in preparer.PROPERTY_INSPECTOR_FILES +
                                preparer.PROPERTY_INSPECTOR_VENDOR_FILES))
            self.assertEqual(set(path.name for path in target.iterdir()),
                             {"assets", "manifest.json", "package.json", "property-inspector",
                              "runtime", "src", "vendor"})
            self.assertTrue((target / "runtime" / "MediaControlRuntime").is_file())
            self.assertTrue((target / "runtime" / "MediaRemoteHelper").is_file())
            self.assertFalse((target / "runtime" / "MediaControlRuntime.exe").exists())
            self.assertTrue((target / "runtime" / "_internal" / "Python3").is_symlink())
            self.assertEqual(
                (target / "runtime" / "_internal" / "Python3").readlink(),
                Path("Python3.framework/Versions/3.9/Python3"),
            )
            self.assertEqual(protected, {name: (plugin / name).read_bytes()
                                         for name in protected})

    def test_projection_rejects_missing_or_changed_action_inventory(self):
        preparer = load_preparer()
        source = ROOT / preparer.PLUGIN_FOLDER
        with (tempfile.TemporaryDirectory() as source_dir,
              tempfile.TemporaryDirectory() as runtime_dir,
              tempfile.TemporaryDirectory() as output_dir):
            plugin = Path(source_dir) / preparer.PLUGIN_FOLDER
            shutil.copytree(source, plugin)
            manifest_path = plugin / "manifest.json"
            manifest = json.loads(manifest_path.read_text("utf-8"))
            manifest["Actions"].pop()
            manifest_path.write_text(json.dumps(manifest), "utf-8")
            runtime = Path(runtime_dir)
            create_runtime(runtime, preparer)
            with self.assertRaisesRegex(ValueError, "action UUID inventory"):
                preparer.prepare_package(plugin, runtime, Path(output_dir), ROOT)

    def test_projection_rejects_non_macos_manifest(self):
        preparer = load_preparer()
        manifest = json.loads((ROOT / preparer.PLUGIN_FOLDER / "manifest.json").read_text("utf-8"))
        manifest["OS"] = [{"Platform": "windows"}]
        with self.assertRaisesRegex(ValueError, "only macOS"):
            preparer.validate_source_manifest(manifest)

    def test_projection_rejects_unsafe_package_paths_and_external_cwd_works(self):
        preparer = load_preparer()
        plugin = ROOT / preparer.PLUGIN_FOLDER
        with self.assertRaisesRegex(ValueError, "unsafe assets path"):
            preparer.exact_source_path(plugin, "../LICENSE", "assets")
        with (tempfile.TemporaryDirectory() as runtime_dir,
              tempfile.TemporaryDirectory() as output_dir,
              tempfile.TemporaryDirectory() as cwd):
            runtime = Path(runtime_dir)
            create_runtime(runtime, preparer)
            result = subprocess.run(
                [sys.executable, "-B", str(PACKAGING / "prepare_ulanzi_spike.py"),
                 "--runtime-bundle", str(runtime), "--output-root", output_dir],
                cwd=cwd, capture_output=True, text=True, timeout=10, check=True,
            )
            target = Path(result.stdout.strip())
            self.assertEqual(target.parent, Path(output_dir).resolve())
            self.assertEqual(target.name, preparer.PLUGIN_FOLDER)


if __name__ == "__main__":
    unittest.main()
