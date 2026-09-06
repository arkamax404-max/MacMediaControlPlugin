import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_LARGEITEM_SETTINGS,
  SpotifyGSMTCPlugin,
  normalizeLargeItemSettings,
  renderLargeItemSvg,
} from "../src/plugin.js";

const LARGE = "com.arkamax404.ulanzi.mediacontrol.largeitem-nowplaying";
const SETUP = "com.arkamax404.ulanzi.mediacontrol.setup-large-display";

function sdk() {
  const calls = [], handlers = {};
  return { calls, handlers, onConnected() {}, onAdd(fn) { handlers.add = fn; },
    onRun(fn) { handlers.run = fn; }, onClear() {}, onSetActive() {}, onParamFromApp() {},
    onParamFromPlugin() {}, onDidReceiveSettings() {}, onSendToPlugin(fn) { handlers.message = fn; },
    onClose() {}, setSettings(...args) { calls.push(["settings", ...args]); },
    setBaseDataIcon(...args) { calls.push(["data", ...args]); },
    setPathIcon(...args) { calls.push(["path", ...args]); },
    sendToPropertyInspector(...args) { calls.push(["inspector", ...args]); } };
}

test("large source renderer is deterministic and uses the exact center canvas", () => {
  const state = { online:true, available:true, isPlaying:true, title:"A & B", artist:"Artist",
    timelineAvailable:true, positionSeconds:30, durationSeconds:120, playbackRate:1,
    positionUpdatedAt:Date.parse("2026-09-05T12:00:00Z") };
  const svg = renderLargeItemSvg(state, null, {}, Date.parse("2026-09-05T12:00:00Z"));
  assert.equal(svg, renderLargeItemSvg(state, null, {}, Date.parse("2026-09-05T12:00:00Z")));
  assert.match(svg, /width="458" height="196" viewBox="0 0 458 196"/);
  assert.match(svg, /A &amp; B/);
  assert.equal(normalizeLargeItemSettings({ SmallViewMode: 0 }).SmallViewMode, 2);
  assert.equal(DEFAULT_LARGEITEM_SETTINGS.SmallViewMode, 2);
});

test("source mode accepts large only at 3_2 and fails Setup without profile mutation", () => {
  const client = sdk();
  const plugin = new SpotifyGSMTCPlugin({ sdk:client, tokenLoader:() => null,
    setIntervalImpl:() => 1, clearIntervalImpl:() => {} });
  plugin.connect();
  plugin.add({ uuid:LARGE, context:`${LARGE}___1_1___id`, param:{} });
  assert.equal(plugin.contexts.size, 0);
  plugin.add({ uuid:LARGE, context:`${LARGE}___3_2___id`, param:{} });
  assert.equal(plugin.entry(`${LARGE}___3_2___id`).settings.SmallViewMode, 2);
  const setupContext = `${SETUP}___0_0___11111111-1111-4111-8111-111111111111`;
  plugin.add({ uuid:SETUP, context:setupContext });
  assert.equal(client.calls.at(-1)[3], "Packaged setup required");
  client.handlers.message({ context:setupContext, payload:{ type:"requestSetupStatus" } });
  assert.equal(client.calls.at(-1)[1].setupStatus.status, "Failed");
  plugin.stop();
});
