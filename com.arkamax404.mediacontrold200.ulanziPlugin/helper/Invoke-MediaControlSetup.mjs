#!/usr/bin/env node
import { createHash, randomUUID } from "node:crypto";
import { cp, lstat, mkdir, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import { basename, dirname, join, relative, resolve } from "node:path";
import { homedir } from "node:os";
import { execFile as execFileCallback } from "node:child_process";
import { promisify } from "node:util";
import { pathToFileURL } from "node:url";

const execFile = promisify(execFileCallback);
const ACTION = "com.arkamax404.ulanzi.mediacontrol.largeitem-nowplaying";
const SETUP = "com.arkamax404.ulanzi.mediacontrol.setup-large-display";
const PLUGIN = "com.arkamax404.ulanzi.mediacontrol";
const BUILTIN = "com.ulanzi.ulanzideck.smallwindow.window";
const VERSION = "2.2.0";
const REQUEST_SCHEMA = "com.arkamax404.ulanzi.mediacontrol.setup-request/v1";
const POINTER_SCHEMA = "com.arkamax404.ulanzi.mediacontrol.request-pointer/v1";
const RECEIPT_SCHEMA = "com.arkamax404.ulanzi.mediacontrol.setup-receipt/v1";
const CODES = new Set(["PROFILE_NOT_FOUND", "PROFILE_AMBIGUOUS", "SETUP_INSTANCE_NOT_FOUND",
  "PAGE_INVALID", "SLOT_UNRELATED", "SETTINGS_SCHEMA_UNSUPPORTED", "MANIFEST_INVALID",
  "COMPATIBILITY_UNSUPPORTED", "HELPER_PROCESS_FAILED", "REPREPARE_REQUIRED",
  "RESTORE_BACKUP_NOT_FOUND", "RESTORE_BACKUP_INVALID", "RESTORED"]);
const PHASES = new Set(["INITIALIZING", "COMPATIBILITY", "COMPAT_PLUGIN_ROOT",
  "COMPAT_MANIFEST_READ", "SETTINGS_READ", "SETTINGS_SCHEMA", "V2_ENUMERATION",
  "DEVICE_PROFILE_MATCH", "PAGE_READ", "TARGET_RESOLUTION", "RESTORE_RESOLUTION",
  "SLOT_VALIDATION", "REQUEST_WRITE", "APPLY_PRECHECK", "BACKUP", "PATCH_WRITE",
  "RESTORE_WRITE", "READBACK", "RECEIPT", "MANUAL_REOPEN"]);
const DEFAULT_SETTINGS = Object.freeze({showArtwork:true,pausedArtwork:"grayscale",
  showProgress:true,showElapsed:false,showRemaining:true,backgroundColor:"#0B0D10",
  primaryColor:"#FFFFFF",secondaryColor:"#B8BEC8",accentColor:"#1DB954",fit:"contain",
  SmallViewMode:2});

const hash = value => createHash("sha256").update(value).digest("hex");
const fileHash = async path => hash(await readFile(path));
const safeSegment = value => typeof value === "string"
  && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value) && value !== "." && value !== "..";
const uuid = value => typeof value === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);

function fail(code, phase) {
  const error = new Error(code);
  error.code = CODES.has(code) ? code : "HELPER_PROCESS_FAILED";
  error.phase = PHASES.has(phase) ? phase : "INITIALIZING";
  throw error;
}
function under(root, ...parts) {
  const path = resolve(root, ...parts);
  const rel = relative(resolve(root), path);
  if (rel === ".." || rel.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`)) {
    fail("HELPER_PROCESS_FAILED", "INITIALIZING");
  }
  return path;
}
async function requireDirectory(path, phase) {
  let metadata;
  try { metadata = await lstat(path); } catch { fail("HELPER_PROCESS_FAILED", phase); }
  if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
    fail("HELPER_PROCESS_FAILED", phase);
  }
}
async function requireRegularFile(path, phase) {
  let metadata;
  try { metadata = await lstat(path); } catch { fail("HELPER_PROCESS_FAILED", phase); }
  if (metadata.isSymbolicLink() || !metadata.isFile()) {
    fail("HELPER_PROCESS_FAILED", phase);
  }
}
async function validateProfileRoot(store, groupId, phase) {
  const root = under(store, groupId);
  const rootManifest = under(root, "manifest.json");
  await requireDirectory(store, phase);
  await requireDirectory(root, phase);
  await requireRegularFile(rootManifest, phase);
  return {root, rootManifest};
}
async function validateMutationTarget(dataRoot, groupId, pageId, phase) {
  const store = under(dataRoot, "ProfilesV2");
  const {root, rootManifest} = await validateProfileRoot(store, groupId, phase);
  const profiles = under(root, "Profiles");
  const pageRoot = under(profiles, pageId);
  const manifest = under(pageRoot, "manifest.json");
  await requireDirectory(profiles, phase);
  await requireDirectory(pageRoot, phase);
  await requireRegularFile(manifest, phase);
  return {manifest, rootManifest};
}
async function json(path, code, phase) {
  try { return JSON.parse(await readFile(path, "utf8")); } catch { fail(code, phase); }
}
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  }
  return value;
}
const canonicalText = value => JSON.stringify(canonical(value));
function large(document, code = "PAGE_INVALID", phase = "SLOT_VALIDATION") {
  const found = (document.Controllers || []).filter(
    controller => controller?.Type === "Keypad" && controller.Actions?.["3_2"]);
  if (found.length !== 1) fail(code, phase);
  return found[0];
}
function setupController(document, key, actionHash) {
  const found = (document.Controllers || []).filter(
    controller => controller?.Type === "Keypad" && controller.Actions?.[key]);
  if (found.length !== 1) fail("SETUP_INSTANCE_NOT_FOUND", "TARGET_RESOLUTION");
  const entry = found[0].Actions[key];
  if (entry?.Action !== SETUP || !uuid(entry.ActionID)
      || hash(entry.ActionID.toLowerCase()) !== actionHash) {
    fail("SETUP_INSTANCE_NOT_FOUND", "TARGET_RESOLUTION");
  }
}
function generatedCenter(actionId) {
  return {Action:ACTION, ActionID:actionId, ActionParam:{SmallViewMode:2}, LinkedTitle:true,
    Name:"Large Now Playing", Plugin:{Name:"Media Control for D200", UUID:PLUGIN, Version:VERSION},
    State:0, ViewParam:[{Icon:"", IconRel:"", Name:"Large Now Playing"}]};
}
function normalizedCenter(action) {
  if (!uuid(action?.ActionID)) return null;
  const expected = generatedCenter(action.ActionID);
  if (![canonicalText(expected.Plugin), "{}"].includes(canonicalText(action.Plugin))) return null;
  const params = canonicalText(action.ActionParam);
  if (![canonicalText(expected.ActionParam), canonicalText(DEFAULT_SETTINGS)].includes(params)) return null;
  const normalized = structuredClone(action);
  normalized.Plugin = {};
  normalized.ActionParam = {SmallViewMode:2};
  const normalizedExpected = structuredClone(expected);
  normalizedExpected.Plugin = {};
  return canonicalText(normalized) === canonicalText(normalizedExpected) ? normalized : null;
}
const centerFingerprint = action => {
  const normalized = normalizedCenter(action);
  return normalized ? hash(canonicalText(normalized)) : null;
};
function normalizedPatchedPage(document, setupKey) {
  const copy = structuredClone(document);
  const center = large(copy, "RESTORE_BACKUP_INVALID", "RESTORE_RESOLUTION").Actions["3_2"];
  if (!normalizedCenter(center)) return null;
  center.Plugin = {};
  center.ActionParam = {SmallViewMode:2};
  const setupEntries = (copy.Controllers || []).filter(
    controller => controller?.Type === "Keypad" && controller.Actions?.[setupKey]);
  if (setupEntries.length !== 1 || setupEntries[0].Actions[setupKey]?.Action !== SETUP) return null;
  const setupAction = setupEntries[0].Actions[setupKey];
  const params = setupAction.ActionParam;
  if (params && Object.hasOwn(params, "operation")) {
    if (!["install", "repair", "restore"].includes(params.operation)) return null;
    delete params.operation;
  }
  if (params && typeof params === "object" && !Array.isArray(params)
      && Object.keys(params).length === 0) delete setupAction.ActionParam;
  return canonicalText(copy);
}
const receiptBound = (receipt, target) => receipt?.target?.store === target.store
  && receipt.target.groupId === target.groupId && receipt.target.pageId === target.pageId
  && receipt.target.key === "3_2";
async function studioClosed() {
  try { await execFile("pgrep", ["-x", "UlanziDeck"]); return false; }
  catch (error) { return error.code === 1; }
}
export async function validateStudioIdentity(pluginRoot, {bundleIdentifier} = {}) {
  const compatibility = await json(under(pluginRoot, "helper", "compatibility.json"),
    "COMPATIBILITY_UNSUPPORTED", "COMPAT_MANIFEST_READ");
  const allowed = compatibility?.studio?.macos?.bundleIdentifiers;
  if (compatibility?.schema !== "com.arkamax404.ulanzi.mediacontrol.compatibility/v1"
      || !Array.isArray(allowed) || !allowed.includes(bundleIdentifier)) {
    fail("COMPATIBILITY_UNSUPPORTED", "COMPATIBILITY");
  }
}
async function atomicWrite(path, text) {
  const temp = join(dirname(path), `.mediacontrol-${randomUUID()}.tmp`);
  try { await writeFile(temp, text, "utf8"); await rename(temp, path); }
  catch (error) { await rm(temp, {force:true}); throw error; }
}
async function writeStatus(stateRoot, status, code, phase, handshakeId, setupActionId=null) {
  await mkdir(stateRoot, {recursive:true});
  await atomicWrite(under(stateRoot, "last-diagnostic.json"),
    JSON.stringify({status, code, phase:phase === "RELAUNCH" ? "MANUAL_REOPEN" : phase,
      handshakeId, setupActionIdSha256:uuid(setupActionId)
        ? hash(setupActionId.toLowerCase()) : null}));
}
const targetKey = target => [target.store, target.groupId, target.pageId, target.manifest].join("\0");

export async function resolveTarget({dataRoot, key, setupActionId}) {
  if (!/^\d{1,2}_\d{1,2}$/.test(key) || key === "3_2" || !uuid(setupActionId)) {
    fail("SETUP_INSTANCE_NOT_FOUND", "TARGET_RESOLUTION");
  }
  const state = await json(under(dataRoot, "Config", "setting_source.json"),
    "SETTINGS_SCHEMA_UNSUPPORTED", "SETTINGS_READ");
  if (!Array.isArray(state.Devices) || state.Devices.length === 0) {
    fail("SETTINGS_SCHEMA_UNSUPPORTED", "SETTINGS_SCHEMA");
  }
  const store = under(dataRoot, "ProfilesV2");
  await requireDirectory(store, "V2_ENUMERATION");
  let groups;
  try { groups = await readdir(store, {withFileTypes:true}); }
  catch { fail("PROFILE_NOT_FOUND", "V2_ENUMERATION"); }
  const matches = [];
  const actionHash = hash(setupActionId.toLowerCase());
  for (const device of state.Devices) {
    if (typeof device?.CurrentProfile !== "string" || typeof device?.CurrentDevice !== "string") continue;
    for (const group of groups.filter(item => item.isDirectory() && safeSegment(item.name))) {
      const {rootManifest} = await validateProfileRoot(store, group.name, "DEVICE_PROFILE_MATCH");
      let profile;
      try { profile = await json(rootManifest, "MANIFEST_INVALID", "DEVICE_PROFILE_MATCH"); }
      catch { continue; }
      if (profile?.Name !== device.CurrentProfile || profile?.Device?.UUID !== device.CurrentDevice
          || profile?.Device?.Model !== "D200" || !safeSegment(profile?.Pages?.Current)
          || !Array.isArray(profile.Pages.Pages)
          || !profile.Pages.Pages.includes(profile.Pages.Current)) continue;
      const pageId = profile.Pages.Current;
      const {manifest} = await validateMutationTarget(
        dataRoot, group.name, pageId, "PAGE_READ");
      const page = await json(manifest, "PAGE_INVALID", "PAGE_READ");
      setupController(page, key, actionHash);
      const slot = large(page).Actions["3_2"];
      if (!slot?.Action) fail("PAGE_INVALID", "SLOT_VALIDATION");
      let kind;
      if (slot.Action === BUILTIN) kind = "patch";
      else if (slot.Action === ACTION && normalizedCenter(slot)) kind = "installed";
      else if (slot.Action === ACTION && uuid(slot.ActionID)) kind = "repair";
      else fail("SLOT_UNRELATED", "SLOT_VALIDATION");
      matches.push({store:"ProfilesV2", groupId:group.name, pageId, manifest, rootManifest,
        profileName:profile.Name, deviceUuid:profile.Device.UUID, setupKey:key,
        setupActionIdSha256:actionHash, kind});
    }
  }
  const unique = [...new Map(matches.map(target => [targetKey(target), target])).values()];
  if (unique.length === 0) fail("SETUP_INSTANCE_NOT_FOUND", "TARGET_RESOLUTION");
  if (unique.length !== 1) fail("PROFILE_AMBIGUOUS", "TARGET_RESOLUTION");
  return unique[0];
}

export async function prepare({dataRoot, stateRoot, key, setupActionId, operation="install"}) {
  const target = await resolveTarget({dataRoot, key, setupActionId});
  const expectedKind = operation === "restore" ? "installed" : operation === "repair" ? "repair" : "patch";
  if (target.kind !== expectedKind) fail("SLOT_UNRELATED", "SLOT_VALIDATION");
  const now = new Date();
  const request = {schema:REQUEST_SCHEMA, pluginVersion:VERSION, createdUtc:now.toISOString(),
    expiresUtc:new Date(now.getTime() + 30 * 60e3).toISOString(), setupKey:key,
    setupActionIdSha256:hash(setupActionId.toLowerCase()), dataRootHash:hash(resolve(dataRoot)),
    store:target.store, groupId:target.groupId, pageId:target.pageId,
    manifestPath:target.manifest, manifestSha256:await fileHash(target.manifest),
    rootManifestSha256:await fileHash(target.rootManifest),
    profileNameSha256:hash(target.profileName.toLowerCase()),
    deviceUuidSha256:hash(target.deviceUuid.toLowerCase()), operation,
    action:ACTION};
  if (operation === "restore") request.restore = await restoreCandidate(stateRoot, target);
  const requests = under(stateRoot, "requests");
  await mkdir(requests, {recursive:true});
  const file = `${randomUUID().replaceAll("-", "")}.json`;
  const text = JSON.stringify(request);
  await atomicWrite(under(requests, file), text);
  await atomicWrite(under(requests, `${file}.sha256`), `${hash(text)}\n`);
  await atomicWrite(under(requests, "current.json"),
    JSON.stringify({schema:POINTER_SCHEMA, file, sha256:hash(text)}));
  return request;
}
async function request(stateRoot) {
  const root = under(stateRoot, "requests");
  const pointer = await json(under(root, "current.json"), "REPREPARE_REQUIRED", "APPLY_PRECHECK");
  if (pointer?.schema !== POINTER_SCHEMA || !/^[0-9a-f]{32}\.json$/i.test(pointer.file)
      || basename(pointer.file) !== pointer.file) fail("REPREPARE_REQUIRED", "APPLY_PRECHECK");
  const text = await readFile(under(root, pointer.file), "utf8");
  if (hash(text) !== pointer.sha256
      || hash(text) !== (await readFile(under(root, `${pointer.file}.sha256`), "utf8")).trim()) {
    fail("REPREPARE_REQUIRED", "APPLY_PRECHECK");
  }
  const value = JSON.parse(text);
  if (value.schema !== REQUEST_SCHEMA || value.pluginVersion !== VERSION
      || !["install", "repair", "restore"].includes(value.operation)
      || value.action !== ACTION || Date.parse(value.expiresUtc) <= Date.now()) {
    fail("REPREPARE_REQUIRED", "APPLY_PRECHECK");
  }
  return value;
}
async function inspectRestoreRun(stateRoot, runId, target, currentPage) {
  const root = under(stateRoot, "backups", runId);
  const backup = under(root, "manifest.before.json");
  const receiptPath = under(root, "receipt.json");
  let receipt;
  try { receipt = await json(receiptPath, "RESTORE_BACKUP_INVALID", "RESTORE_RESOLUTION"); }
  catch { return {status:"malformed"}; }
  if (!receiptBound(receipt, target)) return {status:"unbound"};
  try {
    const backupText = await readFile(backup, "utf8");
    const before = hash(backupText);
    const backupPage = JSON.parse(backupText);
    const backupCenter = large(backupPage, "RESTORE_BACKUP_INVALID", "RESTORE_RESOLUTION").Actions["3_2"];
    const currentCenter = large(currentPage, "RESTORE_BACKUP_INVALID", "RESTORE_RESOLUTION").Actions["3_2"];
    const fingerprint = centerFingerprint(currentCenter);
    if (receipt.schema !== RECEIPT_SCHEMA || receipt.operation !== "install"
        || receipt.result !== "success" || receipt.action !== ACTION
        || receipt.beforeSha256 !== before || receipt.backupSha256 !== before
        || receipt.afterSha256 === before || !fingerprint || backupCenter?.Action !== BUILTIN) {
      return {status:"invalid"};
    }
    const expected = structuredClone(backupPage);
    large(expected, "RESTORE_BACKUP_INVALID", "RESTORE_RESOLUTION").Actions["3_2"] =
      generatedCenter(currentCenter.ActionID);
    if (hash(JSON.stringify(expected, null, 2)) !== receipt.afterSha256
        || receipt.centerActionFingerprintSha256 !== fingerprint
        || normalizedPatchedPage(expected, target.setupKey)
          !== normalizedPatchedPage(currentPage, target.setupKey)) return {status:"invalid"};
    return {status:"candidate", value:{runId, receiptSha256:await fileHash(receiptPath),
      backupSha256:before, beforeSha256:before, afterSha256:receipt.afterSha256,
      centerActionFingerprintSha256:fingerprint}};
  } catch { return {status:"invalid"}; }
}
async function restoreCandidate(stateRoot, target) {
  let runs;
  try { runs = await readdir(under(stateRoot, "backups"), {withFileTypes:true}); }
  catch { fail("RESTORE_BACKUP_NOT_FOUND", "RESTORE_RESOLUTION"); }
  const currentPage = await json(target.manifest, "PAGE_INVALID", "RESTORE_RESOLUTION");
  const candidates = [];
  let mismatch = false;
  for (const run of runs.filter(value => value.isDirectory() && safeSegment(value.name))) {
    const inspected = await inspectRestoreRun(stateRoot, run.name, target, currentPage);
    if (inspected.status === "candidate") candidates.push(inspected.value);
    else if (inspected.status !== "unbound") mismatch = true;
  }
  if (candidates.length === 0) {
    fail(mismatch ? "RESTORE_BACKUP_INVALID" : "RESTORE_BACKUP_NOT_FOUND", "RESTORE_RESOLUTION");
  }
  if (candidates.length !== 1) fail("PROFILE_AMBIGUOUS", "RESTORE_RESOLUTION");
  return candidates[0];
}
async function validateRequestedTarget(dataRoot, req) {
  if (req.store !== "ProfilesV2" || !safeSegment(req.groupId) || !safeSegment(req.pageId)) {
    fail("PROFILE_NOT_FOUND", "APPLY_PRECHECK");
  }
  const {rootManifest, manifest} = await validateMutationTarget(
    dataRoot, req.groupId, req.pageId, "APPLY_PRECHECK");
  if (await fileHash(rootManifest) !== req.rootManifestSha256
      || await fileHash(manifest) !== req.manifestSha256) fail("PAGE_INVALID", "APPLY_PRECHECK");
  const root = await json(rootManifest, "MANIFEST_INVALID", "DEVICE_PROFILE_MATCH");
  const state = await json(under(dataRoot, "Config", "setting_source.json"),
    "SETTINGS_SCHEMA_UNSUPPORTED", "SETTINGS_READ");
  if (root?.Device?.Model !== "D200" || hash(String(root?.Name).toLowerCase()) !== req.profileNameSha256
      || hash(String(root?.Device?.UUID).toLowerCase()) !== req.deviceUuidSha256
      || root?.Pages?.Current !== req.pageId || !root?.Pages?.Pages?.includes(req.pageId)
      || !state?.Devices?.some(device =>
        hash(String(device?.CurrentProfile).toLowerCase()) === req.profileNameSha256
        && hash(String(device?.CurrentDevice).toLowerCase()) === req.deviceUuidSha256)) {
    fail("PROFILE_NOT_FOUND", "APPLY_PRECHECK");
  }
  const page = await json(manifest, "MANIFEST_INVALID", "APPLY_PRECHECK");
  setupController(page, req.setupKey, req.setupActionIdSha256);
  return {manifest, page};
}

export async function apply({dataRoot, stateRoot, isStudioClosed=studioClosed,
  afterRename=async()=>{}}) {
  let initiallyClosed;
  try { initiallyClosed = await isStudioClosed(); } catch {}
  if (initiallyClosed !== true) fail("HELPER_PROCESS_FAILED", "APPLY_PRECHECK");
  const req = await request(stateRoot);
  if (req.dataRootHash !== hash(resolve(dataRoot))) fail("REPREPARE_REQUIRED", "APPLY_PRECHECK");
  const {manifest, page} = await validateRequestedTarget(dataRoot, req);
  const current = large(page).Actions["3_2"];
  const expectedCurrent = req.operation === "install" ? BUILTIN : ACTION;
  if (current?.Action !== expectedCurrent) fail("SLOT_UNRELATED", "SLOT_VALIDATION");
  let replacement;
  if (req.operation === "restore") {
    const restore = req.restore;
    if (!restore || !safeSegment(restore.runId)) fail("RESTORE_BACKUP_INVALID", "RESTORE_RESOLUTION");
    const inspected = await inspectRestoreRun(stateRoot, restore.runId,
      {...req, setupKey:req.setupKey}, page);
    const candidate = inspected.value;
    for (const field of ["receiptSha256", "backupSha256", "beforeSha256", "afterSha256",
      "centerActionFingerprintSha256"]) {
      if (inspected.status !== "candidate" || candidate[field] !== restore[field]) {
        fail("RESTORE_BACKUP_INVALID", "RESTORE_RESOLUTION");
      }
    }
    replacement = await readFile(
      under(stateRoot, "backups", restore.runId, "manifest.before.json"), "utf8");
  } else {
    large(page).Actions["3_2"] = generatedCenter(
      req.operation === "repair" ? current.ActionID : randomUUID());
    replacement = JSON.stringify(page, null, 2);
  }
  const run = under(stateRoot, "backups", `${Date.now()}-${randomUUID()}`);
  await mkdir(run, {recursive:true});
  const backup = under(run, "manifest.before.json");
  await cp(manifest, backup);
  const beforeSha256 = await fileHash(backup);
  const temp = under(dirname(manifest), `.mediacontrol-${randomUUID()}.tmp`);
  await writeFile(temp, replacement, "utf8");
  const check = await json(temp, "MANIFEST_INVALID", "READBACK");
  const expected = req.operation === "restore" ? BUILTIN : ACTION;
  const checkedCenter = large(check, "MANIFEST_INVALID", "READBACK").Actions["3_2"];
  if (checkedCenter?.Action !== expected || (expected === ACTION && !normalizedCenter(checkedCenter))) {
    await rm(temp, {force:true}); fail("MANIFEST_INVALID", "READBACK");
  }
  let stillClosed;
  try { stillClosed = await isStudioClosed(); } catch {}
  if (stillClosed !== true) {
    await rm(temp, {force:true}).catch(() => {});
    fail("HELPER_PROCESS_FAILED", "APPLY_PRECHECK");
  }
  await rename(temp, manifest);
  try {
    await afterRename();
    const after = await json(manifest, "MANIFEST_INVALID", "READBACK");
    const afterCenter = large(after, "MANIFEST_INVALID", "READBACK").Actions["3_2"];
    if (afterCenter?.Action !== expected) fail("MANIFEST_INVALID", "READBACK");
    const receipt = {schema:RECEIPT_SCHEMA, operation:req.operation,
      result:req.operation === "restore" ? "restored" : "success",
      target:{store:"ProfilesV2", groupId:req.groupId, pageId:req.pageId, key:"3_2"},
      beforeSha256, afterSha256:await fileHash(manifest), backupSha256:await fileHash(backup),
      action:ACTION, timestampUtc:new Date().toISOString()};
    if (req.operation === "install") receipt.centerActionFingerprintSha256 = centerFingerprint(afterCenter);
    await atomicWrite(under(run, "receipt.json"), JSON.stringify(receipt));
    return receipt;
  } catch (error) {
    const rollback = under(dirname(manifest), `.mediacontrol-${randomUUID()}.rollback`);
    try {
      await cp(backup, rollback); await rename(rollback, manifest);
      if (await fileHash(manifest) !== beforeSha256) fail("HELPER_PROCESS_FAILED",
        req.operation === "restore" ? "RESTORE_WRITE" : "PATCH_WRITE");
    } catch {
      await rm(rollback, {force:true});
      fail("HELPER_PROCESS_FAILED", req.operation === "restore" ? "RESTORE_WRITE" : "PATCH_WRITE");
    }
    throw error;
  }
}

function args(values) {
  const result = {};
  for (let index = 0; index < values.length; index += 2) {
    result[values[index]?.replace(/^--/, "")] = values[index + 1];
  }
  return result;
}
export async function main(argv=process.argv.slice(2), {
  readBundleIdentifier=async()=>{
    const {stdout} = await execFile("defaults",
      ["read", "/Applications/Ulanzi Studio.app/Contents/Info", "CFBundleIdentifier"]);
    return stdout.trim();
  }, isStudioClosed=studioClosed, now=Date.now,
  wait=()=>new Promise(done => setTimeout(done, 500)),
}={}) {
  const values = args(argv);
  const stateRoot = values["state-root"] || join(homedir(), "Library", "Application Support",
    "GSMTCD200Controller", "large-display-setup");
  const dataRoot = values["data-root"] || join(homedir(), "Library", "Application Support",
    "Ulanzi", "UlanziDeck");
  const handshakeId = values["handshake-id"] || randomUUID();
  try {
    await writeStatus(stateRoot, "started", "PREPARING", "INITIALIZING", handshakeId,
      values["setup-action-id"]);
    const pluginRoot = values["plugin-root"];
    if (!pluginRoot) fail("COMPATIBILITY_UNSUPPORTED", "COMPAT_PLUGIN_ROOT");
    await validateStudioIdentity(pluginRoot, {bundleIdentifier:await readBundleIdentifier()});
    if (values.mode === "assistant") {
      await prepare({dataRoot, stateRoot, key:values["pressed-key"],
        setupActionId:values["setup-action-id"], operation:values.operation});
      await writeStatus(stateRoot, "prepared", "PREPARED", "REQUEST_WRITE", handshakeId,
        values["setup-action-id"]);
      const deadline = now() + 900000;
      while (!await isStudioClosed()) {
        if (now() >= deadline) fail("HELPER_PROCESS_FAILED", "APPLY_PRECHECK");
        await wait();
      }
      const receipt = await apply({dataRoot, stateRoot, isStudioClosed});
      const code = receipt.result === "restored" ? "RESTORED" : "SUCCESS";
      await writeStatus(stateRoot, receipt.result, code, "MANUAL_REOPEN", handshakeId,
        values["setup-action-id"]);
      process.stdout.write(`MEDIACONTROL_DIAGNOSTIC:${code}:MANUAL_REOPEN\n`);
    } else if (values.mode === "prepare") {
      await prepare({dataRoot, stateRoot, key:values["pressed-key"],
        setupActionId:values["setup-action-id"], operation:values.operation});
      await writeStatus(stateRoot, "prepared", "PREPARED", "REQUEST_WRITE", handshakeId,
        values["setup-action-id"]);
    } else if (values.mode === "apply") {
      const receipt = await apply({dataRoot, stateRoot, isStudioClosed});
      const code = receipt.result === "restored" ? "RESTORED" : "SUCCESS";
      await writeStatus(stateRoot, receipt.result, code, "MANUAL_REOPEN", handshakeId,
        values["setup-action-id"]);
    } else fail("HELPER_PROCESS_FAILED", "INITIALIZING");
    return true;
  } catch (error) {
    const code = CODES.has(error?.code) ? error.code : "HELPER_PROCESS_FAILED";
    const phase = PHASES.has(error?.phase) ? error.phase : "INITIALIZING";
    try { await writeStatus(stateRoot, "failed", code, phase, handshakeId,
      values["setup-action-id"]); } catch {}
    process.stderr.write(`MEDIACONTROL_DIAGNOSTIC:${code}:${phase}\n`);
    return false;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main() ? 0 : 1;
}
