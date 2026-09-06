import base64
import sys
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "com.arkamax404.mediacontrold200.ulanziPlugin" / "runtime" / "python"
sys.path.insert(0, str(RUNTIME))

from artwork_bundle import ArtworkBundle  # noqa: E402
from largeitem_action import ACTION_UUID, LargeItemActionModel  # noqa: E402
from largeitem_renderer import (HEIGHT, WIDTH, LargeItemSettings, LargeItemView,
                                render_largeitem, render_largeitem_data_uri)  # noqa: E402
from now_playing_action import MediaSnapshot  # noqa: E402
from progress_state import ProgressState  # noqa: E402
from bridge_client import BridgeStateResult  # noqa: E402
from progress_action import ProgressActionModel  # noqa: E402
from progress_scheduler import ProgressScheduler  # noqa: E402


PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


class LargeItemTests(unittest.TestCase):
    def test_renderer_is_deterministic_exact_canvas_and_xml_safe(self):
        view = LargeItemView("ready", '<Track & "Title">', "Artist 😀", True, PNG,
                             .25, "0:30", "-1:30", LargeItemSettings())
        svg = render_largeitem(view)
        root = ET.fromstring(svg)
        self.assertEqual((WIDTH, HEIGHT), (458, 196))
        self.assertEqual(root.attrib, {"width": "458", "height": "196",
                                      "viewBox": "0 0 458 196"})
        uri = render_largeitem_data_uri(view)
        self.assertEqual(uri, render_largeitem_data_uri(view))
        self.assertEqual(base64.b64decode(uri.split(",", 1)[1]).decode(), svg)
        self.assertIn("&lt;Track &amp;", svg)
        self.assertIn("&quot;Title&quot;&gt;", svg)

    def test_action_accepts_only_center_and_persists_small_view_mode(self):
        model = LargeItemActionModel()
        self.assertEqual(model.add({"uuid": ACTION_UUID,
                                    "context": f"{ACTION_UUID}___1_1___id"}), ())
        context = f"{ACTION_UUID}___3_2___id"
        request = model.add({"uuid": ACTION_UUID, "context": context, "param": {}})[0]
        self.assertEqual(model.persistence_requests()[0].settings["SmallViewMode"], 2)
        media = MediaSnapshot(True, True, True, "Track", "Artist", "a" * 64, "ready")
        progress = ProgressState(True, True, True, True, 30, 120, 1, NOW, "ready", "")
        bundle = ArtworkBundle("a" * 64, PNG, PNG, ("1", "2", "3", "4"))
        intent = model.render(request, media, progress, bundle, lambda: NOW)
        self.assertTrue(intent.data_uri.startswith("data:image/svg+xml;base64,"))
        self.assertTrue(model.acknowledge(intent, True))
        self.assertIsNone(model.render(request, media, progress, bundle, lambda: NOW))

    def test_scheduler_ticks_large_timeline_and_shuts_down_cleanly(self):
        started = time.monotonic()
        class Client:
            def get_state(self, cancelled=None):
                return BridgeStateResult("ok", {
                    "available": True, "is_playing": True, "title": "Track",
                    "artist": "Artist", "artwork_id": None,
                    "timeline_available": True, "position_seconds": 30,
                    "duration_seconds": 120, "playback_rate": 1,
                    "position_updated_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
                }, 200)
        class Api:
            def __init__(self): self.displays = []
            def setSettings(self, *_args): return True
            def setBaseDataIcon(self, context, data, _text):
                self.displays.append((context, data)); return True
        api = Api()
        scheduler = ProgressScheduler(
            api, Client(), ProgressActionModel(),
            clock=lambda: NOW + timedelta(seconds=time.monotonic() - started),
            poll_interval=10, tick_interval=.02,
        )
        context = f"{ACTION_UUID}___3_2___tick"
        self.assertTrue(scheduler.handle_add({"uuid": ACTION_UUID, "context": context}))
        scheduler.start()
        deadline = time.monotonic() + .5
        while len(api.displays) < 2 and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertGreaterEqual(len(api.displays), 2)
        self.assertNotEqual(api.displays[0][1], api.displays[-1][1])
        self.assertTrue(scheduler.stop(.5))


if __name__ == "__main__":
    unittest.main()
