from __future__ import annotations

import json
import hashlib
import os
import stat
import subprocess
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ACTION_UUID = "com.arkamax404.ulanzi.mediacontrol.setup-large-display"
LARGEITEM_UUID = "com.arkamax404.ulanzi.mediacontrol.largeitem-nowplaying"
BUILTIN_UUID = "com.ulanzi.ulanzideck.smallwindow.window"
ICON = "./assets/setup-large-display.svg"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class SetupProbe:
    status: str
    reason: str
    profile_name: str = ""
    setup_key: str | None = None


@dataclass
class _Context:
    action_id: str
    key: str
    generation: int
    active: bool = True
    status: str = "Ready"
    reason: str = "Press Setup to verify the active page"
    profile_name: str = ""
    operation: str = "install"
    launch_reserved: bool = False
    handshake_id: str | None = None


def default_data_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "Ulanzi" / "UlanziDeck"


def default_state_root() -> Path:
    return (Path.home() / "Library" / "Application Support" /
            "GSMTCD200Controller" / "large-display-setup")


def default_helper_launcher(action_id: str, key: str, operation: str,
                            handshake_id: str) -> bool:
    node = os.environ.get("MEDIA_CONTROL_NODE_EXECUTABLE")
    plugin_root = os.environ.get("MEDIA_CONTROL_PLUGIN_ROOT")
    if not node or not plugin_root:
        return False
    node_path = Path(node)
    root = Path(plugin_root)
    helper = root / "helper" / "Invoke-MediaControlSetup.mjs"
    try:
        node_info = node_path.stat()
        helper_info = helper.lstat()
        if (not node_path.is_absolute() or not stat.S_ISREG(node_info.st_mode)
                or not root.is_absolute() or not stat.S_ISREG(helper_info.st_mode)
                or helper.is_symlink()):
            return False
        helper.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    subprocess.Popen(
        [str(node_path), str(helper), "--mode", "assistant", "--operation", operation,
         "--plugin-root", str(root), "--pressed-key", key, "--setup-action-id", action_id,
         "--state-root", str(default_state_root()), "--handshake-id", handshake_id],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, start_new_session=True,
    )
    return True


def default_helper_status() -> Mapping[str, object] | None:
    value = _read_json(default_state_root() / "last-diagnostic.json")
    return value if isinstance(value, Mapping) else None


def probe_setup_action(data_root: Path, action_id: str) -> SetupProbe:
    action_id = _canonical_uuid(action_id)
    root = Path(data_root)
    state = _read_json(root / "Config" / "setting_source.json")
    if not isinstance(state, Mapping) or not isinstance(state.get("Devices"), list):
        return SetupProbe("Failed", "ProfilesV2 settings are unavailable")
    matches = []
    store = root / "ProfilesV2"
    try:
        groups = tuple(store.iterdir())
    except OSError:
        return SetupProbe("Failed", "ProfilesV2 is unavailable")
    for device in state["Devices"]:
        if not isinstance(device, Mapping):
            continue
        for group in groups:
            if not _safe_directory(group):
                continue
            profile = _read_json(group / "manifest.json")
            pages = profile.get("Pages") if isinstance(profile, Mapping) else None
            profile_device = profile.get("Device") if isinstance(profile, Mapping) else None
            if (not isinstance(pages, Mapping) or not isinstance(profile_device, Mapping)
                    or profile.get("Name") != device.get("CurrentProfile")
                    or profile_device.get("UUID") != device.get("CurrentDevice")
                    or profile_device.get("Model") != "D200"
                    or not isinstance(pages.get("Current"), str)
                    or pages.get("Current") not in pages.get("Pages", ())):
                continue
            page = _read_json(group / "Profiles" / pages["Current"] / "manifest.json")
            controllers = page.get("Controllers") if isinstance(page, Mapping) else None
            if not isinstance(controllers, list):
                continue
            setup_entries = []
            centers = []
            for controller in controllers:
                actions = controller.get("Actions") if isinstance(controller, Mapping) \
                    and controller.get("Type") == "Keypad" else None
                if not isinstance(actions, Mapping):
                    continue
                centers.extend(entry for key, entry in actions.items() if key == "3_2")
                setup_entries.extend((key, entry) for key, entry in actions.items()
                                     if isinstance(entry, Mapping)
                                     and entry.get("Action") == ACTION_UUID
                                     and entry.get("ActionID") == action_id)
            if len(setup_entries) == 1 and len(centers) == 1:
                matches.append((str(profile.get("Name") or ""), setup_entries[0][0], centers[0]))
    if len(matches) != 1:
        return SetupProbe("Failed", "Setup action was not found" if not matches
                          else "Setup action is not unique")
    profile_name, key, center = matches[0]
    center_action = center.get("Action") if isinstance(center, Mapping) else None
    if center_action == LARGEITEM_UUID and _canonical_large_center(center):
        return SetupProbe("Installed", "Large Now Playing is assigned", profile_name, key)
    if center_action == LARGEITEM_UUID:
        return SetupProbe("Repair required", "Large Now Playing settings require repair",
                          profile_name, key)
    if center_action != BUILTIN_UUID:
        return SetupProbe("Failed", "The center display contains another action",
                          profile_name, key)
    return SetupProbe("Ready", "Active ProfilesV2 page verified", profile_name, key)


class SetupActionController:
    def __init__(self, api, data_root_factory: Callable[[], Path] = default_data_root,
                 helper_launcher: Callable[[str, str, str, str], bool] | None = default_helper_launcher,
                 helper_status: Callable[[], Mapping[str, object] | None] = default_helper_status,
                 uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4) -> None:
        self.api = api
        self._data_root_factory = data_root_factory
        self._helper_launcher = helper_launcher
        self._helper_status = helper_status
        self._uuid_factory = uuid_factory
        self._lock = threading.Lock()
        self._contexts: dict[str, _Context] = {}
        self._next_generation = 0
        self._shutdown = False
        self._worker: threading.Thread | None = None

    def add(self, event: object) -> bool:
        identity = _event_identity(event)
        if identity is None:
            return False
        context, key, action_id = identity
        raw = event.get("param") if isinstance(event, Mapping) else None
        operation = raw.get("operation") if isinstance(raw, Mapping) else None
        with self._lock:
            if self._shutdown:
                return False
            self._next_generation += 1
            entry = _Context(action_id, key, self._next_generation,
                             operation=operation if operation in
                             ("install", "repair", "restore") else "install")
            self._contexts[context] = entry
            self._apply_durable_status(entry)
        self._publish(context)
        return True

    def run(self, event: object) -> bool:
        context = event.get("context") if isinstance(event, Mapping) else None
        if not isinstance(context, str):
            return False
        with self._lock:
            entry = self._contexts.get(context)
            if self._shutdown or entry is None or not entry.active or entry.launch_reserved:
                return False
            if self._worker is not None and self._worker.is_alive():
                entry.status, entry.reason = "Failed", "Another Setup check is running"
                worker = None
            else:
                identity = (entry.action_id, entry.key, entry.generation)
                entry.status, entry.reason = "Ready", "Checking the active ProfilesV2 page"
                worker = threading.Thread(target=self._probe, args=(context, *identity),
                                          name="ulanzi-setup-probe", daemon=True)
                self._worker = worker
        self._publish(context)
        if worker is None:
            return False
        worker.start()
        return True

    def clear(self, event: object) -> bool:
        items = event.get("param", ()) if isinstance(event, Mapping) else ()
        with self._lock:
            changed = False
            for item in items if isinstance(items, (list, tuple)) else ():
                context = item.get("context") if isinstance(item, Mapping) else None
                if isinstance(context, str):
                    changed |= self._contexts.pop(context, None) is not None
            return changed

    def set_active(self, event: object) -> bool:
        context = event.get("context") if isinstance(event, Mapping) else None
        with self._lock:
            entry = self._contexts.get(context) if isinstance(context, str) else None
            if self._shutdown or entry is None or entry.launch_reserved:
                return False
            entry.active = event.get("active") is not False
            active = entry.active
        if active:
            self._publish(context)
        return True

    def has_context(self, context: object) -> bool:
        with self._lock:
            return isinstance(context, str) and context in self._contexts

    def receive_settings(self, event: object, persist: bool = False) -> bool:
        context = event.get("context") if isinstance(event, Mapping) else None
        raw = event.get("settings") if isinstance(event, Mapping) else None
        operation = raw.get("operation") if isinstance(raw, Mapping) else None
        if not isinstance(context, str) or operation not in ("install", "repair", "restore"):
            return False
        with self._lock:
            entry = self._contexts.get(context)
            if self._shutdown or entry is None or entry.launch_reserved:
                return False
            entry.operation = operation
            entry.status, entry.reason = "Ready", "Operation changed; press Setup to validate"
        if persist:
            try:
                self.api.setSettings({"operation": operation}, context)
            except Exception:
                return False
        self._publish(context)
        return True

    def inspector_message(self, event: object) -> bool:
        payload = event.get("payload") if isinstance(event, Mapping) else None
        context = event.get("context") if isinstance(event, Mapping) else None
        if not isinstance(payload, Mapping) or payload.get("type") != "requestSetupStatus" \
                or not isinstance(context, str):
            return False
        with self._lock:
            entry = self._contexts.get(context)
            if entry is None:
                return False
            self._apply_durable_status(entry)
        self._publish(context)
        return True

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            self._contexts.clear()
            worker = self._worker
        if worker is not None and worker.is_alive() and threading.current_thread() is not worker:
            worker.join(.5)

    def _probe(self, context: str, action_id: str, key: str, generation: int) -> None:
        try:
            probe = probe_setup_action(self._data_root_factory(), action_id)
        except Exception:
            probe = SetupProbe("Failed", "Setup validation failed")
        with self._lock:
            entry = self._contexts.get(context)
            if self._shutdown or entry is None or not entry.active \
                    or (entry.action_id, entry.key, entry.generation) != (action_id, key, generation):
                return
            entry.status, entry.reason, entry.profile_name = (
                probe.status, probe.reason, probe.profile_name)
            expected = {"install": "Ready", "repair": "Repair required",
                        "restore": "Installed"}[entry.operation]
            should_launch = probe.status == expected and self._helper_launcher is not None
            if should_launch:
                handshake_id = str(self._uuid_factory())
                entry.launch_reserved = True
                entry.handshake_id = handshake_id
                operation = entry.operation
            else:
                operation = entry.operation
                handshake_id = None
        if should_launch:
            try:
                launched = bool(self._helper_launcher(action_id, key, operation, handshake_id))
            except Exception:
                launched = False
            with self._lock:
                entry = self._contexts.get(context)
                if entry is None or entry.handshake_id != handshake_id:
                    return
                if not launched:
                    entry.launch_reserved = False
                    entry.handshake_id = None
                entry.status = "Waiting for Studio to close" if launched else "Failed"
                entry.reason = ("Close Ulanzi Studio to continue, then reopen it manually"
                                if launched else "Packaged Setup helper could not be started")
        elif probe.status != expected:
            with self._lock:
                entry = self._contexts.get(context)
                if entry is not None:
                    entry.status = probe.status if probe.status in {"Installed", "Repair required"} else "Failed"
                    entry.reason = probe.reason
        self._publish(context)

    def _apply_durable_status(self, entry: _Context) -> None:
        try:
            status = self._helper_status()
        except Exception:
            return
        if not isinstance(status, Mapping):
            return
        expected_action_hash = hashlib.sha256(entry.action_id.lower().encode("utf-8")).hexdigest()
        if status.get("setupActionIdSha256") != expected_action_hash:
            return
        if entry.handshake_id and status.get("handshakeId") != entry.handshake_id:
            return
        state = status.get("status")
        if state == "prepared":
            entry.status, entry.reason = "Waiting for Studio to close", "Close Studio to continue"
        elif state == "success":
            entry.status, entry.reason = "Installed", "Large Now Playing was installed; reopen Studio manually"
            entry.launch_reserved = False
        elif state == "restored":
            entry.status, entry.reason = "Restored", "The backed-up center action was restored; reopen Studio manually"
            entry.launch_reserved = False
        elif state == "failed":
            entry.status = "Failed"
            entry.reason = f"Setup failed safely [{status.get('code', 'HELPER_PROCESS_FAILED')}]"
            entry.launch_reserved = False

    def _publish(self, context: str) -> None:
        with self._lock:
            entry = self._contexts.get(context)
            if self._shutdown or entry is None or not entry.active:
                return
            payload = {"status": entry.status, "reason": entry.reason,
                       "profileName": entry.profile_name, "operation": entry.operation}
        try:
            self.api.setPathIcon(context, ICON, payload["status"])
        except Exception:
            pass
        try:
            self.api.sendToPropertyInspector({"setupStatus": payload}, context)
        except Exception:
            pass


def _event_identity(event: object) -> tuple[str, str, str] | None:
    if not isinstance(event, Mapping) or event.get("uuid", event.get("action")) != ACTION_UUID:
        return None
    context = event.get("context")
    if not isinstance(context, str):
        return None
    parts = context.split("___", 2)
    if len(parts) != 3 or not __import__("re").fullmatch(r"\d{1,2}_\d{1,2}", parts[1]) \
            or parts[1] == "3_2":
        return None
    action_id = event.get("actionid", parts[2])
    if action_id != parts[2]:
        return None
    try:
        return context, parts[1], _canonical_uuid(action_id)
    except ValueError:
        return None


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Invalid action UUID")
    return value


def _safe_directory(path: Path) -> bool:
    try:
        info = path.lstat()
        return stat.S_ISDIR(info.st_mode) and not path.is_symlink()
    except OSError:
        return False


def _read_json(path: Path) -> dict | None:
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
                or not 1 <= info.st_size <= MAX_MANIFEST_BYTES):
            return None
        value = json.loads(path.read_text("utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _canonical_large_center(action: Mapping[str, object]) -> bool:
    if action.get("Action") != LARGEITEM_UUID:
        return False
    try:
        _canonical_uuid(action.get("ActionID"))
    except ValueError:
        return False
    allowed_params = ({"SmallViewMode": 2}, {
        "showArtwork": True, "pausedArtwork": "grayscale", "showProgress": True,
        "showElapsed": False, "showRemaining": True, "backgroundColor": "#0B0D10",
        "primaryColor": "#FFFFFF", "secondaryColor": "#B8BEC8",
        "accentColor": "#1DB954", "fit": "contain", "SmallViewMode": 2,
    })
    expected = {
        "Action": LARGEITEM_UUID,
        "ActionID": action.get("ActionID"),
        "ActionParam": action.get("ActionParam"),
        "LinkedTitle": True,
        "Name": "Large Now Playing",
        "Plugin": {"Name": "Media Control for D200",
                    "UUID": "com.arkamax404.ulanzi.mediacontrol", "Version": "2.1.1"},
        "State": 0,
        "ViewParam": [{"Icon": "", "IconRel": "", "Name": "Large Now Playing"}],
    }
    plugin = action.get("Plugin")
    if plugin == {}:
        expected["Plugin"] = {}
    return action.get("ActionParam") in allowed_params and dict(action) == expected
