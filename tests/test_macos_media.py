import asyncio
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

from d200_bridge.macos_media import MacOSCurrentMediaAdapter
from d200_bridge.state import MediaStateCache


ARTWORK = b"generic-current-media-artwork"
PNG_URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="


class MacOSCurrentMediaAdapterTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _processor():
        from d200_bridge.artwork import ArtworkProcessor, ArtworkVariants
        processor = ArtworkProcessor(cache_size=1)
        processor._decode_encode = Mock(return_value=ArtworkVariants(PNG_URI, PNG_URI, (PNG_URI,) * 4))
        return processor

    @staticmethod
    def _media():
        return {"state": "playing", "title": "Track", "artist": "Artist", "duration": 180, "position": 30,
                "artwork": ARTWORK}

    @staticmethod
    def _clock(*values):
        values = iter(values)
        return lambda: next(values)

    async def test_refresh_retains_playing_timeline_anchor_when_mediaremote_position_is_unchanged(self):
        first = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        second = datetime(2026, 9, 3, 12, 0, 5, tzinfo=timezone.utc)
        gateway = Mock(read_now_playing=Mock(side_effect=[
            {"status": "ok", "media": self._media()}, {"status": "ok", "media": self._media()},
        ]))
        cache = MediaStateCache()
        adapter = MacOSCurrentMediaAdapter(cache, gateway=gateway, clock=self._clock(first, second))

        await adapter.refresh()
        await adapter.refresh()

        self.assertEqual(cache.get().position_updated_at, first.isoformat())

    async def test_refresh_uses_valid_mediaremote_timeline_anchor(self):
        source_anchor = "2026-09-03T11:59:58Z"
        media = {**self._media(), "position_updated_at": source_anchor}
        cache = MediaStateCache()
        adapter = MacOSCurrentMediaAdapter(cache, gateway=Mock(read_now_playing=Mock(
            return_value={"status": "ok", "media": media})),
                                           clock=lambda: datetime(2026, 9, 3, 12, tzinfo=timezone.utc))

        await adapter.refresh()

        self.assertEqual(cache.get().position_updated_at, "2026-09-03T11:59:58+00:00")

    async def test_refresh_reanchors_on_material_position_change(self):
        first = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        second = datetime(2026, 9, 3, 12, 0, 5, tzinfo=timezone.utc)
        gateway = Mock(read_now_playing=Mock(side_effect=[
            {"status": "ok", "media": self._media()},
            {"status": "ok", "media": {**self._media(), "position": 35}},
        ]))
        cache = MediaStateCache()
        adapter = MacOSCurrentMediaAdapter(cache, gateway=gateway, clock=self._clock(first, second))

        await adapter.refresh()
        await adapter.refresh()

        self.assertEqual(cache.get().position_updated_at, second.isoformat())

    async def test_refresh_reanchors_on_playback_state_change(self):
        first = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        second = datetime(2026, 9, 3, 12, 0, 5, tzinfo=timezone.utc)
        third = datetime(2026, 9, 3, 12, 0, 10, tzinfo=timezone.utc)
        gateway = Mock(read_now_playing=Mock(side_effect=[
            {"status": "ok", "media": self._media()},
            {"status": "ok", "media": {**self._media(), "state": "paused"}},
            {"status": "ok", "media": self._media()},
        ]))
        cache = MediaStateCache()
        adapter = MacOSCurrentMediaAdapter(cache, gateway=gateway, clock=self._clock(first, second, third))

        await adapter.refresh()
        await adapter.refresh()
        self.assertEqual(cache.get().position_updated_at, second.isoformat())
        await adapter.refresh()

        self.assertEqual(cache.get().position_updated_at, third.isoformat())

    async def test_refresh_reanchors_on_duration_or_rate_change(self):
        first = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        second = datetime(2026, 9, 3, 12, 0, 5, tzinfo=timezone.utc)
        third = datetime(2026, 9, 3, 12, 0, 10, tzinfo=timezone.utc)
        gateway = Mock(read_now_playing=Mock(side_effect=[
            {"status": "ok", "media": self._media()},
            {"status": "ok", "media": {**self._media(), "duration": 200}},
            {"status": "ok", "media": {**self._media(), "duration": 200, "playback_rate": 1.25}},
        ]))
        cache = MediaStateCache()
        adapter = MacOSCurrentMediaAdapter(cache, gateway=gateway, clock=self._clock(first, second, third))

        await adapter.refresh()
        await adapter.refresh()
        self.assertEqual(cache.get().position_updated_at, second.isoformat())
        await adapter.refresh()

        self.assertEqual(cache.get().position_updated_at, third.isoformat())

    async def test_refresh_reanchors_after_media_becomes_unavailable(self):
        first = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        third = datetime(2026, 9, 3, 12, 0, 10, tzinfo=timezone.utc)
        gateway = Mock(read_now_playing=Mock(side_effect=[
            {"status": "ok", "media": self._media()}, {"status": "unavailable", "media": None},
            {"status": "ok", "media": self._media()},
        ]))
        cache = MediaStateCache()
        adapter = MacOSCurrentMediaAdapter(cache, gateway=gateway, clock=self._clock(first, third))

        await adapter.refresh()
        await adapter.refresh()
        self.assertFalse(cache.get().available)
        await adapter.refresh()

        self.assertEqual(cache.get().position_updated_at, third.isoformat())

    async def test_refresh_processes_generic_artwork_bytes_through_existing_cache_contract(self):
        cache, processor = MediaStateCache(), self._processor()
        gateway = Mock(read_now_playing=Mock(return_value={"status": "ok", "media": self._media()}))
        adapter = MacOSCurrentMediaAdapter(cache, gateway=gateway, artwork_processor=processor)

        await adapter.refresh()

        artwork_id = hashlib.sha256(ARTWORK).hexdigest()
        self.assertEqual(cache.get().artwork_id, artwork_id)
        self.assertIsNotNone(processor.get_cached(artwork_id))
        self.assertNotIn(base64.b64encode(ARTWORK).decode(), json.dumps(cache.get().public()))

    async def test_refresh_is_off_event_loop_and_unavailable_data_fails_closed(self):
        cache = MediaStateCache()
        reader = Mock(return_value={"status": "unavailable", "media": None})
        adapter = MacOSCurrentMediaAdapter(cache, gateway=Mock(read_now_playing=reader),
                                          clock=lambda: datetime(2026, 9, 3, tzinfo=timezone.utc))
        with patch("d200_bridge.macos_media.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *args: fn(*args))) as thread:
            await adapter.refresh()
        thread.assert_awaited_once_with(reader)
        self.assertFalse(cache.get().available)

    async def test_transport_is_generic_current_source_and_preserves_adapter_operations(self):
        cache = MediaStateCache()
        command = Mock(return_value=True)
        adapter = MacOSCurrentMediaAdapter(cache, gateway=Mock(read_now_playing=Mock(return_value={"status": "ok", "media": self._media()}),
                                                               command=command))
        await adapter.start()
        for action in ("previous", "toggle", "next"):
            self.assertTrue(await adapter.command(action))
        await adapter.stop()
        self.assertFalse(cache.get().available)
        self.assertEqual(command.call_args_list, [(("previous",),), (("toggle",),), (("next",),)])

    async def test_invalid_artwork_and_unknown_media_state_do_not_publish_stale_media(self):
        cache = MediaStateCache()
        gateway = Mock(read_now_playing=Mock(return_value={"status": "ok", "media": {**self._media(), "state": "buffering", "artwork": b"bad"}}))
        await MacOSCurrentMediaAdapter(cache, gateway=gateway).refresh()
        self.assertFalse(cache.get().available)

    def test_mediaremote_gateway_reads_native_json_and_decodes_artwork(self):
        from d200_bridge.macos_mediaremote import MediaRemoteGateway
        encoded = base64.b64encode(ARTWORK).decode("ascii")
        runner = Mock(return_value=Mock(returncode=0, stdout=json.dumps({
            "status": "ok", "media": {**self._media(), "artwork": encoded},
        })))

        result = MediaRemoteGateway(runner=runner, helper_path="MediaRemoteHelper").read_now_playing()

        self.assertEqual(result, {"status": "ok", "media": self._media()})
        self.assertEqual(runner.call_args.args[0], ["MediaRemoteHelper", "read"])
        self.assertNotIn("osascript", repr(runner.call_args))

    def test_mediaremote_gateway_maps_only_generic_transport_commands(self):
        from d200_bridge.macos_mediaremote import MediaRemoteGateway
        runner = Mock(return_value=Mock(returncode=0, stdout='{"status":"ok"}'))
        gateway = MediaRemoteGateway(runner=runner, helper_path="MediaRemoteHelper")

        for action in ("previous", "toggle", "next"):
            self.assertTrue(gateway.command(action))
        self.assertEqual([call.args[0][1] for call in runner.call_args_list],
                         ["previous", "toggle", "next"])
        with self.assertRaisesRegex(ValueError, "Unsupported command"):
            gateway.command("pause")

    def test_mediaremote_gateway_preserves_rejected_transport_protocol(self):
        from d200_bridge.macos_mediaremote import MediaRemoteGateway
        gateway = MediaRemoteGateway(runner=Mock(return_value=Mock(returncode=0, stdout='{"status":"rejected"}')),
                                     helper_path="MediaRemoteHelper")

        self.assertFalse(gateway.command("toggle"))
        self.assertIsNone(gateway.last_diagnostic)

    def test_mediaremote_gateway_reports_bounded_redacted_failure_diagnostics(self):
        from d200_bridge.macos_mediaremote import MediaRemoteGateway
        secret = "Sensitive title /Users/person/token artwork-bytes"
        cases = (
            (Mock(side_effect=FileNotFoundError(secret)), "launch", None),
            (Mock(side_effect=subprocess.TimeoutExpired("MediaRemoteHelper", 3, output=secret)), "timeout", None),
            (Mock(return_value=Mock(returncode=78, stdout=secret, stderr=secret)), "exit", 78),
            (Mock(return_value=Mock(returncode=0, stdout="not-json", stderr=secret)), "parse", None),
            (Mock(return_value=Mock(returncode=0, stdout='{"status":"ok","media":{"title":"T"}}', stderr=secret)),
             "schema", None),
        )
        for runner, stage, exit_code in cases:
            with self.subTest(stage=stage):
                gateway = MediaRemoteGateway(runner=runner, helper_path="MediaRemoteHelper")
                self.assertEqual(gateway.read_now_playing(), {"status": "unavailable", "media": None})
                self.assertEqual(gateway.last_diagnostic, {"stage": stage, "exit_code": exit_code})
                self.assertNotIn(secret, repr(gateway.last_diagnostic))

    def test_mediaremote_gateway_rejects_non_safe_exit_code(self):
        from d200_bridge.macos_mediaremote import MediaRemoteGateway
        runner = Mock(return_value=Mock(returncode=1024, stdout="", stderr=""))

        gateway = MediaRemoteGateway(runner=runner, helper_path="MediaRemoteHelper")

        self.assertFalse(gateway.command("next"))
        self.assertEqual(gateway.last_diagnostic, {"stage": "exit", "exit_code": None})

    def test_mediaremote_diagnostic_log_never_contains_media_or_paths(self):
        from d200_bridge.logging_config import SafeEventFilter
        from d200_bridge.macos_mediaremote import MediaRemoteGateway
        runner = Mock(return_value=Mock(returncode=78, stdout="Title", stderr="/Users/person/token"))
        logger = Mock()
        with patch("d200_bridge.macos_mediaremote.logging.getLogger", return_value=logger):
            MediaRemoteGateway(runner=runner, helper_path="MediaRemoteHelper").read_now_playing()
        message, stage, code = logger.info.call_args.args
        record = logging.LogRecord("d200_bridge", logging.INFO, "", 0, message, (stage, code), None)
        self.assertTrue(SafeEventFilter().filter(record))
        self.assertEqual(record.getMessage(), "mediaremote_failure stage=exit exit=78")
        self.assertNotIn("Title", record.getMessage())
        self.assertNotIn("/Users", record.getMessage())

    @unittest.skipUnless(sys.platform == "darwin", "MediaRemote helper seam requires macOS Foundation")
    def test_native_transport_waits_for_observable_effect_and_never_emits_media(self):
        source = Path(__file__).parents[1] / "d200_bridge" / "native" / "MediaRemoteHelper.m"
        fake = r'''
#import <Foundation/Foundation.h>
#import <dispatch/dispatch.h>
typedef void (^MRNowPlayingCallback)(CFDictionaryRef information);
static BOOL sent = NO;
static int readsAfterSend = 0;
void MRMediaRemoteSendCommand(int command, id options) { sent = YES; }
void MRMediaRemoteGetNowPlayingInfo(dispatch_queue_t queue, MRNowPlayingCallback callback) {
  dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_MSEC), queue, ^{
    NSString *scenario = [[[NSProcessInfo processInfo] environment] objectForKey:@"MEDIAREMOTE_HELPER_TEST_SCENARIO"];
    BOOL changed = sent && ![scenario isEqualToString:@"none"] && ++readsAfterSend >= 2;
    callback((__bridge CFDictionaryRef)@{
      @"kMRMediaRemoteNowPlayingInfoPlaybackRate": changed ? @1 : @0,
      @"kMRMediaRemoteNowPlayingInfoUniqueIdentifier": changed ? @"next" : @"first",
      @"kMRMediaRemoteNowPlayingInfoElapsedTime": changed ? @90 : @30,
      @"kMRMediaRemoteNowPlayingInfoTitle": @"Sensitive title",
      @"kMRMediaRemoteNowPlayingInfoArtist": @"Sensitive artist"
    });
  });
}
'''
        with tempfile.TemporaryDirectory() as directory:
            fake_path, helper = Path(directory) / "fake.m", Path(directory) / "MediaRemoteHelper"
            fake_path.write_text(fake, encoding="utf-8")
            subprocess.run(["xcrun", "--sdk", "macosx", "clang", "-fobjc-arc", "-framework", "Foundation",
                            "-o", str(helper), str(source), str(fake_path)], check=True, capture_output=True, text=True)
            delayed = subprocess.run([str(helper), "toggle"], env={**os.environ, "MEDIAREMOTE_HELPER_TEST_SCENARIO": "delayed"},
                                     check=True, capture_output=True, text=True, timeout=3)
            next_result = subprocess.run([str(helper), "next"], env={**os.environ, "MEDIAREMOTE_HELPER_TEST_SCENARIO": "delayed"},
                                         check=True, capture_output=True, text=True, timeout=3)
            previous_result = subprocess.run([str(helper), "previous"], env={**os.environ, "MEDIAREMOTE_HELPER_TEST_SCENARIO": "delayed"},
                                             check=True, capture_output=True, text=True, timeout=3)
            no_effect = subprocess.run([str(helper), "next"], env={**os.environ, "MEDIAREMOTE_HELPER_TEST_SCENARIO": "none"},
                                       check=True, capture_output=True, text=True, timeout=3)
        self.assertEqual(delayed.stdout, '{"status":"ok"}\n')
        self.assertEqual(next_result.stdout, '{"status":"ok"}\n')
        self.assertEqual(previous_result.stdout, '{"status":"ok"}\n')
        self.assertEqual(no_effect.stdout, '{"status":"rejected"}\n')
        self.assertNotIn("Sensitive", delayed.stdout + next_result.stdout + previous_result.stdout + no_effect.stdout)
