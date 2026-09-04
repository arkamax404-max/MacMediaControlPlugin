import importlib.util
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]
RUNTIME = ROOT / "com.arkamax404.mediacontrold200.ulanziPlugin" / "runtime" / "python"


def load_module(name):
    sys.path.insert(0, str(RUNTIME))
    try:
        path = RUNTIME / f"{name}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(RUNTIME))


supervisor_module = load_module("companion_supervisor")
bridge_module = load_module("bridge_client")


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, amount):
        self.value += amount


class CompanionSupervisorTests(unittest.TestCase):
    def health(self, status="compatible", instance_id="123e4567-e89b-42d3-a456-426614174000"):
        return bridge_module.BridgeHealthResult(status, instance_id if status == "compatible" else None)

    def test_healthy_authenticated_companion_is_external_and_never_spawned_or_stopped(self):
        client = Mock(probe_health=Mock(return_value=self.health()))
        spawn = Mock()
        supervisor = supervisor_module.CompanionSupervisor(client_factory=lambda: client, popen=spawn)

        self.assertEqual(supervisor.ensure_ready(), supervisor_module.CompanionStartResult("ready", False))
        supervisor.shutdown()
        spawn.assert_not_called()
        client.stop_owned.assert_not_called()

    def test_spawns_only_frozen_reserved_mode_then_records_authenticated_instance(self):
        clock = Clock()
        child = Mock(); child.poll.return_value = None; child.stderr = io.BytesIO()
        client = Mock(probe_health=Mock(side_effect=[self.health("unavailable"), self.health()]))
        spawn = Mock(return_value=child)
        supervisor = supervisor_module.CompanionSupervisor(
            client_factory=lambda: client, popen=spawn, clock=clock, sleep=clock.sleep,
            executable="/runtime/MediaControlRuntime",
        )

        self.assertEqual(supervisor.ensure_ready(), supervisor_module.CompanionStartResult("ready", True))
        spawn.assert_called_once_with(
            ["/runtime/MediaControlRuntime", "--d200-bridge"], shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        supervisor.shutdown()
        client.stop_owned.assert_called_once_with("123e4567-e89b-42d3-a456-426614174000")
        child.wait.assert_called_once_with(timeout=supervisor_module.STOP_WAIT_SECONDS)

    def test_child_exit_reports_safe_stage_exit_code_and_never_stops_or_restarts(self):
        clock = Clock()
        child = Mock(poll=Mock(return_value=1)); child.stderr = io.BytesIO(b"startup_failed")
        client = Mock(probe_health=Mock(return_value=self.health("unavailable")))
        startup_diagnostics = Mock()
        supervisor = supervisor_module.CompanionSupervisor(
            client_factory=lambda: client, popen=Mock(return_value=child), clock=clock, sleep=clock.sleep,
            startup_diagnostic_file=startup_diagnostics,
        )
        result = supervisor.ensure_ready()
        self.assertEqual(result.status, "companion_start_failed")
        self.assertEqual((result.stage, result.exit_code), ("exited", 1))
        startup_diagnostics.write.assert_called_once_with("exited", 1, "startup_failed")
        supervisor.shutdown()
        client.stop_owned.assert_not_called()

    def test_health_timeout_reports_fixed_stage_without_stopping_or_restarting(self):
        clock = Clock()
        child = Mock(poll=Mock(return_value=None)); child.stderr = io.BytesIO()
        client = Mock(probe_health=Mock(return_value=self.health("unavailable")))
        supervisor = supervisor_module.CompanionSupervisor(
            client_factory=lambda: client, popen=Mock(return_value=child), clock=clock, sleep=clock.sleep,
            readiness_timeout=0.2, readiness_poll=0.1,
        )
        result = supervisor.ensure_ready()
        self.assertEqual((result.stage, result.exit_code, result.stderr), ("health-timeout", None, ""))
        supervisor.shutdown()
        client.stop_owned.assert_not_called()

    def test_stderr_is_bounded_redacted_and_never_exposes_private_data(self):
        secret = b"A" * 43
        private = secret + b" /Users/person/private.mp3 artwork data:image/png;base64,AAAA --host-arg"
        buffer = supervisor_module._RedactedStderrBuffer(maximum=8)
        buffer.drain(io.BytesIO(private + b"x" * 4096))
        self.assertEqual(buffer.snapshot(), "redacted")
        self.assertLessEqual(buffer._seen, 8)
        self.assertNotIn(secret.decode(), buffer.snapshot())
        self.assertNotIn("Users", buffer.snapshot())

    def test_startup_log_exposes_only_fixed_diagnostic_categories(self):
        logger = Mock()
        child = Mock(poll=Mock(return_value=1)); child.stderr = io.BytesIO(b"SECRET /Users/person/a.mp3")
        client = Mock(probe_health=Mock(return_value=self.health("unavailable")))
        result = supervisor_module.CompanionSupervisor(
            client_factory=lambda: client, popen=Mock(return_value=child), logger=logger,
        ).ensure_ready()
        self.assertEqual(result.stderr, "redacted")
        self.assertNotIn("SECRET", str(logger.info.call_args))
        self.assertNotIn("Users", str(logger.info.call_args))

    def test_exit_code_is_limited_to_unsigned_byte(self):
        self.assertEqual(supervisor_module._safe_exit_code(255), 255)
        for value in (-1, 256, True, "1"):
            with self.subTest(value=value):
                self.assertIsNone(supervisor_module._safe_exit_code(value))

    def test_spawn_failure_and_stop_timeout_never_kill_a_process(self):
        unavailable = Mock(probe_health=Mock(return_value=self.health("unavailable")))
        supervisor = supervisor_module.CompanionSupervisor(client_factory=lambda: unavailable,
                                                            popen=Mock(side_effect=OSError()))
        self.assertEqual(supervisor.ensure_ready().status, "companion_start_failed")

        clock = Clock(); child = Mock(); child.poll.return_value = None; child.stderr = io.BytesIO()
        client = Mock(probe_health=Mock(side_effect=[self.health("unavailable"), self.health()]))
        child.wait.side_effect = subprocess.TimeoutExpired("runtime", 3)
        supervisor = supervisor_module.CompanionSupervisor(client_factory=lambda: client, popen=Mock(return_value=child), clock=clock, sleep=clock.sleep)
        supervisor.ensure_ready(); supervisor.shutdown()
        child.kill.assert_not_called()
        child.terminate.assert_not_called()

    def test_startup_diagnostic_failure_does_not_change_lifecycle_result(self):
        unavailable = Mock(probe_health=Mock(return_value=self.health("unavailable")))
        diagnostics = Mock(write=Mock(side_effect=OSError("blocked")))
        result = supervisor_module.CompanionSupervisor(
            client_factory=lambda: unavailable, popen=Mock(side_effect=OSError()),
            startup_diagnostic_file=diagnostics,
        ).ensure_ready()
        self.assertEqual((result.status, result.stage, result.exit_code, result.stderr), (
            "companion_start_failed", "spawned", None, ""))
        diagnostics.write.assert_called_once_with("spawned", None, "none")


class BridgeStopTests(unittest.TestCase):
    TOKEN = "A" * 43
    INSTANCE = "123e4567-e89b-42d3-a456-426614174000"

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, _size=-1): return b'{}'

    def test_owned_stop_rechecks_health_and_sends_exact_authenticated_instance(self):
        health = self.Response(); health.read = lambda _size=-1: (
            b'{"service":"d200-gsmtc-bridge","api_major":1,"api_minor":0,'
            b'"status":"ready","instance_id":"123e4567-e89b-42d3-a456-426614174000"}'
        )
        opener = Mock(side_effect=[health, self.Response()])
        client = bridge_module.BridgeClient(token_loader=lambda: self.TOKEN, opener=opener)
        self.assertTrue(client.stop_owned(self.INSTANCE))
        request = opener.call_args_list[1].args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:43821/lifecycle/stop")
        self.assertEqual(dict(request.header_items())["X-companion-instance"], self.INSTANCE)

    def test_failed_startup_status_is_user_safe(self):
        progress = load_module("progress_state")
        self.assertEqual(progress.unavailable_progress_state("companion_start_failed").label,
                         "Companion unavailable")


if __name__ == "__main__":
    unittest.main()
