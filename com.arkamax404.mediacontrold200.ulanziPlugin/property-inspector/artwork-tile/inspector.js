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
  };
}

export function startInspector(sdk, documentRef) {
  const actionSelect = documentRef.querySelector("#secondary-action");
  if (!actionSelect) return;
  const candidate = documentRef.documentElement.dataset.action;
  const action = TILE_ACTIONS.has(candidate) ? candidate : "artwork-top-left";
  const apply = (raw) => {
    actionSelect.value = normalizeTileSettings(raw).secondaryAction;
  };
  const send = () => {
    const settings = normalizeTileSettings({ secondaryAction: actionSelect.value });
    apply(settings);
    sdk.sendParamFromPlugin(settings);
  };
  sdk.onAdd((event) => apply(event?.param));
  sdk.onParamFromApp((event) => apply(event?.param));
  sdk.onParamFromPlugin((event) => apply(event?.param));
  sdk.onDidReceiveSettings?.((event) => apply(event?.settings));
  actionSelect.addEventListener("change", send);
  apply({});
  sdk.connect(`com.arkamax404.ulanzi.mediacontrol.${action}`);
}

if (typeof document !== "undefined" && typeof $UD !== "undefined") {
  startInspector($UD, document);
}
