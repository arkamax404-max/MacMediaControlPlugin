"""Deterministic Ulanzi SDK surface used only by the offline verifier."""

import json


class UlanziApi:
    """In-process SDK transport model for verifying the plugin integration."""

    def __init__(self):
        self.uuid = ""
        self.key = ""
        self.actionid = ""
        self.websocket = None
        self._listeners = {}
        self._last_state = {}

    def connect(self, uuid, port=3906, address="127.0.0.1", language="en", argv=None,
                threaded=True, daemon=True):
        self.uuid = uuid
        return self

    def close(self):
        return None

    def _on(self, event, callback):
        self._listeners.setdefault(event, []).append(callback)
        return self

    def onClose(self, callback): return self._on("close", callback)
    def onAdd(self, callback): return self._on("add", callback)
    def onRun(self, callback): return self._on("run", callback)
    def onKeyDown(self, callback): return self._on("keydown", callback)
    def onKeyUp(self, callback): return self._on("keyup", callback)
    def onClear(self, callback): return self._on("clear", callback)
    def onSetActive(self, callback): return self._on("setactive", callback)
    def onParamFromPlugin(self, callback): return self._on("paramfromplugin", callback)
    def onDidReceiveSettings(self, callback): return self._on("didReceiveSettings", callback)

    def emit(self, event, payload=None):
        return [callback(payload) for callback in self._listeners.get(event, ())]

    @staticmethod
    def _context(context):
        uuid, key, actionid = context.split("___")
        return uuid, key, actionid

    def _send(self, command, payload):
        if command == "state":
            state = payload["param"]["statelist"][0]
            fingerprint = json.dumps(state, sort_keys=True)
            if self._last_state.get(state["uuid"]) == fingerprint:
                return
            self._last_state[state["uuid"]] = fingerprint
        if self.websocket is not None and getattr(self.websocket, "connected", False):
            self.websocket.send(json.dumps({"cmd": command, **payload}))

    def setSettings(self, settings, context):
        uuid, key, actionid = self._context(context)
        self._send("setSettings", {"uuid": uuid, "key": key, "actionid": actionid,
                                   "settings": settings})

    def setBaseDataIcon(self, context, data, text):
        uuid, key, actionid = self._context(context)
        self._send("state", {"param": {"statelist": [{
            "uuid": uuid, "key": key, "actionid": actionid, "type": 1, "data": data,
            "textData": text or "", "showtext": bool(text),
        }]}})

    def setPathIcon(self, context, path, text):
        uuid, key, actionid = self._context(context)
        self._send("state", {"param": {"statelist": [{
            "uuid": uuid, "key": key, "actionid": actionid, "type": 2, "path": path,
            "textData": text or "", "showtext": bool(text),
        }]}})
