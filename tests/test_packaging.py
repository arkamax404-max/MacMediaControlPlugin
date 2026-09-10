import importlib.util
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).parents[1]
PACKAGING = ROOT / "packaging"
CPU_TYPE_X86_64 = 0x01000007
CPU_TYPE_ARM64 = 0x0100000C


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
        path.write_bytes(
            struct.pack("<IiiIIIII", 0xFEEDFACF, CPU_TYPE_X86_64,
                        3, 2, 0, 0, 0, 0)
            if relative in {"MediaControlRuntime", "MediaRemoteHelper"} else b"license"
        )
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


def add_pyinstaller_macos_dylib_alias(root, name="libXau.6.dylib", target="PIL/.dylibs/libXau.6.dylib"):
    library = root / "_internal" / target
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_bytes(b"dylib")
    os.symlink(target, root / "_internal" / name)


class PackagingContractTests(unittest.TestCase):
    def test_release_metadata_is_consistently_v2_3_3(self):
        plugin = ROOT / "com.arkamax404.mediacontrold200.ulanziPlugin"
        manifest = json.loads((plugin / "manifest.json").read_text("utf-8"))
        package = json.loads((plugin / "package.json").read_text("utf-8"))
        package_lock = json.loads((plugin / "package-lock.json").read_text("utf-8"))
        helper = (plugin / "helper" / "Invoke-MediaControlSetup.mjs").read_text("utf-8")
        setup_action = (plugin / "runtime" / "python" / "setup_action.py").read_text("utf-8")
        companion_version = (ROOT / "d200_bridge" / "version.py").read_text("utf-8")

        self.assertEqual(manifest["Version"], "2.3.3")
        self.assertEqual(package["version"], "2.3.3")
        self.assertEqual(package_lock["version"], "2.3.3")
        self.assertEqual(package_lock["packages"][""]["version"], "2.3.3")
        self.assertIn('const VERSION = "2.3.3";', helper)
        self.assertIn('"Version": "2.3.3"', setup_action)
        self.assertEqual(companion_version.splitlines()[0], 'COMPANION_VERSION = "2.3.3"')

    def test_macos_build_contract_uses_extensionless_launcher_runtime(self):
        build = (PACKAGING / "build_ulanzi_runtime_macos.sh").read_text("utf-8")
        spec = (PACKAGING / "ulanzi_runtime.spec").read_text("utf-8")
        lock = (PACKAGING / "requirements-ulanzi-runtime.lock").read_text("utf-8")
        launcher = (ROOT / "com.arkamax404.mediacontrold200.ulanziPlugin" / "src" /
                    "launcher.js").read_text("utf-8")

        self.assertIn("Darwin", build)
        self.assertIn("--require-hashes", build)
        self.assertIn('python3.13 -I -s -m venv "$1/venv"', build)
        self.assertIn('PYTHON="$1/venv/bin/python"', build)
        self.assertIn('"$PYTHON" -I -s -m pip install --require-hashes', build)
        self.assertIn('"$PYTHON" -I -s -m PyInstaller --noconfirm --clean', build)
        self.assertNotIn('python3 -I -s -m pip install', build)
        self.assertIn("--workpath \"$1/pyinstaller\" --distpath \"$2\"", build)
        self.assertIn('name="MediaControlRuntime"', spec)
        self.assertIn('collect_submodules("d200_bridge")', spec)
        self.assertIn('"PIL"', spec)
        self.assertNotIn('"PIL"],', spec)
        self.assertIn('name="runtime"', spec)
        self.assertIn('MEDIAREMOTE_HELPER', build)
        self.assertIn('build_mediaremote_helper.py', build)
        self.assertIn('mv "$2/runtime/_internal/MediaRemoteHelper" "$2/runtime/MediaRemoteHelper"', build)
        self.assertIn('"$(uname -m)" != "x86_64"', build)
        self.assertIn('(str(mediaremote_helper), ".")', spec)
        self.assertIn('packaging" / "licenses" / "cpython" / "LICENSE.txt"', spec)
        self.assertNotIn("sys.base_prefix", spec)
        self.assertIn('"MediaControlRuntime"', launcher)
        self.assertNotIn("MediaControlRuntime.exe", launcher)
        for forbidden in ("pefile", "pywin32", "winrt", "pycaw"):
            self.assertNotIn(forbidden, lock.lower())
        self.assertIn("Pillow==", lock)

    def test_companion_import_graph_has_hashed_runtime_and_bundle_contracts(self):
        artwork = (ROOT / "d200_bridge" / "artwork.py").read_text("utf-8")
        spec = (PACKAGING / "ulanzi_runtime.spec").read_text("utf-8")
        lock = (PACKAGING / "requirements-ulanzi-runtime.lock").read_text("utf-8")
        pillow = next(line for line in lock.splitlines() if line.startswith("Pillow==11.3.0 "))

        self.assertIn("from PIL import Image, ImageOps", artwork)
        self.assertRegex(lock, r"(?m)^Pillow==[^\s]+(?:\s+--hash=sha256:[0-9a-f]{64})+$")
        self.assertIn(
            "--hash=sha256:1cd110edf822773368b396281a2293aeb91c90a2db00d78ea43e7e861631b722",
            pillow,
        )
        self.assertIn('collect_submodules("d200_bridge")', spec)
        self.assertIn('"PIL"', spec)
        self.assertNotRegex(spec, r"excludes\s*=\s*\[[^]]*['\"]PIL['\"]")

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

    def test_runtime_inventory_allows_contained_pyinstaller_dylib_aliases(self):
        preparer = load_preparer()
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            create_runtime(runtime, preparer)
            add_pyinstaller_macos_dylib_alias(runtime)
            self.assertIn(
                "_internal/PIL/.dylibs/libXau.6.dylib",
                preparer.validate_runtime_bundle(runtime),
            )

    def test_runtime_inventory_rejects_unsafe_pyinstaller_dylib_aliases(self):
        preparer = load_preparer()
        cases = (
            ("libXau.6.dylib", "/tmp/libXau.6.dylib", None),
            ("libXau.6.dylib", "../../outside", b"outside"),
            ("libXau.6.dylib", "PIL/.dylibs/missing.dylib", None),
            ("libXau.6.dylib", "PIL/.dylibs", "directory"),
            ("unexpected-link", "PIL/.dylibs/libXau.6.dylib", b"dylib"),
        )
        for name, target, content in cases:
            with self.subTest(name=name, target=target), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory)
                create_runtime(runtime, preparer)
                if content == "directory":
                    (runtime / "_internal" / target).mkdir(parents=True)
                elif content is not None:
                    library = runtime / "_internal" / target
                    library.parent.mkdir(parents=True, exist_ok=True)
                    library.write_bytes(content)
                os.symlink(target, runtime / "_internal" / name)
                with self.assertRaisesRegex(ValueError, "symbolic link|escapes the runtime root"):
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
                                preparer.PROPERTY_INSPECTOR_VENDOR_FILES +
                                preparer.HELPER_FILES))
            self.assertEqual(set(path.name for path in target.iterdir()),
                             {"assets", "helper", "manifest.json", "package.json",
                              "property-inspector", "runtime", "src", "vendor"})
            self.assertTrue((target / "runtime" / "MediaControlRuntime").is_file())
            self.assertTrue((target / "runtime" / "MediaRemoteHelper").is_file())
            self.assertFalse((target / "runtime" / "MediaControlRuntime.exe").exists())
            self.assertTrue((target / "runtime" / "_internal" / "Python3").is_file())
            self.assertFalse(any(path.is_symlink() for path in target.rglob("*")))
            self.assertEqual(protected, {name: (plugin / name).read_bytes()
                                         for name in protected})

    def test_runtime_rejects_arm64_only_required_executables(self):
        preparer = load_preparer()
        for name in ("MediaControlRuntime", "MediaRemoteHelper"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory)
                create_runtime(runtime, preparer)
                (runtime / name).write_bytes(
                    struct.pack("<IiiIIIII", 0xFEEDFACF, CPU_TYPE_ARM64,
                                0, 2, 0, 0, 0, 0)
                )
                with self.assertRaisesRegex(ValueError, f"{name} is not x86_64-compatible"):
                    preparer.validate_runtime_bundle(runtime)

    def test_runtime_rejects_non_macho_required_executables(self):
        preparer = load_preparer()
        for name in ("MediaControlRuntime", "MediaRemoteHelper"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory)
                create_runtime(runtime, preparer)
                (runtime / name).write_bytes(b"not Mach-O")
                with self.assertRaisesRegex(
                        ValueError, rf"lipo could not inspect required executable {name} \(exit 1\)"):
                    preparer.validate_runtime_bundle(runtime)

    def test_runtime_rejects_empty_required_executables(self):
        preparer = load_preparer()
        for name in ("MediaControlRuntime", "MediaRemoteHelper"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory)
                create_runtime(runtime, preparer)
                (runtime / name).write_bytes(b"")
                with self.assertRaisesRegex(
                        ValueError, rf"lipo could not inspect required executable {name} \(exit 1\)"):
                    preparer.validate_runtime_bundle(runtime)

    def test_runtime_rejects_exact_eight_byte_thin_macho_bypass(self):
        preparer = load_preparer()
        payload = struct.pack("<II", 0xFEEDFACF, CPU_TYPE_X86_64)
        self.assertEqual(len(payload), 8)
        for name in ("MediaControlRuntime", "MediaRemoteHelper"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory)
                create_runtime(runtime, preparer)
                (runtime / name).write_bytes(payload)
                with self.assertRaisesRegex(
                        ValueError, rf"lipo could not inspect required executable {name} \(exit 1\)"):
                    preparer.validate_runtime_bundle(runtime)

    def test_runtime_rejects_truncated_thin_macho_headers(self):
        preparer = load_preparer()
        cases = (
            ("32-bit little-endian", "<", 0xFEEDFACE, 7, 28),
            ("32-bit big-endian", ">", 0xFEEDFACE, 7, 28),
            ("64-bit little-endian", "<", 0xFEEDFACF, CPU_TYPE_X86_64, 32),
            ("64-bit big-endian", ">", 0xFEEDFACF, CPU_TYPE_X86_64, 32),
        )
        for name in ("MediaControlRuntime", "MediaRemoteHelper"):
            for shape, byte_order, magic, cpu_type, header_size in cases:
                for truncated_size in (8, header_size - 1):
                    with (self.subTest(name=name, shape=shape, size=truncated_size),
                          tempfile.TemporaryDirectory() as directory):
                        runtime = Path(directory)
                        create_runtime(runtime, preparer)
                        truncated_header = (
                            struct.pack(f"{byte_order}II", magic, cpu_type)
                            + bytes(truncated_size - 8)
                        )
                        self.assertEqual(len(truncated_header), truncated_size)
                        (runtime / name).write_bytes(truncated_header)

                        with self.assertRaisesRegex(
                                ValueError,
                                rf"lipo could not inspect required executable {name} \(exit 1\)"):
                            preparer.validate_runtime_bundle(runtime)

    def test_runtime_rejects_malformed_truncated_fat_macho_headers(self):
        preparer = load_preparer()
        cases = (
            ("fat32 big-endian", ">", 0xCAFEBABE),
            ("fat32 little-endian", "<", 0xCAFEBABE),
            ("fat64 big-endian", ">", 0xCAFEBABF),
            ("fat64 little-endian", "<", 0xCAFEBABF),
        )
        for name in ("MediaControlRuntime", "MediaRemoteHelper"):
            for shape, byte_order, magic in cases:
                with (self.subTest(name=name, shape=shape),
                      tempfile.TemporaryDirectory() as directory):
                    runtime = Path(directory)
                    create_runtime(runtime, preparer)
                    payload = struct.pack(f"{byte_order}III", magic, 1, CPU_TYPE_X86_64)
                    self.assertEqual(len(payload), 12)
                    (runtime / name).write_bytes(payload)
                    with self.assertRaisesRegex(
                            ValueError,
                            rf"lipo could not inspect required executable {name} \(exit 1\)"):
                        preparer.validate_runtime_bundle(runtime)

    def test_runtime_fails_closed_when_lipo_cannot_produce_trusted_architectures(self):
        preparer = load_preparer()
        failures = (
            ("missing", FileNotFoundError("missing"), "Could not run /usr/bin/lipo.*missing"),
            ("raises", RuntimeError("runner failed"), "Could not run /usr/bin/lipo.*runner failed"),
            ("timeout", subprocess.TimeoutExpired(preparer.LIPO, 10), "/usr/bin/lipo timed out"),
            ("nonzero", subprocess.CompletedProcess([], 7, "", "invalid binary"),
             r"lipo could not inspect.*\(exit 7\): invalid binary"),
            ("empty", subprocess.CompletedProcess([], 0, "\n", ""),
             "lipo reported no architectures"),
            ("unexpected", subprocess.CompletedProcess([], 0, "x86_64 mystery\n", ""),
             "lipo reported malformed architectures.*x86_64 mystery"),
            ("duplicate", subprocess.CompletedProcess([], 0, "x86_64 x86_64\n", ""),
             "lipo reported malformed architectures.*x86_64 x86_64"),
        )
        for name in ("MediaControlRuntime", "MediaRemoteHelper"):
            for case, failure, message in failures:
                with (self.subTest(name=name, case=case),
                      tempfile.TemporaryDirectory() as directory):
                    runtime = Path(directory)
                    create_runtime(runtime, preparer)

                    def runner(command, **kwargs):
                        if Path(command[-1]).name == name:
                            if isinstance(failure, Exception):
                                raise failure
                            return failure
                        return subprocess.CompletedProcess(command, 0, "x86_64\n", "")

                    with self.assertRaisesRegex(ValueError, message):
                        preparer.validate_runtime_bundle(runtime, architecture_runner=runner)

    @unittest.skipUnless(sys.platform == "darwin", "requires the macOS toolchain")
    def test_runtime_accepts_real_x86_64_built_required_executables(self):
        preparer = load_preparer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            create_runtime(runtime, preparer)
            source = root / "main.c"
            source.write_text("int main(void) { return 0; }\n", "utf-8")
            for name in ("MediaControlRuntime", "MediaRemoteHelper"):
                subprocess.run(
                    ["/usr/bin/clang", "-arch", "x86_64", str(source),
                     "-o", str(runtime / name)],
                    capture_output=True, text=True, timeout=30, check=True,
                )
            self.assertEqual(
                preparer.validate_runtime_bundle(runtime),
                tuple(sorted(preparer.REQUIRED_RUNTIME_FILES)),
            )

    def test_runtime_invokes_absolute_lipo_without_a_shell_for_each_required_executable(self):
        preparer = load_preparer()
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            create_runtime(runtime, preparer)
            runner = Mock(return_value=subprocess.CompletedProcess([], 0, "x86_64\n", ""))

            preparer.validate_runtime_bundle(runtime, architecture_runner=runner)

            self.assertEqual(runner.call_count, 2)
            for call, name in zip(runner.call_args_list,
                                  ("MediaControlRuntime", "MediaRemoteHelper")):
                self.assertEqual(call.args[0],
                                 ["/usr/bin/lipo", "-archs", str(runtime / name)])
                self.assertEqual(call.kwargs, {
                    "capture_output": True,
                    "text": True,
                    "timeout": preparer.LIPO_TIMEOUT_SECONDS,
                    "check": False,
                    "shell": False,
                })

    def test_runtime_accepts_universal_lipo_output_with_x86_64(self):
        preparer = load_preparer()
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            create_runtime(runtime, preparer)
            runner = Mock(return_value=subprocess.CompletedProcess(
                [], 0, "x86_64 arm64\n", ""))
            self.assertEqual(
                preparer.validate_runtime_bundle(runtime, architecture_runner=runner),
                tuple(sorted(preparer.REQUIRED_RUNTIME_FILES)),
            )

    def test_release_workflow_pins_and_verifies_intel_runner(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
        runner = workflow.index("runs-on: macos-15-intel")
        architecture_check = workflow.index('run: test "$(uname -m)" = x86_64')
        checkout = workflow.index("uses: actions/checkout@v6")

        self.assertLess(runner, architecture_check)
        self.assertLess(architecture_check, checkout)

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
