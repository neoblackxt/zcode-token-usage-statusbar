#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit hook：每次用户发消息时
1) 记录"用户最后所处的会话"与提问时间戳到状态文件（--current 守卫依赖该时间戳）
2) clean 分叉：**无条件不注入任何上下文**（additionalContext 恒为空串）
速率统计行已改为确定性渲染（overlay.js 经 renderer 只读桥读取会话状态，完成的 turn
在消息流末尾由前端插入统计行），模型零配合、零额外工具调用。上游曾把"上一轮速率背景
行"注入模型上下文——clean 分叉彻底移除该注入（⚙ 的 answer_rate_line 只控制前端统计行
这一纯 UI 功能，与本 hook 无关，开启它也不会恢复注入）；每次提问仍记录时刻（守卫数据
保持新鲜）。输出只包含 hook 协议字段，诊断不得混入 stdout。
"""
import json
import os
import sys
import threading
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def read_stdin(timeout_s=2.0):
    """消费 stdin（协议要求），设超时兜底：客户端不给 EOF 也不挂起。内容不使用。"""
    result = {"raw": ""}

    def pump():
        try:
            result["raw"] = sys.stdin.read()
        except Exception:
            pass

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t.join(timeout_s)
    return result["raw"]


def emit(ctx):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": ctx,
        }
    }, ensure_ascii=False))


def rate_line_enabled():
    """⚙ 面板开关：运行时 config.json 的 answer_rate_line（控制前端"回答末尾统计行"）。

    clean 分叉中本 hook 不再消费该开关（无条件零注入）；保留函数供契约兼容与
    独立 CLI 排查使用。运行时目录与 overlay_install.default_runtime() 同源，
    含 ZUSAGE_PLUGIN_RUNTIME 测试接缝。"""
    runtime = Path(os.environ.get("ZUSAGE_PLUGIN_RUNTIME")
                   or Path.home() / ".zcode" / "zcode-token-usage-statusbar")
    try:
        cfg = json.loads((runtime / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return True
    return not (isinstance(cfg, dict) and cfg.get("answer_rate_line") is False)


def main():
    read_stdin()
    # 启动策略按 agent 分流：逐问速率行依赖 ZCode 的渲染桥与 token_rate 状态文件，
    # 其它宿主（WorkBuddy）没有这条链，静默跳过而不是写无用状态。
    try:
        sys.path.insert(0, str(PLUGIN_ROOT))
        import zusage
        if zusage.detect_agent() != zusage.AGENT_ZCODE:
            print(json.dumps({"suppressOutput": True}, ensure_ascii=False))
            return
    except Exception:   # noqa: BLE001 — 判定失败按 ZCode 老路径继续
        pass
    sid = os.environ.get("ZCODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""
    try:
        sys.path.insert(0, str(PLUGIN_ROOT))
        import token_rate
        if sid:
            token_rate.write_session_state(sid, "prompt-submit")   # 守卫数据保持新鲜
    except Exception:
        pass
    emit("")   # clean 分叉：无条件零注入（含速率背景行；统计行由前端确定性渲染）


if __name__ == "__main__":
    # Windows 默认（无全局 PYTHONUTF8）下中文输出会按区域编码（GBK 等）写管道，
    # 宿主按 UTF-8 解码 hook stdout 会得到乱码——与 usage_mcp.py 同款防护。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
