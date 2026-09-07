export const DEFAULT_ICON_COLOR = "#1DB954";

const COLOR_PATTERN = /^#[0-9A-Fa-f]{6}$/;
const COLOR_ACTIONS = new Set([
  "previous", "toggle", "next", "volume-up", "volume-down", "mute-toggle",
]);

export function normalizeIconColor(value) {
  return COLOR_PATTERN.test(String(value || ""))
    ? String(value).toUpperCase() : DEFAULT_ICON_COLOR;
}

export function startInspector(sdk, documentRef) {
  const form = documentRef.querySelector("#icon-color-settings");
  const color = documentRef.querySelector("#icon-color");
  const colorHex = documentRef.querySelector("#icon-color-hex");
  if (!form || !color || !colorHex) return;
  const candidate = documentRef.documentElement.dataset.action;
  const action = COLOR_ACTIONS.has(candidate) ? candidate : "toggle";

  const apply = (raw) => {
    const iconColor = normalizeIconColor(raw?.iconColor);
    color.value = iconColor;
    colorHex.value = iconColor;
  };
  const send = (source) => {
    const iconColor = normalizeIconColor(source === color ? color.value : colorHex.value);
    apply({ iconColor });
    sdk.sendParamFromPlugin({ iconColor });
  };

  sdk.onAdd((event) => apply(event?.param));
  sdk.onParamFromApp((event) => apply(event?.param));
  sdk.onParamFromPlugin((event) => apply(event?.param));
  sdk.onDidReceiveSettings?.((event) => apply(event?.settings));
  form.addEventListener("change", (event) => {
    if (event.target === color || event.target === colorHex) send(event.target);
  });
  apply({});
  sdk.connect(`com.arkamax404.ulanzi.mediacontrol.${action}`);
}

if (typeof document !== "undefined" && typeof $UD !== "undefined") {
  startInspector($UD, document);
}
