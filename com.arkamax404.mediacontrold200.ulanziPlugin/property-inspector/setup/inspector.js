export function normalizeSetupStatus(raw = {}) {
  const accepted = new Set(["Ready", "Waiting for Studio to close", "Installed", "Restored", "Repair required", "Failed"]);
  return {status:accepted.has(raw?.status) ? raw.status : "Ready",
    reason:String(raw?.reason || "Press the assigned D200 key to verify this page").trim().slice(0, 160),
    profileName:String(raw?.profileName || "").trim().slice(0, 96)};
}
export function startInspector(sdk, documentRef) {
  const status = documentRef.querySelector("#status"), reason = documentRef.querySelector("#reason");
  const profile = documentRef.querySelector("#profile"), operation = documentRef.querySelector("#operation");
  if (!status || !reason || !profile || !operation) return;
  const apply = raw => { const value = normalizeSetupStatus(raw); status.textContent = value.status;
    reason.textContent = value.reason; profile.textContent = value.profileName ? `Profile: ${value.profileName}` : "";
    status.style.color = value.status === "Failed" ? "#ff6b6b" : "#1db954"; };
  const request = () => sdk.sendToPlugin({type:"requestSetupStatus"});
  operation.addEventListener("change", () => sdk.sendParamFromPlugin({operation:operation.value}));
  sdk.onConnected(request); sdk.onAdd(event => { if (["install", "repair", "restore"].includes(event?.param?.operation)) operation.value = event.param.operation; request(); });
  sdk.onSendToPropertyInspector(event => apply(event?.payload?.setupStatus));
  documentRef.defaultView?.setInterval?.(request, 1000);
  apply({}); sdk.connect("com.arkamax404.ulanzi.mediacontrol.setup-large-display");
}
if (typeof document !== "undefined" && typeof $UD !== "undefined") startInspector($UD, document);
