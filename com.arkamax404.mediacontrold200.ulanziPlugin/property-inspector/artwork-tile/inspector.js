import { DEFAULT_ICON_COLOR, normalizeIconColor } from "../shared/icon-color.js";

export const DEFAULT_BADGE_COLOR = DEFAULT_ICON_COLOR;

export const SECONDARY_ACTIONS = Object.freeze([
  "none", "previous", "toggle", "next", "volume-up", "volume-down", "mute-toggle",
]);
const TILE_ACTIONS = new Set([
  "artwork-top-left", "artwork-top-right", "artwork-bottom-left", "artwork-bottom-right",
]);

export function normalizeTileSettings(raw = {}) {
  return {
    secondaryAction: SECONDARY_ACTIONS.includes(raw?.secondaryAction)
      ? raw.secondaryAction : "none",
    badgeColor: normalizeIconColor(raw?.badgeColor),
  };
}

export function startInspector(sdk, documentRef) {
  const actionSelect = documentRef.querySelector("#secondary-action");
  const badgeColor = documentRef.querySelector("#badge-color");
  const badgeColorHex = documentRef.querySelector("#badge-color-hex");
  if (!actionSelect || !badgeColor || !badgeColorHex) return;
  const candidate = documentRef.documentElement.dataset.action;
  const action = TILE_ACTIONS.has(candidate) ? candidate : "artwork-top-left";
  const apply = (raw) => {
    const settings = normalizeTileSettings(raw);
    actionSelect.value = settings.secondaryAction;
    badgeColor.value = settings.badgeColor;
    badgeColorHex.value = settings.badgeColor;
  };
  const send = (event) => {
    const settings = normalizeTileSettings({
      secondaryAction: actionSelect.value,
      badgeColor: event.target === badgeColorHex ? badgeColorHex.value : badgeColor.value,
    });
    apply(settings);
    sdk.sendParamFromPlugin(settings);
  };
  sdk.onAdd((event) => apply(event?.param));
  sdk.onParamFromApp((event) => apply(event?.param));
  sdk.onParamFromPlugin((event) => apply(event?.param));
  sdk.onDidReceiveSettings?.((event) => apply(event?.settings));
  actionSelect.addEventListener("change", send);
  badgeColor.addEventListener("change", send);
  badgeColorHex.addEventListener("change", send);
  apply({});
  sdk.connect(`com.arkamax404.ulanzi.mediacontrol.${action}`);
}

if (typeof document !== "undefined" && typeof $UD !== "undefined") {
  startInspector($UD, document);
}
