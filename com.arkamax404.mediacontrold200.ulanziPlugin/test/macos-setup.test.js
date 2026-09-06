import assert from "node:assert/strict";
import test from "node:test";
import { cp, mkdir, mkdtemp, readFile, readdir, rename, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apply, prepare, resolveTarget } from "../helper/Invoke-MediaControlSetup.mjs";

const setup = "com.arkamax404.ulanzi.mediacontrol.setup-large-display";
const large = "com.arkamax404.ulanzi.mediacontrol.largeitem-nowplaying";
const builtIn = "com.ulanzi.ulanzideck.smallwindow.window";
const actionId = "11111111-1111-4111-8111-111111111111";

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "mediacontrol-setup-"));
  const data = join(root, "data"), state = join(root, "state"), group = "group-a", page = "page-a";
  const manifest = join(data, "ProfilesV2", group, "Profiles", page, "manifest.json");
  await mkdir(join(data, "Config"), { recursive: true });
  await mkdir(join(data, "ProfilesV2", group, "Profiles", page), { recursive: true });
  await writeFile(join(data, "Config", "setting_source.json"), JSON.stringify({ Devices: [{ CurrentProfile: "Test", CurrentDevice: "device-a" }] }));
  await writeFile(join(data, "ProfilesV2", group, "manifest.json"), JSON.stringify({ Name: "Test", Device: { UUID: "device-a", Model: "D200" }, Pages: { Current: page, Pages: [page] } }));
  await writeFile(manifest, JSON.stringify({ Controllers: [{ Type: "Keypad", Actions: {
    "0_0": { Action: setup, ActionID: actionId, ActionParam: { operation: "install" } },
    "1_0": { Action: "example.safe", Name: "Preserve me" }, "3_2": { Action: builtIn },
  } }] }));
  return { root, data, state, manifest, pageRoot: join(data, "ProfilesV2", group, "Profiles", page) };
}
const page = async f => JSON.parse(await readFile(f.manifest, "utf8"));
async function redirectPage(f) {
  const external = join(f.root, "external-page");
  await rename(f.pageRoot, external);
  await symlink(external, f.pageRoot, "dir");
  return join(external, "manifest.json");
}

test("setup installs canonical center, records a backup, and restores exact bytes", async () => {
  const f = await fixture();
  try {
    const before = await readFile(f.manifest);
    await prepare({ dataRoot: f.data, stateRoot: f.state, key: "0_0", setupActionId: actionId });
    const installed = await apply({ dataRoot: f.data, stateRoot: f.state, isStudioClosed: async () => true });
    const center = (await page(f)).Controllers[0].Actions["3_2"];
    assert.equal(center.Action, large); assert.equal(center.ActionParam.SmallViewMode, 2);
    assert.match(installed.centerActionFingerprintSha256, /^[0-9a-f]{64}$/);
    assert.equal((await resolveTarget({ dataRoot: f.data, key: "0_0", setupActionId: actionId })).kind, "installed");
    const current = await page(f); current.Controllers[0].Actions["3_2"].Plugin = {};
    current.Controllers[0].Actions["0_0"].ActionParam.operation = "restore";
    await writeFile(f.manifest, JSON.stringify(current));
    await prepare({ dataRoot: f.data, stateRoot: f.state, key: "0_0", setupActionId: actionId, operation: "restore" });
    await apply({ dataRoot: f.data, stateRoot: f.state, isStudioClosed: async () => true });
    assert.deepEqual(await readFile(f.manifest), before);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("restore rejects unrelated edits and apply rolls back after post-write failure", async () => {
  const f = await fixture();
  try {
    await prepare({ dataRoot: f.data, stateRoot: f.state, key: "0_0", setupActionId: actionId });
    await apply({ dataRoot: f.data, stateRoot: f.state, isStudioClosed: async () => true });
    const changed = await page(f); changed.Controllers[0].Actions["1_0"].Name = "Unexpected";
    await writeFile(f.manifest, JSON.stringify(changed));
    await assert.rejects(prepare({ dataRoot: f.data, stateRoot: f.state, key: "0_0", setupActionId: actionId, operation: "restore" }), error => error.code === "RESTORE_BACKUP_INVALID");
  } finally { await rm(f.root, { recursive: true, force: true }); }
  const rollback = await fixture();
  try {
    const before = await readFile(rollback.manifest);
    await prepare({ dataRoot: rollback.data, stateRoot: rollback.state, key: "0_0", setupActionId: actionId });
    await assert.rejects(apply({ dataRoot: rollback.data, stateRoot: rollback.state,
      isStudioClosed: async () => true, afterRename: async () => { throw new Error("injected"); } }), /injected/);
    assert.deepEqual(await readFile(rollback.manifest), before);
  } finally { await rm(rollback.root, { recursive: true, force: true }); }
});

test("apply refuses to mutate while Studio is open", async () => {
  const f = await fixture();
  try {
    await prepare({ dataRoot: f.data, stateRoot: f.state, key: "0_0", setupActionId: actionId });
    await assert.rejects(apply({ dataRoot: f.data, stateRoot: f.state, isStudioClosed: async () => false }), error => error.code === "HELPER_PROCESS_FAILED");
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("prepare rejects symlinked page ancestry without touching the external manifest", async () => {
  const f = await fixture();
  try {
    const externalManifest = await redirectPage(f);
    const before = await readFile(externalManifest);
    await assert.rejects(
      prepare({ dataRoot: f.data, stateRoot: f.state, key: "0_0", setupActionId: actionId }),
      error => error.code === "HELPER_PROCESS_FAILED");
    assert.deepEqual(await readFile(externalManifest), before);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("apply rechecks symlinked page ancestry without touching the external manifest", async () => {
  const f = await fixture();
  try {
    await prepare({ dataRoot: f.data, stateRoot: f.state, key: "0_0", setupActionId: actionId });
    const externalManifest = await redirectPage(f);
    const before = await readFile(externalManifest);
    await assert.rejects(
      apply({ dataRoot: f.data, stateRoot: f.state, isStudioClosed: async () => true }),
      error => error.code === "HELPER_PROCESS_FAILED");
    assert.deepEqual(await readFile(externalManifest), before);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("Studio reopening after backup preparation prevents live replacement", async () => {
  const f = await fixture();
  try {
    const before = await readFile(f.manifest);
    await prepare({ dataRoot: f.data, stateRoot: f.state, key: "0_0", setupActionId: actionId });
    let checks = 0;
    await assert.rejects(
      apply({ dataRoot: f.data, stateRoot: f.state,
        isStudioClosed: async () => ++checks === 1 }),
      error => error.code === "HELPER_PROCESS_FAILED");
    assert.equal(checks, 2);
    assert.deepEqual(await readFile(f.manifest), before);
    assert.deepEqual(await readdir(f.pageRoot), ["manifest.json"]);
    const runs = await readdir(join(f.state, "backups"));
    assert.equal(runs.length, 1);
    assert.deepEqual(await readFile(join(f.state, "backups", runs[0], "manifest.before.json")), before);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});

test("canonical installed assignment is idempotent and malformed assignment requires explicit repair", async () => {
  const f = await fixture();
  try {
    await prepare({ dataRoot: f.data, stateRoot: f.state, key: "0_0", setupActionId: actionId });
    await apply({ dataRoot: f.data, stateRoot: f.state, isStudioClosed: async () => true });
    const installed = await readFile(f.manifest);
    await assert.rejects(prepare({ dataRoot: f.data, stateRoot: f.state, key: "0_0",
      setupActionId: actionId, operation: "install" }), error => error.code === "SLOT_UNRELATED");
    assert.deepEqual(await readFile(f.manifest), installed);
    const malformed = await page(f); malformed.Controllers[0].Actions["3_2"].ActionParam.SmallViewMode = 1;
    await writeFile(f.manifest, JSON.stringify(malformed));
    assert.equal((await resolveTarget({ dataRoot: f.data, key: "0_0", setupActionId: actionId })).kind, "repair");
    await prepare({ dataRoot: f.data, stateRoot: f.state, key: "0_0", setupActionId: actionId,
      operation: "repair" });
    await apply({ dataRoot: f.data, stateRoot: f.state, isStudioClosed: async () => true });
    assert.equal((await page(f)).Controllers[0].Actions["3_2"].ActionParam.SmallViewMode, 2);
  } finally { await rm(f.root, { recursive: true, force: true }); }
});
