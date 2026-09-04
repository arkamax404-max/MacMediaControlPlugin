import asyncio
import http.client
import json
import logging
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import d200_bridge.__main__ as bridge_main
from d200_bridge.lifecycle import CompanionLifecycle
from d200_bridge.logging_config import configure_logging
from d200_bridge.paths import CompanionPaths, PathSecurity, ensure_token, load_token, load_token_file
from d200_bridge.server import BRIDGE_HOST, create_server
from d200_bridge.state import MediaStateCache
from d200_bridge.version import API_MAJOR, API_MINOR, COMPANION_VERSION
TOKEN = "A" * 43
class FoundationTests(unittest.TestCase):
    def test_macos_paths_and_nonblocking_lock_use_application_support(self):
        from d200_bridge.platform_services import create_lock

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = CompanionPaths.from_environment({"HOME": str(root)})
            self.assertEqual(paths.root, root / "Library" / "Application Support" / "GSMTCD200Controller")
            self.assertEqual(paths.cache, root / "Library" / "Caches" / "GSMTCD200Controller")
            self.assertEqual(paths.logs, root / "Library" / "Logs" / "GSMTCD200Controller")
            first = create_lock(paths)
            second = create_lock(paths)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            self.assertFalse(second.unavailable)
            first.close()
            self.assertTrue(second.acquire())
            second.close()

    def test_posix_lock_closes_the_opened_handle_when_acquisition_errors(self):
        from d200_bridge.platform_services import PosixFileLock

        with tempfile.TemporaryDirectory() as directory:
            handle = Mock()
            with patch("builtins.open", return_value=handle), patch(
                "fcntl.flock", side_effect=OSError("lock unavailable")
            ):
                lock = PosixFileLock(Path(directory) / "companion.lock")
                self.assertFalse(lock.acquire())

        self.assertTrue(lock.unavailable)
        handle.close.assert_called_once_with()

    def test_macos_paths_reject_a_symlinked_home_before_selecting_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "real-home"
            target.mkdir()
            linked_home = root / "linked-home"
            linked_home.symlink_to(target, target_is_directory=True)

            with self.assertRaises(OSError):
                CompanionPaths.from_environment({"HOME": str(linked_home)})

    def test_paths_are_absolute_normalized_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = CompanionPaths.from_environment({"HOME": str(root)})
        self.assertTrue(paths.root.is_absolute())
        self.assertEqual(paths.token, paths.root / "bridge-token")
        invalid = ({}, {"HOME": "relative"})
        for value in invalid:
            with self.assertRaises((RuntimeError, ValueError)):
                CompanionPaths.from_environment(value)
        with self.assertRaises(ValueError):
            CompanionPaths(Path("relative"))
        security = Mock(); security.validate_chain.side_effect = OSError("unsafe ancestor")
        with self.assertRaises(OSError):
            CompanionPaths.from_environment({"HOME": "/absolute/home"}, security)
    def test_metadata_rejects_symlinks_and_hardlinks(self):
        security = PathSecurity()
        regular = SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=1, st_size=43,
                                  st_file_attributes=0)
        security.validate_metadata(regular, "file")
        for changes in ({"st_mode": stat.S_IFLNK}, {"st_nlink": 2}):
            with self.assertRaises(OSError):
                security.validate_metadata(SimpleNamespace(**{**regular.__dict__, **changes}), "file")
    def test_token_creation_reuse_cli_retry_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = CompanionPaths(Path(directory).resolve()).token
            with patch("d200_bridge.paths.secrets.token_bytes", return_value=b"\0" * 32):
                self.assertEqual(ensure_token(path), TOKEN)
            self.assertEqual(ensure_token(path), TOKEN)
            paths = CompanionPaths(Path(directory).resolve())
            with patch("d200_bridge.paths.load_token_file", side_effect=[FileNotFoundError, TOKEN]), \
                 patch("d200_bridge.paths.time.sleep") as sleep:
                self.assertEqual(load_token(paths), TOKEN)
            sleep.assert_called_once_with(0.02)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            path.unlink()
            with patch("d200_bridge.paths.os.link", side_effect=OSError("failed")):
                with self.assertRaises(OSError):
                    ensure_token(path)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
    def test_token_rejects_types_links_sizes_encoding_and_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "token"
            target.mkdir()
            with self.assertRaises(OSError):
                load_token_file(target)
            target.rmdir()
            for value in (b"", b"A" * 42, b"A" * 44, b"+" * 43, b"\xff" * 43):
                target.write_bytes(value)
                with self.assertRaises((OSError, ValueError, UnicodeError)):
                    load_token_file(target)
                target.unlink()
            target.write_text(TOKEN, encoding="ascii")
            linked = root / "linked"
            os.link(target, linked)
            with self.assertRaises(OSError):
                load_token_file(linked)
            linked.unlink(); metadata = os.lstat(target); changed = SimpleNamespace(**{name: getattr(metadata, name, 0) for name in ("st_mode", "st_nlink", "st_size", "st_file_attributes", "st_dev", "st_ino")})
            changed.st_size += 1
            with patch("d200_bridge.paths.os.fstat", side_effect=[metadata, changed]):
                with self.assertRaises(OSError): load_token_file(target)
    def test_health_is_bounded_versioned_and_private(self):
        lifecycle = CompanionLifecycle(clock=Mock(side_effect=[10.0, 12.5]))
        lifecycle.set_status("degraded")
        health = lifecycle.health()
        self.assertEqual((health["api_major"], health["api_minor"]), (API_MAJOR, API_MINOR))
        self.assertEqual((health["companion_version"], health["status"]), (COMPANION_VERSION, "degraded"))
        self.assertLess(len(json.dumps(health)), 512)
        self.assertFalse(set(health) & {"token", "path", "title", "artist", "hostname"})
        transitions = (("degraded", "ready", True), ("ready", "degraded", True), ("stopping", "stopping", True), ("stopping", "ready", False))
        for source, target, allowed in transitions:
            lifecycle._status = source
            if allowed: lifecycle.set_status(target)
            else:
                with self.assertRaises(ValueError): lifecycle.set_status(target)
                self.assertEqual(lifecycle.status, source)
    def test_logging_owns_only_its_handlers_bounds_records_and_rotates(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = logging.getLogger("d200_bridge")
            foreign = logging.NullHandler(); logger.addHandler(foreign)
            configure_logging(Path(directory), token=TOKEN, console=False, max_bytes=256)
            first = next(item for item in logger.handlers if item is not foreign)
            configure_logging(Path(directory), token=TOKEN, console=False, max_bytes=256)
            self.assertIsNone(first.stream); self.assertIn(foreign, logger.handlers)
            child = logging.getLogger("d200_bridge.child")
            try:
                raise RuntimeError("synthetic-marker")
            except RuntimeError:
                for _index in range(40):
                    child.error("Authorization: Bearer %s C:\\Users\\Synthetic Name\\cover.png %s", TOKEN, "synthetic-marker " * 1000, exc_info=True)
            for item in logger.handlers: item.flush()
            files = list(Path(directory).glob("companion.log*"))
            self.assertLessEqual(len(files), 5)
            self.assertTrue(all(path.stat().st_size <= 256 for path in files))
            output = "".join(path.read_text(encoding="utf-8") for path in files)
            self.assertNotIn(TOKEN, output); self.assertNotIn("Synthetic Name", output)
            self.assertNotIn("synthetic-marker", output)
            self.assertEqual(output.count("redacted_event"), len(output.splitlines()))
            for item in list(logger.handlers):
                if item is not foreign: item.close(); logger.removeHandler(item)
            logger.removeHandler(foreign)
    def test_main_acquisition_matrix_releases_lock_before_bridge(self):
        for failure in ("mutex", "paths", "token", "logging"):
            mutex = Mock(); mutex.acquire.return_value = failure != "mutex"; error = RuntimeError("failed")
            with patch.object(bridge_main, "create_lock", return_value=mutex), patch.object(
                bridge_main.CompanionPaths, "from_environment", side_effect=error if failure == "paths" else None,
                return_value=SimpleNamespace(token=Path("C:/token"), logs=Path("C:/logs"))), patch.object(
                bridge_main, "ensure_token", side_effect=error if failure == "token" else None, return_value=TOKEN), patch.object(bridge_main, "configure_logging", side_effect=error if failure == "logging" else None), patch.object(
                bridge_main.asyncio, "run") as run:
                self.assertEqual(bridge_main.main([]), 1)
            if failure == "paths":
                mutex.close.assert_not_called()
            else:
                mutex.close.assert_called_once_with()
            run.assert_not_called()
class ServerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever); self.loop_thread.start()
        self.commands = []; self.stop_event = asyncio.Event(); self.lifecycle = CompanionLifecycle()
        self.artwork_lookup = Mock()
        self.request_stop = Mock(side_effect=lambda: self.loop.call_soon_threadsafe(self.stop_event.set))
        async def command(action): self.commands.append(action); return True
        self.server = create_server(MediaStateCache(), command, self.loop, port=0, token=TOKEN, lifecycle=self.lifecycle, artwork_lookup=self.artwork_lookup, request_stop=self.request_stop)
        self.thread = threading.Thread(target=self.server.serve_forever); self.thread.start()
        self.origin = f"http://{BRIDGE_HOST}:{self.server.server_port}"
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.loop.call_soon_threadsafe(self.loop.stop); self.loop_thread.join(); self.loop.close()
    def request(self, path, method="GET", authorization=None):
        headers = {} if authorization is None else {"Authorization": authorization}
        if authorization is not None: headers["X-Companion-Instance"] = self.lifecycle.instance_id
        request = Request(self.origin + path, data=b"{}" if method == "POST" else None, method=method, headers=headers)
        return urlopen(request, timeout=2)
    def raw_command(self, values, instance=True):
        connection = http.client.HTTPConnection(BRIDGE_HOST, self.server.server_port, timeout=2)
        connection.putrequest("POST", "/command/next")
        for value in values: connection.putheader("Authorization", value)
        if instance is not None: connection.putheader("X-Companion-Instance", self.lifecycle.instance_id if instance is True else instance)
        connection.putheader("Content-Length", "2"); connection.endheaders(b"{}")
        response = connection.getresponse(); result = response.status, json.loads(response.read())
        connection.close(); return result
    def test_health_and_strict_generic_authorization_matrix(self):
        with self.request("/health") as response:
            self.assertEqual(json.load(response)["service"], "d200-gsmtc-bridge")
        invalid = (None, "", "Basic abc", "bearer " + TOKEN, "Bearer wrong", "Bearer",
                   "Bearer  " + TOKEN, "Bearer\t" + TOKEN, "Bearer " + TOKEN + " ",
                   "Bearer é", "Bearer " + "A" * 1000)
        for value in invalid:
            with self.assertRaises(HTTPError) as error:
                self.request("/command/next", "POST", value)
            self.assertEqual(error.exception.code, 401 if value is None else 403)
            self.assertEqual(json.load(error.exception), {"error": "unauthorized"})
            error.exception.close()
        self.assertEqual(self.raw_command([f"Bearer {TOKEN}"] * 2), (403, {"error": "unauthorized"}))
        for path, method in (("/state", "GET"), ("/artwork/" + "a" * 64, "GET"), ("/lifecycle/stop", "POST"), ("/command/unknown", "POST")):
            with self.assertRaises(HTTPError) as error: self.request(path, method)
            error.exception.close()
        self.assertEqual(self.commands, []); self.artwork_lookup.assert_not_called()
        self.request_stop.assert_not_called()
    def test_command_instance_mismatch_is_generic_and_never_calls_callback(self):
        for instance in (None, "bad", "123e4567-e89b-42d3-a456-426614174000"):
            self.assertEqual(self.raw_command([f"Bearer {TOKEN}"], instance),
                             (409, {"error": "companion_mismatch"}))
        self.assertEqual(self.commands, [])
    def test_exact_auth_stop_order_and_command_refusal(self):
        self.assertEqual(self.raw_command([f"Bearer {TOKEN}"]), (200, {"ok": True}))
        original = self.server.RequestHandlerClass._json; self.server.handle_error = Mock()
        def broken_write(handler, status, payload, **kwargs):
            if payload == {"ok": True}: raise BrokenPipeError("disconnected")
            return original(handler, status, payload, **kwargs)
        self.server.RequestHandlerClass._json = broken_write
        with self.assertRaises((http.client.RemoteDisconnected, ConnectionError)):
            self.request("/lifecycle/stop", "POST", f"Bearer {TOKEN}")
        self.assertEqual(self.lifecycle.status, "stopping"); self.request_stop.assert_called_once_with()
        with self.assertRaises(HTTPError) as error:
            self.request("/command/next", "POST", f"Bearer {TOKEN}")
        self.assertEqual(error.exception.code, 503); error.exception.close()
class CliStopTests(unittest.TestCase):
    def test_cli_stop_is_bounded_loopback_and_does_not_disclose_token(self):
        response = MagicMock(status=200); response.__enter__.return_value = response
        with patch.object(bridge_main, "load_token", return_value=TOKEN), patch.object(
            bridge_main, "urlopen", return_value=response
        ) as send:
            self.assertEqual(bridge_main.main(["--stop"]), 0)
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:43821/lifecycle/stop")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(send.call_args.kwargs["timeout"], 2)
if __name__ == "__main__": unittest.main()
