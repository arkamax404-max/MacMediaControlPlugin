import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from d200_bridge.macos_audio import MacOSOutputAudioController, MacOSOutputAudioGateway
from d200_bridge.state import MediaStateCache


class MacOSAudioTests(unittest.TestCase):
    def test_gateway_serializes_and_parses_live_output_contract(self):
        runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="42|true\n"))

        self.assertEqual(MacOSOutputAudioGateway(runner=runner).read_state(),
                         {"volume_percent": 42, "is_muted": True})

        script = runner.call_args.args[0][2]
        self.assertIn("set volumeSettings to get volume settings", script)
        self.assertIn("outputVolume as integer as text", script)
        self.assertIn('set mutedText to "false"', script)
        self.assertIn('set mutedText to "true"', script)

    def test_gateway_rejects_malformed_or_unsuccessful_output(self):
        for returncode, output in (
            (1, "42|true"),
            (0, "{output volume:42, input volume:100, alert volume:100, output muted:true}"),
            (0, "42|True"),
            (0, "101|false"),
            (0, "42|false|unexpected"),
        ):
            with self.subTest(returncode=returncode, output=output):
                runner = Mock(return_value=SimpleNamespace(returncode=returncode, stdout=output))
                self.assertIsNone(MacOSOutputAudioGateway(runner=runner).read_state())

    def test_refresh_publishes_bounded_macos_output_state(self):
        cache = MediaStateCache()
        gateway = Mock(read_state=Mock(return_value={"volume_percent": 140, "is_muted": True}))

        self.assertTrue(MacOSOutputAudioController(cache, gateway=gateway).refresh())

        self.assertEqual(cache.get().public()["volume_percent"], 100)
        self.assertTrue(cache.get().public()["is_muted"])

    def test_volume_command_is_bounded_and_proves_readback_without_live_output(self):
        cache = MediaStateCache()
        gateway = Mock(
            read_state=Mock(side_effect=(
                {"volume_percent": 98, "is_muted": False},
                {"volume_percent": 100, "is_muted": False},
            )),
            set_volume=Mock(return_value=True),
        )

        result = MacOSOutputAudioController(cache, gateway=gateway).command("volume-up")

        gateway.set_volume.assert_called_once_with(100)
        self.assertEqual((result.status, result.applied_count, result.failed_count), ("ok", 1, 0))
        self.assertEqual(result.public()["volume_percent"], 100)

    def test_mute_toggle_and_failures_are_explicit_output_failures(self):
        cache = MediaStateCache()
        gateway = Mock(
            read_state=Mock(return_value={"volume_percent": 42, "is_muted": False}),
            set_muted=Mock(return_value=False),
        )
        controller = MacOSOutputAudioController(cache, gateway=gateway)

        result = controller.command("mute-toggle")

        gateway.set_muted.assert_called_once_with(True)
        self.assertEqual((result.status, result.applied_count, result.failed_count), ("failed", 0, 1))
        self.assertFalse(cache.get().public()["audio_available"])

    def test_unknown_commands_are_rejected_without_calling_output_gateway(self):
        gateway = Mock()
        with self.assertRaises(ValueError):
            MacOSOutputAudioController(MediaStateCache(), gateway=gateway).command("next")
        self.assertEqual(gateway.method_calls, [])
