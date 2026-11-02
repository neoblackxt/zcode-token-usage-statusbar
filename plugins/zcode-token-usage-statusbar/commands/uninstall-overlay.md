---
description: 从本机 ZCode 桌面客户端卸载悬浮用量状态条并还原全部客户端修改（重启客户端生效）
---
从本机 ZCode 桌面客户端卸载 zcode-token-usage-statusbar（clean 分叉）的悬浮状态条。

执行：用 Bash 运行（插件根目录 = 本命令文件所在目录的上一级）：

    python3 "<插件根目录>/overlay_install.py" remove

结果处理：

- 成功（含"当前 asar 未注入"）：告知用户「重启 ZCode 后浮层消失」。卸载会：剥 asar 注入行、剥渲染桥、**还原引擎自动压缩补丁**（若打过，clean 分叉新增；原版卸载会残留）并清理 `.pre-acthreshold-*` 备份，最后写豁免标记。
- 失败（替换失败待收尾）：让用户完全退出 ZCode 后重跑同一条命令。
- clean 分叉本就不自动安装（豁免标记仅具兼容意义）；若用户只是想保留浮层但停止某些功能，引导其在 ⚙ 面板关闭对应开关，不要执行 remove。

用户附加要求：$ARGUMENTS
