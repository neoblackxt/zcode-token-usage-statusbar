"use strict";
// v179 自动压缩阈值接线回归：pollPluginRequests 白名单新分支（autocompact-patch/
// autocompact-config）分发契约 + pushPluginState 的 enginePatch 状态字段注入。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const { getInjectSlice } = require("./helpers/overlay-extract.cjs");

// clean 分叉契约测试用：读主 loader 源码钉死缺省关门控写法
const source = fs.readFileSync(path.join(__dirname, "..", "inject-main.cjs"), "utf8");

function pollSlice() {
  return getInjectSlice("pollPluginRequests");
}

function runPoll(requests, stubs) {
  const HOST_URL = require("url").pathToFileURL(path.join(path.sep, "client", "resources", "app", "out", "main", "index.js")).href;
  const wc = { isDestroyed: () => false, getURL: () => HOST_URL, executeJavaScript: async () => requests };
  const calls = { patch: [], config: [], deliveries: [] };
  vm.runInNewContext(pollSlice() + "\npollPluginRequests();", {
    path, fileURLToPath: require("url").fileURLToPath,
    process: { platform: process.platform, resourcesPath: path.join(path.sep, "client", "resources") },
    webContents: { getAllWebContents: () => [wc] },
    isMainWindowState: () => true,
    pluginRequestBusy: false,
    pluginUpdater: null, quotaClient: null, enginePatcher: null, modelPuller: null, gitTick: async () => { },
    setAnswerRateLine: () => { }, setRestartNotice: () => { }, setQuotaEnabled: () => { }, setResetBadgeEnabled: () => { },
    handleQuotaProviders: () => { }, handleQuotaSourceRequest: () => { }, handleModelPullRequest: () => { },
    queryTurnText: () => { }, quotaEnabled: () => true,
    setAutoCompactPatch: (enabled, wc2, requestId) => calls.patch.push({ enabled, requestId }),
    handleAutoCompactConfig: (req2) => calls.config.push({ mode: req2.mode, value: req2.value, requestId: req2.requestId }),
    deliverPluginRequest: (w, requestId, result) => calls.deliveries.push({ requestId, result }),
    LOG: () => { }, setTimeout, clearTimeout, Promise,
  });
  return calls;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 80));   // pollPluginRequests 是 async：等分发落账再断言

test("autocompact-patch dispatches only with a strict boolean", async () => {
  const ok = runPoll([{ action: "autocompact-patch", enabled: true, requestId: "r1" }]);
  await settle();
  assert.deepEqual(ok.patch, [{ enabled: true, requestId: "r1" }]);
  const bad = runPoll([{ action: "autocompact-patch", enabled: "yes", requestId: "r2" }]);
  await settle();
  assert.deepEqual(bad.patch, [], "非布尔拒绝：不落白名单分支");
});

test("autocompact-config dispatches mode/value passthrough", async () => {
  const calls = runPoll([{ action: "autocompact-config", mode: "percent", value: 70, requestId: "r3" }]);
  await settle();
  assert.deepEqual(calls.config, [{ mode: "percent", value: 70, requestId: "r3" }]);
});

test("clean 分叉契约：引擎补丁缺省关，显式 true 才打", () => {
  // 上游 v188 起缺省开（且头注与代码不一致——注释写默认关、代码 !== false），该默认值
  // 上游一直零测试覆盖。clean 分叉改缺省关，这里钉死为源码契约，防止合并上游时静默回归。
  assert.ok(source.includes("isEnabled: () => readCfgCached().auto_compact_patch === true"),
    "inject-main.cjs 引擎补丁 isEnabled 必须是显式 === true（clean 分叉缺省关）");
  assert.ok(!source.includes("auto_compact_patch !== false"),
    "不得回归上游缺省开写法（auto_compact_patch !== false）");
});

function pushStateSlice() {
  return getInjectSlice("pushPluginState");
}

test("pushPluginState carries enginePatch status for the settings panel", () => {
  let captured = null;
  const wc = { isDestroyed: () => false, executeJavaScript: async (js) => { captured = js; } };
  vm.runInNewContext(pushStateSlice() + "\npushPluginState(wc, { foo: 1 });", {
    wc,
    isMainWindowState: () => true, isLocalHostPage: () => true,
    readCfgCached: () => ({ answer_rate_line: false, restart_notice: true }),
    quotaEnabled: () => false, resetBadgeEnabled: () => true, quotaClient: null,
    enginePatcher: { status: () => ({ enabled: true, state: "applied", build: "3.17.1" }) },
    JSON, webContentsDummy: 0,
  });
  assert.ok(captured && captured.includes("window.__zusagePluginUpdate"), "走 __zusagePluginUpdate 通道");
  const payload = JSON.parse(captured.replace(/^window\.__zusagePluginUpdate && window\.__zusagePluginUpdate\(/, "").replace(/\)$/s, ""));
  assert.equal(payload.enginePatch.state, "applied");
  assert.equal(payload.enginePatch.build, "3.17.1");
  assert.equal(payload.rateLine, false);
  assert.equal(payload.restartNotice, true);
});

test("pushPluginState survives a missing engine patcher (null field)", () => {
  let captured = null;
  const wc = { isDestroyed: () => false, executeJavaScript: async (js) => { captured = js; } };
  vm.runInNewContext(pushStateSlice() + "\npushPluginState(wc, {});", {
    wc,
    isMainWindowState: () => true, isLocalHostPage: () => true,
    readCfgCached: () => ({}),
    quotaEnabled: () => false, resetBadgeEnabled: () => false, quotaClient: null,
    enginePatcher: null,
    JSON,
  });
  assert.ok(captured.includes('"enginePatch":null'), "patcher 缺失时推送 null（旧主进程兼容形状）");
});
