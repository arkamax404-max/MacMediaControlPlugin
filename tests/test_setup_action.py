import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "com.arkamax404.mediacontrold200.ulanziPlugin" / "runtime" / "python"
sys.path.insert(0, str(RUNTIME))

from setup_action import (ACTION_UUID, BUILTIN_UUID, LARGEITEM_UUID,
                          SetupActionController, probe_setup_action)  # noqa: E402


SETUP_ID = "30000000-0000-4000-8000-000000000000"
CONTEXT = f"{ACTION_UUID}___1_1___{SETUP_ID}"


def fixture(root: Path, center=BUILTIN_UUID, params=None):
    group, page = "group-a", "page-a"
    page_root = root / "ProfilesV2" / group / "Profiles" / page
    page_root.mkdir(parents=True)
    (root / "Config").mkdir()
    (root / "Config" / "setting_source.json").write_text(json.dumps({
        "Devices": [{"CurrentProfile": "Test", "CurrentDevice": "device-a"}]
    }), "utf-8")
    (root / "ProfilesV2" / group / "manifest.json").write_text(json.dumps({
        "Name": "Test", "Device": {"UUID": "device-a", "Model": "D200"},
        "Pages": {"Current": page, "Pages": [page]},
    }), "utf-8")
    center_entry = {"Action": center, "ActionID": "40000000-0000-4000-8000-000000000000"}
    if params is not None:
        center_entry["ActionParam"] = params
    if center == LARGEITEM_UUID:
        center_entry.update({
            "LinkedTitle": True, "Name": "Large Now Playing", "State": 0,
            "Plugin": {"Name": "Media Control for D200",
                       "UUID": "com.arkamax404.ulanzi.mediacontrol", "Version": "2.2.0"},
            "ViewParam": [{"Icon": "", "IconRel": "", "Name": "Large Now Playing"}],
        })
    (page_root / "manifest.json").write_text(json.dumps({"Controllers": [{
        "Type": "Keypad", "Actions": {"1_1": {"Action": ACTION_UUID, "ActionID": SETUP_ID},
                                          "3_2": center_entry}}]}), "utf-8")


class SetupActionTests(unittest.TestCase):
    def test_probe_uses_active_profiles_v2_page_and_classifies_canonical_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture(root)
            self.assertEqual(probe_setup_action(root, SETUP_ID).status, "Ready")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture(root, LARGEITEM_UUID, {"SmallViewMode": 2})
            self.assertEqual(probe_setup_action(root, SETUP_ID).status, "Installed")

    def test_controller_launches_only_matching_operation_and_fails_source_without_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture(root)
            launches, labels = [], []
            class Api:
                def setPathIcon(self, _context, _icon, label): labels.append(label); return True
                def sendToPropertyInspector(self, *_args): return True
            controller = SetupActionController(
                Api(), lambda: root,
                helper_launcher=lambda *args: launches.append(args) or True,
                helper_status=lambda: None,
            )
            self.assertTrue(controller.add({"uuid": ACTION_UUID, "context": CONTEXT}))
            self.assertTrue(controller.run({"uuid": ACTION_UUID, "context": CONTEXT}))
            deadline = time.monotonic() + 1
            while not launches and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertEqual(launches[0][:3], (SETUP_ID, "1_1", "install"))
            self.assertEqual(labels[-1], "Waiting for Studio to close")
            controller.shutdown()


if __name__ == "__main__":
    unittest.main()
