const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { getInjectSlice } = require('./helpers/overlay-extract.cjs');

// 切出 CDP 调试口块（注释首 → catch 收尾），在受控桩下跑各分支。
// clean 分叉契约（改造表 #12）：cdp_debug_port 缺省关——键省略/false/损坏 config/无
// config 文件都不 append；显式 true 才补开。env 逃生口与用户显式端口优先于 true。
const block = getInjectSlice('cdpSwitchBlock');

function harness() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zusage-cdp-"));
  const configFile = path.join(root, "config.json");
  const calls = [];
  const appendSwitch = (...args) => calls.push(['append', ...args]);
  const commandLine = { hasSwitch: () => false, appendSwitch };
  const runBlock = ({ env = {}, cfg = undefined, cl = commandLine } = {}) => {
    calls.length = 0;
    try { fs.unlinkSync(configFile); } catch (e) {}
    if (cfg !== undefined) fs.writeFileSync(configFile, JSON.stringify(cfg));
    const sandbox = vm.createContext({
      process: { env },
      LOG: (...args) => { calls.push(['log', ...args]); },
      fs: { readFileSync: (file) => fs.readFileSync(file, "utf8") },
      CONFIG: configFile,
      app: { commandLine: cl === null ? undefined : cl },   // null = 桩缺失 commandLine
    });
    vm.runInContext(block, sandbox);
  };
  return { runBlock, calls, cleanup: () => fs.rmSync(root, { recursive: true, force: true }) };
}
const APPEND = ['append', 'remote-debugging-port', '9229'];

test('clean 分叉缺省关：无 config 文件、键省略、false 都不 append', () => {
  const h = harness();
  try {
    for (const cfg of [undefined, {}, { cdp_debug_port: false }, { cdp_debug_port: 0 }, { cdp_debug_port: "true" }]) {
      h.runBlock({ cfg });
      assert.deepStrictEqual(h.calls, [], `cfg=${JSON.stringify(cfg)}`);
    }
  } finally { h.cleanup(); }
});

test('clean 分叉缺省关：损坏 config 视为关（内层 try 吞掉解析错误）', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zusage-cdp-"));
  try {
    const bad = path.join(root, "bad.json");
    fs.writeFileSync(bad, "{not json");
    const calls = [];
    vm.runInContext(block, vm.createContext({
      process: { env: {} },
      LOG: (...a) => calls.push(['log', ...a]),
      fs: { readFileSync: (f) => fs.readFileSync(f, "utf8") },
      CONFIG: bad,
      app: { commandLine: { hasSwitch: () => false, appendSwitch: (...a) => calls.push(['append', ...a]) } },
    }));
    assert.deepStrictEqual(calls, []);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('显式 cdp_debug_port:true 时按名追加 9229', () => {
  const h = harness();
  try {
    h.runBlock({ cfg: { cdp_debug_port: true } });
    assert.deepStrictEqual(h.calls, [APPEND]);
  } finally { h.cleanup(); }
});

test('env 逃生口 ZCODE_DISABLE_FIXED_REMOTE_DEBUGGING_PORT=1 优先于 true：跳过；"0" 不视为关闭', () => {
  const h = harness();
  try {
    h.runBlock({ env: { ZCODE_DISABLE_FIXED_REMOTE_DEBUGGING_PORT: '1' }, cfg: { cdp_debug_port: true } });
    assert.deepStrictEqual(h.calls, []);
    h.runBlock({ env: { ZCODE_DISABLE_FIXED_REMOTE_DEBUGGING_PORT: '0' }, cfg: { cdp_debug_port: true } });
    assert.deepStrictEqual(h.calls, [APPEND]);
  } finally { h.cleanup(); }
});

test('用户显式已传 --remote-debugging-port 时不覆盖（含 true 场景）', () => {
  const h = harness();
  try {
    for (const cfg of [undefined, { cdp_debug_port: true }]) {
      h.runBlock({ cfg, cl: { hasSwitch: () => true, appendSwitch: (...a) => h.calls.push(['append', ...a]) } });
      assert.deepStrictEqual(h.calls, [], `cfg=${JSON.stringify(cfg)}`);
    }
  } finally { h.cleanup(); }
});

test('stub 缺 commandLine 时退回 catch 日志路径，不抛出（true 场景亦无 append）', () => {
  const h = harness();
  try {
    assert.doesNotThrow(() => h.runBlock({ cfg: { cdp_debug_port: true }, cl: null }));
    assert.strictEqual(h.calls.filter(c => c[0] === 'log').length, 1);
    assert.deepStrictEqual(h.calls.filter(c => c[0] === 'append'), []);
  } finally { h.cleanup(); }
});
