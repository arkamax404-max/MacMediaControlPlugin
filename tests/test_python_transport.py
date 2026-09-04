import json
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).parents[1]
RUNTIME = ROOT / "com.arkamax404.mediacontrold200.ulanziPlugin" / "runtime" / "python"
sys.path.insert(0, str(RUNTIME))

from bridge_client import (  # noqa: E402
    BRIDGE_ORIGIN,
    BRIDGE_TIMEOUT_SECONDS,
    COMMAND_REQUEST_TIMEOUT_SECONDS,
    BridgeClient,
    BridgeResult,
    bridge_origin_from_future,
)
from transport_actions import (  # noqa: E402
    ACTION_COMMANDS,
    TransportRouter,
    action_uuid_from_event,
    command_from_event,
)
from transport_diagnostics import (  # noqa: E402
    MAX_COUNTER,
    CategoryCounterFile,
    TransportDiagnostics,
    result_class,
    transport_diagnostic_path,
)


TOKEN = "A" * 43
INSTANCE_ID = "123e4567-e89b-42d3-a456-426614174000"
PLUGIN_UUID = "com.arkamax404.ulanzi.mediacontrol"


def health(**overrides):
    return {
        "service": "d200-gsmtc-bridge",
        "api_major": 1,
        "api_minor": 0,
        "status": "ready",
        "instance_id": INSTANCE_ID,
        **overrides,
    }


class Response:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, amount=-1):
        return self.payload if amount < 0 else self.payload[:amount]


class RecordingOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


class RecordingClient:
    def __init__(self, status="ok"):
        self.status = status
        self.commands = []

    def execute(self, command, cancelled=None):
        self.commands.append(command)
        return BridgeResult(command, self.status, 200 if self.status == "ok" else None)


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.005)
    return True


class PythonTransportTests(unittest.TestCase):
    def test_transport_diagnostic_path_uses_companion_log_directory(self):
        home = Path("/Users/example")
        self.assertEqual(transport_diagnostic_path(home), home / "Library" / "Logs"
                         / "GSMTCD200Controller" / "diagnostics" / "transport-counters.json")
        with self.assertRaises(ValueError):
            transport_diagnostic_path("relative-home")
        with self.assertRaises(ValueError):
            CategoryCounterFile("relative-counters.json")

    def test_transport_diagnostics_persist_atomic_category_counters_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Library" / "Logs" / "GSMTCD200Controller" / "diagnostics" / "transport-counters.json"
            diagnostics = TransportDiagnostics(counter_file=CategoryCounterFile(path))
            diagnostics.recognized_event("next")
            diagnostics.result("Bearer private-token https://example.test/track")
            self.assertEqual(json.loads(path.read_text(encoding="ascii")), {
                "recognized_next": 1,
                "result_unavailable": 1,
            })
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            content = path.read_text(encoding="ascii")
            for forbidden in ("Bearer", "private-token", "https://", "track", str(path.parent)):
                self.assertNotIn(forbidden, content)

    def test_counter_file_rejects_symlinked_directory_or_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            unsafe_directory = root / "Library"
            unsafe_directory.symlink_to(target, target_is_directory=True)
            with self.assertRaises(OSError):
                CategoryCounterFile(unsafe_directory / "Logs" / "transport-counters.json").write({})

            path = root / "safe" / "transport-counters.json"
            path.parent.mkdir()
            path.symlink_to(target / "outside")
            with self.assertRaises(OSError):
                CategoryCounterFile(path).write({"recognized_next": 1})
            self.assertFalse((target / "outside").exists())

    def test_counter_file_preserves_previous_snapshot_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "diagnostics" / "transport-counters.json"
            path.parent.mkdir()
            path.write_text('{"recognized_next":1}\n', encoding="ascii")
            with mock.patch("transport_diagnostics.os.replace", side_effect=OSError("blocked")):
                with self.assertRaises(OSError):
                    CategoryCounterFile(path).write({"recognized_next": 2})
            self.assertEqual(path.read_text(encoding="ascii"), '{"recognized_next":1}\n')
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_exact_action_uuid_mapping(self):
        self.assertEqual(ACTION_COMMANDS, {
            f"{PLUGIN_UUID}.nowplaying": "toggle",
            f"{PLUGIN_UUID}.previous": "previous",
            f"{PLUGIN_UUID}.toggle": "toggle",
            f"{PLUGIN_UUID}.next": "next",
            f"{PLUGIN_UUID}.volume-up": "volume-up",
            f"{PLUGIN_UUID}.volume-down": "volume-down",
            f"{PLUGIN_UUID}.mute-toggle": "mute-toggle",
        })

    def test_physical_transport_diagnostics_recognize_each_event_and_classify_results(self):
        diagnostics = TransportDiagnostics()
        for action in ("previous", "toggle", "next"):
            diagnostics.recognized_event(action)
        for status, expected in (
            ("ok", "success"), ("rejected", "rejected"),
            ("unavailable", "unavailable"), ("configuration", "compatibility"),
            ("incompatible", "compatibility"), ("stopped", "lifecycle"),
            ("queue_full", "lifecycle"), ("discarded", "lifecycle"),
        ):
            self.assertEqual(result_class(status), expected)
            diagnostics.result(status)
        diagnostics.command_post_attempt()
        self.assertEqual(diagnostics.snapshot(), {
            "command_post_attempt": 1,
            "recognized_next": 1,
            "recognized_previous": 1,
            "recognized_toggle": 1,
            "result_compatibility": 2,
            "result_lifecycle": 3,
            "result_rejected": 1,
            "result_success": 1,
            "result_unavailable": 1,
        })
        diagnostics._counters["command_post_attempt"] = MAX_COUNTER
        diagnostics.command_post_attempt()
        self.assertEqual(diagnostics.snapshot()["command_post_attempt"], MAX_COUNTER)

    def test_diagnostic_log_records_are_fixed_categories_without_event_data(self):
        class Recorder:
            def __init__(self):
                self.calls = []

            def info(self, message, *arguments):
                self.calls.append((message, arguments))

        recorder = Recorder()
        diagnostics = TransportDiagnostics(recorder)
        diagnostics.recognized_event("next")
        diagnostics.result("Bearer private-token")
        self.assertEqual(recorder.calls, [
            ("transport_diagnostic category=%s count=%d", ("recognized_next", 1)),
            ("transport_diagnostic category=%s count=%d", ("result_unavailable", 1)),
        ])

    def test_accepts_real_event_variants_and_ignores_malformed_or_unknown_actions(self):
        variants = [
            ({"uuid": f"{PLUGIN_UUID}.previous"}, "previous"),
            ({"action": f"{PLUGIN_UUID}.toggle"}, "toggle"),
            ({"context": f"{PLUGIN_UUID}.next___key___action"}, "next"),
        ]
        for event, expected in variants:
            with self.subTest(event=event):
                self.assertEqual(command_from_event(event), expected)
        for event in (None, [], {}, {"uuid": 3}):
            with self.subTest(event=event):
                self.assertIsNone(action_uuid_from_event(event))
        for event in ({"context": "bad"}, {"uuid": f"{PLUGIN_UUID}.progress"},
                      {"uuid": "com.other.plugin.next"}):
            with self.subTest(event=event):
                self.assertIsNone(command_from_event(event))

    def test_each_run_event_executes_exactly_one_command(self):
        client = RecordingClient()
        router = TransportRouter(client)
        self.assertTrue(router.start())
        mixed = (
            "previous", "volume-up", "volume-up", "volume-up",
            "toggle", "volume-down", "mute-toggle", "next",
        )
        for suffix in mixed:
            self.assertTrue(router.handle_run({"uuid": f"{PLUGIN_UUID}.{suffix}"}))
        self.assertTrue(wait_for(lambda: len(client.commands) == len(mixed)))
        self.assertEqual(client.commands, list(mixed))
        self.assertFalse(router.handle_run({"uuid": f"{PLUGIN_UUID}.progress"}))
        self.assertEqual(client.commands, list(mixed))
        self.assertTrue(router.stop())

    def test_router_is_single_run_authority_and_polls_after_every_successful_command(self):
        class Client:
            def __init__(self):
                self.commands = []

            def execute(self, command, cancelled=None):
                self.commands.append(command)
                status = "unavailable" if len(self.commands) == 3 else "ok"
                return BridgeResult(command, status)

        client = Client()
        routed, polls = [], []
        router = TransportRouter(client)
        router.configure_runtime(lambda event: routed.append(event) or True,
                                 lambda: polls.append(tuple(client.commands)))
        router.start()
        progress = {"uuid": f"{PLUGIN_UUID}.progress", "context": "progress"}
        self.assertTrue(router.handle_run(progress))
        self.assertEqual(routed, [progress])
        for uuid in (f"{PLUGIN_UUID}.toggle", f"{PLUGIN_UUID}.nowplaying",
                     f"{PLUGIN_UUID}.volume-up", f"{PLUGIN_UUID}.previous",
                     f"{PLUGIN_UUID}.next"):
            self.assertTrue(router.handle_run({"uuid": uuid}))
        self.assertTrue(wait_for(lambda: len(client.commands) == 5))
        self.assertTrue(wait_for(lambda: len(polls) == 4))
        self.assertEqual(client.commands, ["toggle", "toggle", "volume-up", "previous", "next"])
        self.assertEqual(polls, [
            ("toggle",),
            ("toggle", "toggle"),
            ("toggle", "toggle", "volume-up", "previous"),
            ("toggle", "toggle", "volume-up", "previous", "next"),
        ], "poll must follow every successful command and skip failures")
        self.assertTrue(router.stop())

    def test_bridge_client_matches_health_auth_and_command_contract(self):
        for command in ("previous", "toggle", "next", "volume-up", "volume-down", "mute-toggle"):
            with self.subTest(command=command):
                opener = RecordingOpener([Response(payload=health()), Response(payload={"ok": True})])
                result = BridgeClient(token_loader=lambda: TOKEN, opener=opener).execute(command)
                self.assertEqual(result, BridgeResult(command, "ok", 200))
                self.assertEqual(len(opener.calls), 2)
                health_request, health_timeout = opener.calls[0]
                command_request, command_timeout = opener.calls[1]
                self.assertEqual((health_request.full_url, health_request.method),
                                 (f"{BRIDGE_ORIGIN}/health", "GET"))
                self.assertEqual((command_request.full_url, command_request.method),
                                 (f"{BRIDGE_ORIGIN}/command/{command}", "POST"))
                health_headers = {key.lower(): value for key, value in health_request.header_items()}
                command_headers = {key.lower(): value for key, value in command_request.header_items()}
                self.assertEqual(health_headers["authorization"], f"Bearer {TOKEN}")
                self.assertEqual(command_headers["authorization"], f"Bearer {TOKEN}")
                self.assertEqual(command_headers["x-companion-instance"], INSTANCE_ID)
                self.assertEqual(command_headers["content-type"], "application/json")
                self.assertEqual(command_request.data, b"{}")
                self.assertEqual((health_timeout, command_timeout), (
                    BRIDGE_TIMEOUT_SECONDS, COMMAND_REQUEST_TIMEOUT_SECONDS,
                ))

    def test_authenticated_post_attempt_is_counted_only_after_compatible_health(self):
        diagnostics = TransportDiagnostics()
        compatible = RecordingOpener([Response(payload=health()), Response(payload={"ok": True})])
        self.assertTrue(BridgeClient(token_loader=lambda: TOKEN, opener=compatible,
                                     diagnostics=diagnostics).execute("next").ok)
        incompatible = RecordingOpener([Response(payload=health(api_major=2))])
        BridgeClient(token_loader=lambda: TOKEN, opener=incompatible,
                     diagnostics=diagnostics).execute("next")
        self.assertEqual(diagnostics.snapshot(), {"command_post_attempt": 1})

    def test_command_timeout_is_bounded_above_the_three_step_audio_budget(self):
        self.assertEqual(BRIDGE_TIMEOUT_SECONDS, 1.0)
        self.assertEqual(COMMAND_REQUEST_TIMEOUT_SECONDS, 10.0)
        self.assertGreater(COMMAND_REQUEST_TIMEOUT_SECONDS, 3 * 3.0)

    def test_intermediate_audio_error_does_not_block_next_fifo_command(self):
        class SequenceClient:
            def __init__(self):
                self.commands = []

            def execute(self, command, cancelled=None):
                self.commands.append(command)
                status = "rejected" if command == "volume-down" else "ok"
                return BridgeResult(command, status, 409 if status == "rejected" else 200)

        client = SequenceClient()
        router = TransportRouter(client)
        router.start()
        mixed = ("previous", "volume-up", "toggle", "volume-down", "mute-toggle", "next")
        for suffix in mixed:
            self.assertTrue(router.handle_run({"uuid": f"{PLUGIN_UUID}.{suffix}"}))
        self.assertTrue(wait_for(lambda: len(client.commands) == len(mixed)))
        self.assertEqual(client.commands, list(mixed))
        self.assertEqual(router.last_result, BridgeResult("next", "ok", 200))
        self.assertTrue(router.stop())

    def test_only_loopback_origins_are_allowed(self):
        BridgeClient(token_loader=lambda: TOKEN, origin="http://127.0.0.1:1")
        for origin in ("https://127.0.0.1:43821", "http://localhost:43821",
                       "http://0.0.0.0:43821", "http://example.com:43821"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                BridgeClient(token_loader=lambda: TOKEN, origin=origin)

    def test_future_bridge_origin_override_is_loopback_only_and_defaults_to_fixed_origin(self):
        self.assertEqual(bridge_origin_from_future(("unknown",)), BRIDGE_ORIGIN)
        override = "http://127.0.0.1:54321"
        self.assertEqual(bridge_origin_from_future((f"--bridge-origin={override}",)), override)
        with self.assertRaises(ValueError):
            BridgeClient(origin=bridge_origin_from_future(("--bridge-origin=http://example.com:1",)))
        with self.assertRaises(ValueError):
            bridge_origin_from_future(("--bridge-origin=http://127.0.0.1:1",
                                       "--bridge-origin=http://127.0.0.1:2"))

    def test_missing_or_invalid_token_never_sends_http(self):
        loaders = (
            lambda: None,
            lambda: "bad",
            lambda: (_ for _ in ()).throw(FileNotFoundError()),
        )
        for loader in loaders:
            opener = RecordingOpener([])
            result = BridgeClient(token_loader=loader, opener=opener).execute("next")
            self.assertEqual(result.status, "configuration")
            self.assertEqual(opener.calls, [])

    def test_timeout_error_and_non_2xx_are_observable_without_exceptions(self):
        cases = [
            ([TimeoutError("slow")], "unavailable"),
            ([Response(status=503)], "unavailable"),
            ([Response(payload=health()), TimeoutError("slow")], "unavailable"),
            ([Response(payload=health()), Response(status=409)], "rejected"),
        ]
        for responses, expected in cases:
            with self.subTest(expected=expected):
                result = BridgeClient(
                    token_loader=lambda: TOKEN, opener=RecordingOpener(responses)
                ).execute("toggle")
                self.assertEqual(result.status, expected)

    def test_real_http_error_is_rejected_with_status_without_reading_body(self):
        for command_status in (409, 503):
            with self.subTest(command_status=command_status):
                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        payload = json.dumps(health()).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)

                    def do_POST(self):
                        self.send_response(command_status)
                        self.send_header("Content-Length", "9")
                        self.end_headers()
                        self.wfile.write(b"sensitive")

                    def log_message(self, *_args):
                        pass

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    result = BridgeClient(
                        token_loader=lambda: TOKEN,
                        origin=f"http://127.0.0.1:{server.server_port}",
                    ).execute("next")
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()
                self.assertEqual(result, BridgeResult("next", "rejected", command_status))

    def test_incompatible_or_malformed_health_suppresses_command(self):
        for payload, expected in (
            (health(api_major=2), "incompatible"),
            (health(status="starting"), "unavailable"),
            (health(instance_id="bad"), "unavailable"),
            (health(api_minor=True), "unavailable"),
        ):
            with self.subTest(payload=payload):
                opener = RecordingOpener([Response(payload=payload)])
                result = BridgeClient(token_loader=lambda: TOKEN, opener=opener).execute("previous")
                self.assertEqual(result.status, expected)
                self.assertEqual(len(opener.calls), 1)

    def test_router_contains_unexpected_client_failure(self):
        class BrokenClient:
            def execute(self, _command, cancelled=None):
                raise RuntimeError("private detail")

        router = TransportRouter(BrokenClient())
        router.start()
        self.assertTrue(router.handle_run({"uuid": f"{PLUGIN_UUID}.next"}))
        self.assertTrue(wait_for(lambda: router.last_result is not None))
        self.assertEqual(router.last_result, BridgeResult("next", "unavailable"))
        self.assertTrue(router.stop())

    def test_router_reports_success_and_failure_categories_without_event_data(self):
        diagnostics = TransportDiagnostics()
        for status, category in (("ok", "success"), ("rejected", "rejected"),
                                 ("unavailable", "unavailable")):
            with self.subTest(status=status):
                router = TransportRouter(RecordingClient(status), diagnostics=diagnostics)
                self.assertTrue(router.start())
                self.assertTrue(router.handle_run({"uuid": f"{PLUGIN_UUID}.next"}))
                self.assertTrue(wait_for(lambda: router.last_result is not None))
                self.assertTrue(router.stop())
                self.assertGreaterEqual(diagnostics.snapshot()[f"result_{category}"], 1)
        snapshot = diagnostics.snapshot()
        self.assertEqual(snapshot["recognized_next"], 3)

    def test_blocked_worker_keeps_callback_immediate_and_preserves_order(self):
        class BlockingClient:
            def __init__(self):
                self.commands = []
                self.started = threading.Event()
                self.release = threading.Event()

            def execute(self, command, cancelled=None):
                self.commands.append(command)
                self.started.set()
                if len(self.commands) == 1:
                    self.release.wait(2)
                return BridgeResult(command, "ok", 200)

        client = BlockingClient()
        router = TransportRouter(client, queue_capacity=2)
        self.assertTrue(router.start())
        self.assertFalse(router.start())
        started_at = time.monotonic()
        self.assertTrue(router.handle_run({"uuid": f"{PLUGIN_UUID}.previous"}))
        self.assertLess(time.monotonic() - started_at, 0.05)
        self.assertTrue(client.started.wait(1))
        self.assertTrue(router.handle_run({"uuid": f"{PLUGIN_UUID}.toggle"}))
        self.assertTrue(router.handle_run({"uuid": f"{PLUGIN_UUID}.next"}))
        self.assertFalse(router.handle_run({"uuid": f"{PLUGIN_UUID}.previous"}))
        self.assertEqual(router.last_enqueue_result, BridgeResult("previous", "queue_full"))
        client.release.set()
        self.assertTrue(wait_for(lambda: len(client.commands) == 3))
        self.assertEqual(client.commands, ["previous", "toggle", "next"])
        self.assertTrue(router.stop())
        self.assertFalse(router.worker_alive)

    def test_stop_cancels_current_discards_pending_and_rejects_new_work(self):
        class CancellationAwareClient:
            def __init__(self):
                self.commands = []
                self.started = threading.Event()

            def execute(self, command, cancelled=None):
                self.commands.append(command)
                self.started.set()
                while not cancelled():
                    time.sleep(0.005)
                return BridgeResult(command, "stopped")

        client = CancellationAwareClient()
        router = TransportRouter(client, queue_capacity=2)
        router.start()
        self.assertTrue(router.handle_run({"uuid": f"{PLUGIN_UUID}.previous"}))
        self.assertTrue(client.started.wait(1))
        self.assertTrue(router.handle_run({"uuid": f"{PLUGIN_UUID}.toggle"}))
        self.assertTrue(router.handle_run({"uuid": f"{PLUGIN_UUID}.next"}))
        started_at = time.monotonic()
        self.assertTrue(router.stop(timeout=0.5))
        self.assertLess(time.monotonic() - started_at, 0.5)
        self.assertEqual(client.commands, ["previous"])
        self.assertEqual(router.discarded_count, 2)
        self.assertFalse(router.handle_run({"uuid": f"{PLUGIN_UUID}.next"}))
        self.assertEqual(router.last_enqueue_result, BridgeResult("next", "stopped"))
        self.assertTrue(router.stop(timeout=0))
        self.assertFalse(router.worker_alive)


if __name__ == "__main__":
    unittest.main()
