# clean 分叉：去入侵化改造说明

本分叉基于上游 [ByteRax/zcode-token-usage-statusbar](https://github.com/ByteRax/zcode-token-usage-statusbar) **v1.5.9**（tag `v1.5.9`，commit `3dba150`），分支 `clean`。清单版本号保持 `1.5.9`（release_tool 强制严格 SEMVER，且与上游同版本可原地替换、不触发插件管理器升级提示）；分叉身份由 `clean` 分支、README 横幅与本文件承载。

目标：**完整保留状态条/用量统计/额度显示等功能，消灭一切未经显式同意的"自动行为"**——
不向模型上下文注入任何文字、不自动修改客户端文件、不自动联网检查更新、不自动打引擎补丁。
功能本身都还在，只是全部改为**显式开启**。

本目标已固化为仓库最高规范：[AGENTS.md](AGENTS.md) 首节「分叉最高原则：上下文零入侵」——
对本仓库工作的任何人（含 AI 助手）具有约束力，与上游合并冲突时按该原则裁剪。

## 一、改造清单（相对上游 v1.5.9）

| # | 行为 | 上游 v1.5.9 | clean 分叉 | 实现位置 |
|---|---|---|---|---|
| 1 | SessionStart 未注入时自动安装（改 app.asar，恒开无开关） | 无条件自动装 | **不装**，仅 stderr 日志提示 `/install-overlay` | `overlay_install.py` `run_hook()` |
| 2 | SessionStart 已注入时自动补渲染桥（改 asar 内 renderer 资产） | 每次会话自动补 | **不动**，补桥仅经显式安装命令 | 同上 |
| 3 | SessionStart 异常路径注入 `additionalContext` | 注入"自检失败"文案 | 仅 stderr + `systemMessage`（UI 展示，不入上下文） | `overlay_install.py`、`hooks/session-start.py` |
| 4 | 安装失败等故障路径注入"请在回复正文转述提醒"的行为指令 | 注入 | **全链移除**：`visible_context()` 恒返回空串，`_nudge` 对空串门控 | `restart_notice.py`、`overlay_install.py` |
| 5 | UserPromptSubmit 每轮注入速率背景行（`【内部背景·勿展示】…`） | 缺省开（与 UI 统计行共用 `answer_rate_line` 键） | **彻底移除注入**（无条件空上下文）；`answer_rate_line` 回归纯 UI 开关（只控制前端"回答末尾统计行"，默认开），在 ⚙ 开统计行也不会恢复注入；提问时刻仍记录（`--current` 守卫依赖） | `hooks/prompt-submit.py` |
| 6 | 后台自动更新（SessionStart 拉起分离进程 + 客户端 loader 每 60s 轮询，每小时限频，自动安装新 Release） | 缺省开（键缺省视为 true） | **缺省关**（Python 侧与 Node 侧两处默认值都翻转），config `plugin_auto_update: true` 显式开启 | `plugin_auto_update.py`、`plugin-updater.cjs` |
| 7 | 自动压缩阈值引擎补丁（改 `<resources>/glm/zcode.cjs`，v1.5.9 新增） | 缺省开（`auto_compact_patch !== false`） | **缺省关**（`=== true`），⚙ 面板或 config 显式开启才打；同时修正了上游注释与代码不一致（上游注释写"默认关"而代码默认开） | `inject-main.cjs` |
| 8 | 卸载还原 | 剥注入行+剥桥；**引擎补丁永久残留**（备份 `.pre-acthreshold-*` 同样残留） | 新增 `restore_engine_patch()`：当前引擎字节含本插件标记时用首打备份整体还原（防客户端已官方升级时写回旧字节），随后清理备份与哨兵 | `overlay_install.py` `remove` 链 |

未改动的已披露行为（有意保留，见第三节审计）：

- **额度查询缺省开**（`quota_enabled`/`reset_badge_enabled`）：解密宿主自身加密的 OAuth 凭据，
  只发往各厂商自己的官方域名（逐适配器核对，发前有域名白名单校验）。这是状态条的显示功能本体，
  不属于上下文注入/客户端修改/自动更新，故保留缺省开；不想要可在 ⚙ 面板关闭对应开关。
- **运行时目录热同步**（SessionStart 把 17 个运行时文件同步到 `~/.zcode/zcode-token-usage-statusbar/`）：
  只写插件自身目录，不碰客户端；这是已装状态条接收更新的通道，保留。
- **渲染层热更新**（overlay.js mtime 变化 5s 内重注入，`hot_reload:false` 可关）：本地资产，保留。

## 二、配置速查（`~/.zcode/zcode-token-usage-statusbar/config.json`）

| 键 | 上游缺省 | clean 缺省 | 作用 |
|---|---|---|---|
| `answer_rate_line` | `true` | `true`（**已与上下文注入解耦**） | 只控制前端"回答末尾统计行"（纯 UI、确定性渲染）；上游此键同时控制 hook 注入速率背景行，clean 分叉的 hook 无条件零注入，本键怎么设都不影响上下文 |
| `plugin_auto_update` | `true` | **`false`** | 后台自动检查+安装新版本（GitHub/GitCode Release，SHA256 校验） |
| `auto_compact_patch` | `true` | **`false`** | 给客户端内置引擎打自动压缩阈值补丁（改 `glm/zcode.cjs`） |
| `hot_reload` | `true` | `true` | overlay.js 热更新（本地） |
| `quota_enabled` / `reset_badge_enabled` | `true` | `true` | 额度/重置卡查询（凭据只发各厂商官方域） |

安装/卸载状态条：`/install-overlay`、`/uninstall-overlay`（clean 分叉中前者是唯一安装入口，
后者会还原全部客户端修改包括引擎补丁）。旧键 `plugin_auto_install_overlay` 与环境变量
`ZUSAGE_PLUGIN_AUTO_INSTALL` 在上游 v1.5.7 起已失效，clean 分叉中无对应行为可开关。

## 三、安全审计结论（v1.5.9 全量，2026-11-01）

四个独立审计覆盖全部运行时代码（更新/加载器链、凭据与数据流向、安装/补丁链、渲染层载荷
overlay.js），**均未发现恶意代码**。要点：

- **网络出口全量枚举，无陌生主机**：GitHub/GitCode（更新渠道，含资产 CDN：
  release-assets/objects.githubusercontent.com、file-cdn.gitcode.com、api.gitcode.com）+
  各模型厂商官方端点（bigmodel/z.ai/zcode.z.ai/minimax/moonshot-kimi/deepseek/
  siliconflow/openrouter/anthropic/cline/opencode/commandcode/小米 MiMo/火山方舟，
  及 www.minimaxi.com、www.minimax.io、open.volcengineapi.com、cloud.siliconflow.cn、
  models.dev）。无遥测/分析端点。
- **凭据只发各自厂商域名**：16+ 个额度适配器逐家核对，无一例外；bigmodel/z.ai 家族
  key 有官方域校验（绝不发第三方中转）；自定义 baseUrl 强制 https（例外仅 localhost）
  + 域名白名单；API Key 只落 `quota-secrets.json`（0600、原子写），不进日志/诊断/面板。
- **除额度查询外零外发**：无遥测、无崩溃上报；会话内容、提示词、文件路径从不进入任何请求。
- **更新包完整性**：SHA256+size 校验先于一切使用（下载时、解包时、安装后核对官方安装记录，
  三层）；下载的字节本身从不被执行；渠道身份钉死在宿主已注册市场（github.com|gitcode.com 域
  白名单+重定向逐跳复验），不可被远端切换。
- **客户端修改是外科手术式**：app.asar 内主进程入口文件尾部追加 1 行 dynamic import；
  渲染桥按客户端版本 SHA256 指纹表门控（表外 fail-closed 拒绝）；引擎补丁按引擎整文件
  SHA256 白名单+锚点唯一性校验（不匹配即拒绝写盘）。
- **无持久化、无提权**：无计划任务/注册表/登录项/服务（旧 schtasks 方案已废弃删除）；
  从不自我提权，仅打印指引；收尾监控是普通进程（单实例锁、最长存活 1 天）。
- **无混淆、无后门**：`inject-main.cjs` 的 `new Function(src)` 仅对本地 overlay.js/
  turn-stats.js 做语法校验（只编译不执行）；渲染层→主进程动作全白名单；远程内容（更新日志
  正文、模型 id、额度货币串）进入 DOM 前全部转义，禁脚本禁远程图片。
- **需知情的边界行为**（非恶意，保留并披露）：
  - Anthropic 额度适配器可跨应用读 `~/.claude/.credentials.json`（默认关，⚙ 显式开启才读）；
  - `zusage.py` 远端用量功能经用户自己的 ssh/wsl/docker exec 通道在远端跑查询脚本；
  - 解密宿主 OAuth 凭据用宿主同款密钥派生（`ZCODE_CREDENTIAL_SECRET` 或机器指纹回退串），
    凭据仅驻内存；
  - 诊断文件 `diag-N.json` 含会话 id 摘要（本地，`diag:false` 可关），显式排除凭据类键。

## 四、测试状态（2026-11-02，Windows 本机）

- **Python 全量**：672 项全绿（含随分叉翻转的默认值断言；另修复了上游若干 Windows 平台性测试缺陷：`pwd` 导入、sqlite 句柄占用、mode 位断言、symlink 守卫）。
- **Node 全量**：1116 项中 1022 过；**93 个失败与未改动的原版 v1.5.9 在本机的失败清单逐字一致**（92 个共有 + 1 个已修复的分叉前提用例），全部是上游测试在 Windows 的既有环境性失败，集中在 Git chip 域（readdir 顺序/文件系统语义）与 zcode-start 额度客户端域，上游 CI（macOS）为绿。分叉**零回归**。
- **本机跑 updater 系测试的前置条件**：`python3` 必须是真实解释器。Windows 的 Microsoft Store `python3` 执行别名存根会导致跨进程锁永不就绪 → 批量失败与挂起（本次以在 miniforge env 目录复制 `python3.exe` 解决，删除该文件即回滚）。
- 分叉侧所有与"默认值/注入语义"相关的测试已翻转为新契约，auto 流程用例一律显式 `plugin_auto_update: true` 前提（被测行为不变，只显式化原来隐式依赖的默认开）。

## 五、维护

### 上游更新通知与升级流程

- **通知渠道**：在 GitHub 上游仓库 [ByteRax/zcode-token-usage-statusbar](https://github.com/ByteRax/zcode-token-usage-statusbar) 页面点 **Watch → Releases only**，上游每发正式版会收到 GitHub 通知/邮件。本分叉的插件自身不检查更新（自动更新缺省关），也不会产生任何更新提示——这是有意设计。
- **⚠ 宿主插件管理器的"有更新"是陷阱，别点**：上游发布新版本且本机市场刷新后，ZCode 插件管理器会提示"有更新"并允许一键更新——**接受它会把官方包覆盖进缓存安装目录，自动安装、上下文注入等全部入侵行为随之回归**。把这个提示仅当作"上游有新版了"的信号，升级一律走下面的分叉流程。
- **升级流程**（本地安装目录为插件管理器缓存里的版本目录，如
  `~/.zcode/cli/plugins/cache/zusage-marketplace/zcode-token-usage-statusbar/1.5.4/`）：

  ```bash
  cd <本仓库检出目录>
  git fetch upstream --tags
  git merge v1.5.x                 # 冲突按 AGENTS.md 首节原则裁剪，不按上游
  # 四套检查全绿后：
  python sync_plugin.py --check
  node --test tests/*.test.cjs --test-timeout=30000
  python -B -m unittest discover -s tests -p 'test_*.py'
  # 覆盖缓存目录（保留目录名不变，管理器登记不受影响）：
  rm -rf "<缓存版本目录>"/* && cp -r plugins/zcode-token-usage-statusbar/. "<缓存版本目录>"/
  python "<缓存版本目录>/overlay_install.py" sync    # 更新运行时目录
  # 最后：更新 installed_plugins.json 里该插件的 version 字段，完全退出 ZCode 并重开
  ```

- 上游 remote：`upstream`（ByteRax）；分叉 remote：`origin`（neoblackxt）。工作分支 `clean`。
- 合并上游新版本时：`git fetch upstream && git merge v1.5.x`，冲突集中在上述改造点文件；
  合并后重跑 `node --check inject-main.cjs plugin-updater.cjs`、
  `python -m py_compile plugins/zcode-token-usage-statusbar/*.py plugins/zcode-token-usage-statusbar/hooks/*.py`、
  `python sync_plugin.py --check`、`node --test tests/*.test.cjs`、
  `python -B -m unittest discover -s tests -p 'test_*.py'`。
- 测试套件中与"缺省开"相关的断言已随本分叉翻转为"缺省关"（见提交 diff 中 tests/ 部分）。
