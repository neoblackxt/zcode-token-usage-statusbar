# -*- coding: utf-8 -*-
"""SessionStart 输出通道回归；HOME、运行时和 asar 全部使用临时夹具。"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "zcode-token-usage-statusbar"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SessionStartChannelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.runtime = self.root / "runtime"
        self.asar = self.root / "client" / "resources" / "app.asar"
        self.home.mkdir()
        self.runtime.mkdir()
        self.asar.parent.mkdir(parents=True)
        environment = {"HOME": str(self.home), "USERPROFILE": str(self.home),
                       "ZUSAGE_PLUGIN_RUNTIME": str(self.runtime), "ZUSAGE_PLUGIN_ASAR": str(self.asar),
                       "ZCODE_SESSION_ID": "sess_channel_test", "PYTHONDONTWRITEBYTECODE": "1",
                       # 子进程输出编码必须与父进程的解码一致：默认中文 Windows 上清空环境会
                       # 丢掉全局 PYTHONUTF8，子进程按 GBK 写中文诊断、父进程读线程按
                       # UTF-8 解码直接抛 UnicodeDecodeError（stdout 收成 None，测试炸在
                       # json.loads(None)）。补回 UTF-8 模式保证两种 locale 的机器行为一致。
                       "PYTHONUTF8": "1",
                       "PATH": os.environ.get("PATH", ""),
                       **{name: os.environ[name] for name in ("SystemRoot", "TEMP", "TMP")
                          if os.name == "nt" and name in os.environ}}
        self.patch(mock.patch.dict(os.environ, environment, clear=True))
        self.patch(mock.patch.object(sys, "path", list(sys.path)))
        self.installer = load_module("channel_installer", PLUGIN / "overlay_install.py")
        self.notice = load_module("channel_notice", PLUGIN / "restart_notice.py")
        self.pi = load_module("channel_patch_install", PLUGIN / "patch_install.py")
        self.hook = load_module("channel_session_start", PLUGIN / "hooks" / "session-start.py")
        self.updater = mock.Mock(spec=["start_auto_update"])
        self.rate = mock.Mock(spec=["write_session_state"])
        self.patch(mock.patch.dict(sys.modules, {"overlay_install": self.installer,
                   "restart_notice": self.notice, "plugin_auto_update": self.updater,
                   "token_rate": self.rate}))
        self.patch(mock.patch.object(self.installer, "load_patch_install", return_value=self.pi))
        self.sync = self.patch(mock.patch.object(self.installer, "sync_runtime", return_value={"changed": []}))
        self.install = self.patch(mock.patch.object(self.pi, "install", return_value=False))
        self.pi.set_runtime(self.runtime)
        self.pi.set_target(self.asar)
        self.make_asar()
        self.config = self.runtime / "config.json"
        self.config.write_text('{"plugin_auto_update": false, "python_path": "custom"}', encoding="utf-8")

    def patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def make_asar(self, injected=False):
        entry = b"official;" + (self.pi.INJECT_LINE.encode("utf-8") if injected else b"")
        header = {"files": {"out": {"files": {"main": {"files": {"index.js": {
            "offset": "0", "size": len(entry)}}}}}}}
        raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
        aligned = (len(raw) + 7) // 4 * 4
        self.asar.write_bytes(struct.pack("<4I", 4, aligned + 4, aligned, len(raw)) + raw +
                              b"\0" * (aligned - len(raw) - 4) + entry)

    def invoke(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO('{"source": "resume"}')), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.hook.main()
        output = json.loads(stdout.getvalue())
        self.assertEqual(stdout.getvalue(), json.dumps(output, ensure_ascii=False) + "\n")
        self.assertIsNot(output.get("continue"), False)
        self.assertNotEqual(output.get("decision"), "block")
        self.assertNotIn("stopReason", output)
        self.assertTrue(output["suppressOutput"])
        self.assertEqual(list(self.home.iterdir()), [])
        diagnostic = self.runtime / "plugin-session-start.json"
        if diagnostic.exists():
            record = json.loads(diagnostic.read_text(encoding="utf-8"))
            self.assertEqual(record["result"], output)
            self.assertEqual(record["source"], "resume")
            self.assertEqual(record["sessionId"], "sess_channel_test")
            self.assertEqual(record["pluginRoot"], str(PLUGIN))
            self.assertGreater(record["time"], 0)
        return output, stderr.getvalue()

    def failure_message(self, output):
        """clean 分叉：故障提示只走 systemMessage（stderr + UI 展示），模型上下文零注入。"""
        self.assertNotIn("hookSpecificOutput", output, "clean 分叉不得向模型上下文注入文字")
        self.assertNotIn("additionalContext", output)
        message = output.get("systemMessage")
        self.assertIsInstance(message, str, "故障提示必须落在 systemMessage")
        return message

    def test_run_hook_resolution_sync_and_install_errors_reach_context_and_diagnostic(self):
        # clean 分叉反转：resolve/sync 异常 → systemMessage（原 additionalContext 通道移除）；
        # _install_overlay 根本不被 hook 调用（无安装行为，也就没有安装错误路径）。
        for target in ("resolve_paths", "sync_runtime"):
            with self.subTest(target=target), mock.patch.object(
                    self.installer, target, side_effect=OSError("fixture denied: " + target)):
                before = self.asar.read_bytes()
                output, errors = self.invoke()
                self.assertIn("自检失败（已忽略）", self.failure_message(output))
                self.assertIn("fixture denied: " + target, self.failure_message(output))
                self.assertEqual(self.asar.read_bytes(), before)
                self.assertIn("fixture denied: " + target, errors)   # stderr 同步诊断
                self.assertTrue((self.runtime / "plugin-session-start.json").is_file())
        with mock.patch.object(self.installer, "_install_overlay",
                               side_effect=AssertionError("hook must not install")):
            before = self.asar.read_bytes()
            output, errors = self.invoke()
        self.assertEqual(output, {"suppressOutput": True})
        self.assertEqual(self.asar.read_bytes(), before)
        self.assertTrue((self.runtime / "plugin-session-start.json").is_file())
        self.assertEqual(self.rate.write_session_state.call_count, 3)

    def test_entry_catches_unexpected_run_hook_failure_with_context_and_diagnostic(self):
        with mock.patch.object(self.installer, "run_hook", side_effect=RuntimeError("fixture bootstrap failed")):
            output, errors = self.invoke()
        self.assertIn("fixture bootstrap failed", self.failure_message(output))
        self.assertIn("fixture bootstrap failed", errors)
        self.assertTrue((self.runtime / "plugin-session-start.json").is_file())

    def test_entry_import_failure_keeps_json_and_context_without_notice_helper(self):
        with mock.patch.dict(sys.modules, {"overlay_install": None, "restart_notice": None}):
            output, errors = self.invoke()
        self.assertIn("自检失败（已忽略）", self.failure_message(output))
        self.assertIn("会话启动诊断写入失败", errors)
        self.assertFalse((self.runtime / "plugin-session-start.json").exists())

    def test_diagnostic_failure_does_not_hide_error_context_or_pollute_stdout(self):
        self.sync.side_effect = ValueError("fixture corrupt record")
        with mock.patch.object(self.installer, "_atomic_write", side_effect=OSError("fixture diagnostic denied")):
            output, errors = self.invoke()
        self.assertIn("fixture corrupt record", self.failure_message(output))
        self.assertIn("会话启动诊断写入失败", errors)
        self.assertIn("fixture diagnostic denied", errors)
        self.assertFalse((self.runtime / "plugin-session-start.json").exists())

    def test_uninstalled_attempts_auto_install_and_preserves_config(self):
        # clean 分叉反转：未注入时 hook 不安装也不提示——install 零调用、配置零写入，
        # 用户其余配置与旧 nudge 标记原样保留；未注入说明只走 stderr 日志行。
        self.config.write_text('{"plugin_auto_update": false, '
                               '"python_path": "custom", "other": 7}', encoding="utf-8")
        legacy = self.runtime / ".plugin-overlay-nudge"
        legacy.write_text("1", encoding="utf-8")
        for _ in range(2):
            output, errors = self.invoke()
            self.assertEqual(output, {"suppressOutput": True})
            self.assertIn("不自动安装", errors)
        self.assertEqual(self.install.call_count, 0)
        config = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertIs(config["plugin_auto_update"], False)
        self.assertEqual(config["python_path"], "custom")
        self.assertEqual(config["other"], 7)
        self.assertNotIn("asar_path", config)
        self.assertEqual(legacy.read_text(encoding="utf-8"), "1")

    def test_corrupt_config_still_attempts_auto_install(self):
        # clean 分叉反转：损坏/异常配置不再触发安装尝试（本就没有安装行为）；
        # 损坏配置保持原样不重建，合法配置原样保留不被合并改写。
        for raw in ("{broken", "null", '{"plugin_auto_install_overlay": "false"}'):
            with self.subTest(raw=raw):
                self.config.write_text(raw, encoding="utf-8")
                output, _ = self.invoke()
                self.assertEqual(output, {"suppressOutput": True})
                self.install.assert_not_called()
                if raw in ("{broken", "null"):
                    self.assertEqual(self.config.read_text(encoding="utf-8"), raw)
                else:
                    self.assertEqual(json.loads(self.config.read_text())["plugin_auto_install_overlay"], "false")

    def test_unfinished_auto_install_nudge_restores_monitor_flags_and_redirects_logs(self):
        # clean 分叉反转：hook 不运行安装器，监控静默标志全程不被设置也不需还原；
        # 安装器 stdout（人类可读日志）自然无从混入 hook stdout。
        def unfinished(before_publish):
            raise AssertionError("hook must not auto-install in the clean fork")
        self.install.side_effect = unfinished
        with mock.patch.dict(os.environ, {"ZUSAGE_NO_MONITOR": "custom", "ZUSAGE_MONITOR_QUIET": "0"}):
            for _ in range(2):
                output, _ = self.invoke()
                self.assertEqual(output, {"suppressOutput": True})
                self.assertEqual(os.environ["ZUSAGE_NO_MONITOR"], "custom")
                self.assertEqual(os.environ["ZUSAGE_MONITOR_QUIET"], "0")

    def test_pending_install_failure_nudges_full_quit_instead_of_retry(self):
        # clean 分叉反转：issue #120 的「已排定/完全退出」承诺随自动安装一起消失——
        # hook 不安装、不判定待收尾状态、不给任何重试或退出指引。
        self.install.side_effect = lambda before_publish: False
        with mock.patch.object(self.pi, "pending_tmp", return_value={"operation": "install"}) as pending, \
                mock.patch.object(self.pi, "client_running", return_value=True):
            output, _ = self.invoke()
        pending.assert_not_called()   # 不再判定待收尾包
        self.assertEqual(output, {"suppressOutput": True})
        self.install.assert_not_called()

    def test_pending_without_running_client_keeps_retry_hint(self):
        # clean 分叉反转：待收尾包 + 客户端未运行同样零动作零提示。
        self.install.side_effect = lambda before_publish: False
        with mock.patch.object(self.pi, "pending_tmp", return_value={"operation": "install"}), \
                mock.patch.object(self.pi, "client_running", return_value=False):
            output, _ = self.invoke()
        self.assertEqual(output, {"suppressOutput": True})
        self.install.assert_not_called()

    def test_install_exception_restores_previously_absent_monitor_flags(self):
        # clean 分叉反转：安装器异常无从发生（不会被调用）；标志保持进入前缺失状态。
        self.install.side_effect = OSError("fixture install denied")
        output, _ = self.invoke()
        self.assertEqual(output, {"suppressOutput": True})
        self.install.assert_not_called()
        self.assertNotIn("ZUSAGE_NO_MONITOR", os.environ)
        self.assertNotIn("ZUSAGE_MONITOR_QUIET", os.environ)

    def test_installed_no_session_text_notice_and_marker_untouched(self):
        # v172：已注入时不再注入重启提醒文字——待重启由状态条齿轮红点承载（overlay 消费
        # pluginUpdate.restartRequired）；marker 只读不动，等新进程 loader ack。
        self.make_asar(injected=True)
        self.notice.record_restart(self.runtime)
        marker = self.runtime / self.notice.MARKER
        before = (marker.read_bytes(), marker.stat().st_mtime_ns)
        self.sync.return_value = {"changed": ["inject-main.cjs"]}
        output, _ = self.invoke()
        self.assertEqual(output, {"suppressOutput": True})
        self.assertEqual((marker.read_bytes(), marker.stat().st_mtime_ns), before)
        self.install.assert_not_called()

    def test_successful_auto_install_stays_silent(self):
        # clean 分叉反转：不存在"自动安装成功"路径——hook 零安装，asar 保持官方字节，
        # 未注入说明只走 stderr 日志行，stdout 仍为纯 hook JSON。
        def install(before_publish):
            raise AssertionError("hook must not auto-install in the clean fork")
        self.install.side_effect = install
        before = self.asar.read_bytes()
        output, errors = self.invoke()
        self.assertEqual(output, {"suppressOutput": True})
        self.install.assert_not_called()
        self.assertEqual(self.asar.read_bytes(), before)
        self.assertIn("不自动安装", errors)
        self.assertIn("install-overlay", errors)

    def test_incomplete_restart_record_stays_silent_in_session(self):
        # v172：已注入时记录不可验证也不注入文字；修复文案只由 /install-overlay CLI 反馈。
        self.make_asar(injected=True)
        self.notice.record_restart(self.runtime, {"state": "pending-sync", "hashes": {"inject-main.cjs": "a" * 64}})
        (self.runtime / "plugin-runtime-pending.json").write_text("{}", encoding="utf-8")
        output, _ = self.invoke()
        self.assertEqual(output, {"suppressOutput": True})

    def test_no_marker_or_unreadable_client_stays_silent(self):
        for readable in (True, False):
            with self.subTest(readable=readable):
                if readable:
                    self.make_asar(injected=True)
                else:
                    self.asar.write_bytes(b"fixture unreadable asar")
                output, _ = self.invoke()
                self.assertEqual(output, {"suppressOutput": True})
        self.install.assert_not_called()

    def test_optout_suppresses_normal_and_failed_selfcheck_without_config_changes(self):
        # clean 分叉反转：豁免标记不再压制诊断——失败照样 systemMessage + stderr；
        # 正常路径与失败路径都不写 config（未注入不补 asar_path）、不动豁免标记。
        optout = self.runtime / ".plugin-overlay-optout"
        optout.write_text("1", encoding="utf-8")
        self.notice.record_restart(self.runtime)
        before = self.config.read_bytes()
        for fail in (False, True):
            with self.subTest(fail=fail):
                self.sync.side_effect = OSError("fixture opted-out failure") if fail else None
                output, errors = self.invoke()
                if fail:
                    self.assertIn("fixture opted-out failure", self.failure_message(output))
                    self.assertIn("fixture opted-out failure", errors)
                else:
                    self.assertEqual(output, {"suppressOutput": True})
                    self.assertIn("不自动安装", errors)
                self.assertEqual(self.config.read_bytes(), before)
                self.assertEqual(optout.read_text(encoding="utf-8"), "1")
        self.install.assert_not_called()

    def test_optout_suppresses_entry_and_import_failures(self):
        # clean 分叉反转：optout 不改变入口失败的行为——systemMessage 照发、诊断照写。
        (self.runtime / ".plugin-overlay-optout").write_text("1", encoding="utf-8")
        with mock.patch.object(self.installer, "run_hook", side_effect=RuntimeError("fixture entry denied")):
            output, errors = self.invoke()
        self.assertIn("fixture entry denied", self.failure_message(output))
        self.assertIn("fixture entry denied", errors)
        self.assertTrue((self.runtime / "plugin-session-start.json").is_file())
        # import 失败分支写不了诊断文件：先清掉上一分支的记录，避免陈旧内容干扰比对
        (self.runtime / "plugin-session-start.json").unlink()
        with mock.patch.dict(sys.modules, {"overlay_install": None}):
            output, errors = self.invoke()
        self.assertIn("自检失败", self.failure_message(output))
        self.assertIn("自检失败", errors)
        self.assertFalse((self.runtime / "plugin-session-start.json").exists())

    def test_unreadable_optout_in_entry_failure_keeps_legal_json(self):
        # clean 分叉：hook/入口不再读豁免标记——stat 失败与入口失败互不相干，输出仍合法 JSON。
        stat = Path.stat
        def unreadable(path, *args, **kwargs):
            if path.name == ".plugin-overlay-optout":
                raise PermissionError("fixture stat denied")
            return stat(path, *args, **kwargs)
        with mock.patch.object(self.installer, "run_hook", side_effect=RuntimeError("fixture entry denied")), \
                mock.patch.object(Path, "stat", unreadable):
            output, errors = self.invoke()
        self.assertIn("fixture entry denied", self.failure_message(output))
        self.assertIn("fixture entry denied", errors)

    def test_background_update_failure_is_stderr_only(self):
        # v172：已注入时后台更新失败只走 stderr 诊断，不再有会话内重启提醒文字。
        self.make_asar(injected=True)
        self.notice.record_restart(self.runtime)
        self.updater.start_auto_update.side_effect = OSError("fixture updater denied")
        output, errors = self.invoke()
        self.assertEqual(output, {"suppressOutput": True})
        self.assertIn("后台更新启动失败（已忽略）", errors)
        self.assertIn("fixture updater denied", errors)

    def test_process_failure_exits_zero_and_emits_single_json_with_diagnostic(self):
        script = ("import runpy, sys; sys.path.insert(0, {!r}); import overlay_install; "
                  "overlay_install.sync_runtime = lambda runtime, target_asar=None: (_ for _ in ()).throw(ValueError('fixture sync rejected')); "
                  "runpy.run_path({!r}, run_name='__main__')"
                  ).format(str(PLUGIN), str(PLUGIN / "hooks" / "session-start.py"))
        environment = dict(os.environ)
        environment.pop("ZCODE_SESSION_ID")
        process = subprocess.run([sys.executable, "-B", "-c", script], input='{"source": "startup"}',
                                 capture_output=True, text=True, env=environment, cwd=str(self.home), timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        output = json.loads(process.stdout)
        self.assertEqual(process.stdout, json.dumps(output, ensure_ascii=False) + "\n")
        self.assertIn("fixture sync rejected", self.failure_message(output))
        self.assertTrue(output["suppressOutput"])
        self.assertNotIn("decision", output)
        self.assertNotIn("hookSpecificOutput", output)
        record = json.loads((self.runtime / "plugin-session-start.json").read_text(encoding="utf-8"))
        self.assertEqual(record["result"], output)
        self.assertEqual(record["source"], "startup")
        self.assertEqual(record["sessionId"], "")
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
