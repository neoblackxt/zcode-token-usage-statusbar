# clean 分叉：去入侵化改造说明

本分叉基于上游 [ByteRax/zcode-token-usage-statusbar](https://github.com/ByteRax/zcode-token-usage-statusbar) **v1.5.14**（tag `v1.5.14`，commit `4a8b4e1`），分支 `clean`——升级方式为 **rebase**（分叉提交线性重放在上游 tag 之上，不产生 merge 提交）。清单版本号保持 `1.5.14`（release_tool 强制严格 SEMVER，且与上游同版本可原地替换、不触发插件管理器升级提示）；分叉身份由 `clean` 分支、README 横幅与本文件承载。

目标：**完整保留状态条/用量统计/额度显示等功能，消灭一切未经显式同意的"自动行为"**——
不向模型上下文注入任何文字、不自动修改客户端文件、不自动联网检查更新、不自动打引擎补丁。
功能本身都还在，只是全部改为**显式开启**。

本目标已固化为仓库最高规范：[AGENTS.md](AGENTS.md) 首节「分叉最高原则：上下文零入侵」——
对本仓库工作的任何人（含 AI 助手）具有约束力，与上游合并冲突时按该原则裁剪。

## 一、改造清单（相对上游 v1.5.14）

| # | 行为 | 上游 v1.5.14 | clean 分叉 | 实现位置 |
|---|---|---|---|---|
| 1 | SessionStart 未注入时自动安装（改 app.asar，恒开无开关） | 无条件自动装 | **不装**，仅 stderr 日志提示 `/install-overlay` | `overlay_install.py` `run_hook()` |
| 2 | SessionStart 已注入时自动补渲染桥（改 asar 内 renderer 资产） | 每次会话自动补 | **不动**，补桥仅经显式安装命令 | 同上 |
| 3 | SessionStart 异常路径注入 `additionalContext` | 注入"自检失败"文案 | 仅 stderr + `systemMessage`（UI 展示，不入上下文） | `overlay_install.py`、`hooks/session-start.py` |
| 4 | 安装失败等故障路径注入"请在回复正文转述提醒"的行为指令 | 注入 | **全链移除**：`visible_context()` 恒返回空串，`_nudge` 对空串门控 | `restart_notice.py`、`overlay_install.py` |
| 5 | UserPromptSubmit 每轮注入速率背景行（`【内部背景·勿展示】…`） | 缺省开（与 UI 统计行共用 `answer_rate_line` 键） | **彻底移除注入**（无条件空上下文）；`answer_rate_line` 回归纯 UI 开关（只控制前端"回答末尾统计行"，默认开），在 ⚙ 开统计行也不会恢复注入；提问时刻仍记录（`--current` 守卫依赖） | `hooks/prompt-submit.py` |
| 6 | 后台自动更新（SessionStart 拉起分离进程 + 客户端 loader 每 60s 轮询，每小时限频，自动安装新 Release） | 缺省开（键缺省视为 true） | **缺省关**（Python 侧与 Node 侧两处默认值都翻转），config `plugin_auto_update: true` 显式开启 | `plugin_auto_update.py`、`plugin-updater.cjs` |
| 7 | 自动压缩阈值引擎补丁（改 `<resources>/glm/zcode.cjs`，v1.5.9 新增） | 缺省开（`auto_compact_patch !== false`） | **缺省关**（`=== true`），⚙ 面板或 config 显式开启才打；同时修正了上游注释与代码不一致（上游注释写"默认关"而代码默认开） | `inject-main.cjs` |
| 8 | 卸载还原 | 剥注入行+剥桥；**引擎补丁永久残留**（备份 `.pre-acthreshold-*` 同样残留） | 新增 `restore_engine_patch()`：当前引擎字节含本插件标记时用首打备份整体还原（防客户端已官方升级时写回旧字节），随后清理备份与哨兵 | `overlay_install.py` `remove` 链 |
| 9 | 费用预估联网同步（v1.5.10 新增：serve 常驻进程每 12h 拉 models.dev 价表 + USD→CNY 汇率，主源 `open.er-api.com`、兜底 `api.exchangerate.fun`） | 缺省开（`pricing.fetch` 键缺省视为 true） | **缺省关**（`is not False` → `is True`）：费用显示照常（离线用内置烘焙价表，无汇率显示 $ 原值），config `pricing.fetch: true` 才开启在线刷新；⚙ 文案同步改写 | `token_pricing.py` |
| 10 | macOS 待更新包预注入（v1.5.11 新增：已注入的 loader 监视 Squirrel pending 目录，发现未来版本更新 zip 即改写 zip 内 app.asar 补注入行/渲染桥，更新重启即生效，免二次重启） | 缺省开（`pending_zip_patch` 键缺省视为 true） | **缺省关**（两处门控 `=== false` → `=== true`，watcher 不注册）：仅 macOS 生效，config `pending_zip_patch: true` 显式开启；工具本身保留（`pending_zip_patch.py`，原子/幂等/fail-closed，绝不动 update-info.json） | `inject-main.cjs` |
| 11 | SessionStart 孤儿待收尾包自愈（v1.5.12 新增：合法 pending 且监控实例锁空闲时自动补拉静默收尾监控，issue #228） | 缺省开（`run_hook` st 分支无条件调用） | **不接线**：`_revive_orphan_pending` 函数保留（附分叉注释）但 `run_hook` 不调用——孤儿待收尾包的恢复路径是重跑 `/install-overlay`（显式链自带 pending 复用与监控拉起） | `overlay_install.py` |
| 12 | CDP 本机调试口（v1.5.13 新增：loader 顶层给宿主 appendSwitch `--remote-debugging-port=9229`，为实机调试/自动化 QA 用——插件运行时代码零消费方） | 缺省强开（仅 env `ZCODE_DISABLE_FIXED_REMOTE_DEBUGGING_PORT=1` 或用户显式端口可免） | **缺省关**（`cdp_debug_port === true` 才补开）：默认为本机全部进程打开可读控 ZCode 窗口的控制通道不可接受；env 逃生口与用户显式端口优先级保留 | `inject-main.cjs` |

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
| `pricing.fetch` | `true` | **`false`** | 费用预估在线同步（每 12h 拉 models.dev 价表与汇率；关闭时用内置价表离线计算，无汇率显示 $） |
| `pending_zip_patch` | `true` | **`false`** | macOS 待更新包预注入（loader 监视 Squirrel pending zip 并补注入行；仅 macOS，Windows 天然不适用） |
| `cdp_debug_port` | `true` | **`false`** | CDP 本机调试口 `127.0.0.1:9229`（实机调试/QA 用；插件自身功能不需要它。开启后同机本地进程可读控 ZCode 窗口；env `ZCODE_DISABLE_FIXED_REMOTE_DEBUGGING_PORT=1` 仍优先关闭） |

安装/卸载状态条：`/install-overlay`、`/uninstall-overlay`（clean 分叉中前者是唯一安装入口，
后者会还原全部客户端修改包括引擎补丁）。旧键 `plugin_auto_install_overlay` 与环境变量
`ZUSAGE_PLUGIN_AUTO_INSTALL` 在上游 v1.5.7 起已失效，clean 分叉中无对应行为可开关。

## 三、安全审计结论（v1.5.9 全量 2026-11-01 + v1.5.10~v1.5.14 增量）

四个独立审计覆盖全部运行时代码（更新/加载器链、凭据与数据流向、安装/补丁链、渲染层载荷
overlay.js），**均未发现恶意代码**；v1.5.10 增量（token_pricing 费用链、v208 更新红点、
客户端 3.17.2 双适配）与 v1.5.11/v1.5.12 增量（2026-11-05 复核：pending_zip_patch 预注入链、
WMI 脱离式监控拉起、quota 宿主路径解析、overlay 速度图/tooltip 批、安装器解释器与
提权指引修复；v1.5.12 的 SessionStart 孤儿自愈补拉、model-puller 全新环境自建规则、
Windows 探测 CREATE_NO_WINDOW、macOS Helper bundle 拉起 CLI、Busy journal 状态机）
与 v1.5.13 增量（2026-11-09 复核：overlay.js 模块化拆分 src/overlay 十四分片+零依赖
拼接门禁、usage_stats/token_rate 统计库同源提取、速度 Tooltip V2 与轮播、主进程子模块
热替换与渲染端运行时合成桥（v258 动态桥）、宿主启动 CDP 9229 强开、SessionStart
兼容性预判与 force 更新触发（issue #247）、model-puller models.dev api.json 档位目录
（约 6MB，后台下载、24h 缓存、只落轻量档位索引）、darwin LSUIElement Helper 后台拉起
（#238）、Ark Agent Plan 额度源认领（#261，只解析本地 provider 配置）、MCP CRLF 与
ESM 语法自检修复）与 v1.5.14 增量（2026-11-09 复核：tooltip  哨兵剥除 v306、
状态轮播主开关默认关（上游按 2026-11-09 用户规格自行翻转——与分叉方向一致，采纳；
显式开/关过的存量配置经 loadRotatorConfig 布尔覆盖原样保留）、速率 chip 右键直开设置；
无新配置键、无网络面、hook 零改动）结论相同：**无新增外发端点**（api.json 复用既有
models.dev 域，且仅发生在 ⚙「拉取模型」显式动作链内）、无新自动行为越过分叉红线。要点：

- **网络出口全量枚举，无陌生主机**：GitHub/GitCode（更新渠道，含资产 CDN：
  release-assets/objects.githubusercontent.com、file-cdn.gitcode.com、api.gitcode.com）+
  各模型厂商官方端点（bigmodel/z.ai/zcode.z.ai/minimax/moonshot-kimi/deepseek/
  siliconflow/openrouter/anthropic/cline/opencode/commandcode/小米 MiMo/火山方舟，
  及 www.minimaxi.com、www.minimax.io、open.volcengineapi.com、cloud.siliconflow.cn、
  models.dev）+ **v1.5.10 新增费用同步源**（`models.dev/api.json` 牌价、
  `open.er-api.com/v6/latest/USD` 汇率主源、`api.exchangerate.fun` 兜底；
  全部 GET 公共数据、不带任何凭据，且 clean 分叉缺省关）。无遥测/分析端点。
- **凭据只发各自厂商域名**：16+ 个额度适配器逐家核对，无一例外；bigmodel/z.ai 家族
  key 有官方域校验（绝不发第三方中转）；自定义 baseUrl 强制 https（例外仅 localhost）
  + 域名白名单；API Key 只落 `quota-secrets.json`（0600、原子写），不进日志/诊断/面板。
- **除额度查询外零外发**：无遥测、无崩溃上报；会话内容、提示词、文件路径从不进入任何请求。
- **更新包完整性**：SHA256+size 校验先于一切使用（下载时、解包时、安装后核对官方安装记录，
  三层）；下载的字节本身从不被执行；渠道身份钉死在宿主已注册市场（github.com|gitcode.com 域
  白名单+重定向逐跳复验），不可被远端切换。
- **客户端修改是外科手术式**：app.asar 内主进程入口文件尾部追加 1 行 dynamic import；
  v1.5.11 的 macOS pending zip 预注入同纪律（zip 内 asar 条目唯一匹配才动、原子替换、
  stat 锚点复核、update-info.json 绝不改动、失败保留原包），但 clean 分叉缺省关；
  WMI 拉起只用于本插件自己的收尾监控进程（父进程 WmiPrvSE、本地、`ZUSAGE_MONITOR_NO_WMI` 可禁），非逃逸手段；
  v1.5.12 的 model-puller 自建规则只写本地 provider 配置文件（⚙「拉取模型」显式动作、
  结构缺口 fail-closed），macOS Helper 只换本插件检查更新的 CLI 启动路径；
  渲染桥按客户端版本 SHA256 指纹表门控（表外 fail-closed 拒绝）；引擎补丁按引擎整文件
  SHA256 白名单+锚点唯一性校验（不匹配即拒绝写盘）；v1.5.13 的 CDP 9229 调试口属
  宿主运行时行为（仅回环监听、不改文件），但为同机进程可读控窗口的控制通道——
  clean 分叉缺省关（改造表 #12），显式开启者自担该调试面。
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

## 四、测试状态（2026-11-09，Windows 本机，v1.5.14 基线）

- **Python 全量**：839 项中 0 失败（32 skip 为平台性跳过；v1.5.10 时 714 → v1.5.11 752 → v1.5.12 776 → v1.5.13/14 839，增量均为上游新用例：兼容性预判/桥 quiet_hook/项目目录归一化/speed history/ui_element 等，其中上游 8 个 compat-nudge 与桥状态用例随分叉契约重写——提示链在分叉不存在，断言改为「零注入 + bridge 不被调用 + force 信号保留」）。分叉恒比原版绿：原版 v1.5.13/v1.5.14 在本机分别 2F+6E / 3F+6E（sqlite 临时目录占用 ×2、sudo 加载器、mode 位断言、渲染桥 ×2、损坏库 CLI 等 Windows 环境性失败），分叉侧因既有 Windows 修复（显式 close、pwd 导入等）与新用例的同类修法全部通过。
- **Node 全量**：1441 项中 101 失败，**与未改动的原版 v1.5.13/v1.5.14 在本机的失败清单均逐字一致**（分叉独有失败 = 原版独有失败 = 0；v1.5.12 基线时为 100，+1 为上游新增测试的环境性漂移；上游 CI（macOS）为绿），全部是上游测试在 Windows 的既有环境性失败（Git chip 域的 readdir 顺序/文件系统语义、zcode-start 额度客户端域、inject-main-lifecycle 的宿主形态桩等）。分叉**零回归**。分叉新增契约测试全绿：CDP 守卫 6 用例（缺省关矩阵）、引擎补丁源码契约、通道 compat 预判 2 用例、upgrade compat 4 用例重写。
- **本机跑 updater 系测试的前置条件**：`python3` 必须是真实解释器。Windows 的 Microsoft Store `python3` 执行别名存根会导致跨进程锁永不就绪 → 批量失败与挂起（此前以在 miniforge env 目录复制 `python3.exe` 解决，删除该文件即回滚）。
- 分叉侧所有与"默认值/注入语义"相关的测试已翻转为新契约，auto 流程用例一律显式 `plugin_auto_update: true` / `pending_zip_patch: true` 前提（被测行为不变，只显式化原来隐式依赖的默认开）。教训沉淀：**文档类改动也要跑相关契约测试**——v1.5.10 的 f672dcc 改了 install-overlay.md 却未跑 `test_restart_notice`，漏掉一处 assertIn 横幅断言，v1.5.11 全量跑才暴露（本次已反转补齐）。

## 五、维护

### 上游更新通知与升级流程

- **通知渠道**：在 GitHub 上游仓库 [ByteRax/zcode-token-usage-statusbar](https://github.com/ByteRax/zcode-token-usage-statusbar) 页面点 **Watch → Releases only**，上游每发正式版会收到 GitHub 通知/邮件。本分叉的插件自身不检查更新（自动更新缺省关），也不会产生任何更新提示——这是有意设计。
- **⚠ 宿主插件管理器的"有更新"是陷阱，别点**：上游发布新版本且本机市场刷新后，ZCode 插件管理器会提示"有更新"并允许一键更新——**接受它会把官方包覆盖进缓存安装目录，自动安装、上下文注入等全部入侵行为随之回归**。把这个提示仅当作"上游有新版了"的信号，升级一律走下面的分叉流程。
- **升级流程**（本地安装目录为插件管理器缓存里的版本目录，如
  `~/.zcode/cli/plugins/cache/zusage-marketplace/zcode-token-usage-statusbar/1.5.4/`）：

  ```bash
  cd <本仓库检出目录>
  git fetch upstream --tags
  git branch clean-pre-vX.Y.Z clean        # 留本地备份 ref
  git rebase vX.Y.Z clean                  # 冲突按 AGENTS.md 首节原则裁剪，不按上游
  # 版本号对齐（3 份 manifest 的 version 字段与上游同号）；四套检查全绿后：
  python sync_plugin.py --check
  node --test tests/*.test.cjs --test-timeout=30000
  python -B -m unittest discover -s tests -p 'test_*.py'
  # 发布（rebase 改写历史，推送一律 force-with-lease）：
  git push --force-with-lease origin clean clean:plugin-releases
  git tag -fa vX.Y.Z -m "发布说明……" clean && git push -f origin vX.Y.Z   # 触发 release CI
  # 覆盖缓存目录（保留目录名不变，管理器登记不受影响）：
  rm -rf "<缓存版本目录>"/* && cp -r plugins/zcode-token-usage-statusbar/. "<缓存版本目录>"/
  python "<缓存版本目录>/overlay_install.py" sync    # 更新运行时目录
  # 最后：更新 installed_plugins.json 里该插件的 version 字段，完全退出 ZCode 并重开
  ```

- 上游 remote：`upstream`（ByteRax）；分叉 remote：`origin`（neoblackxt）。工作分支 `clean`，**升级一律 rebase 到上游 tag**（分叉提交线性重放，无 merge 提交；rebase 后历史改写，推送必须 `--force-with-lease`）。
- **本机市场注册已指向分叉**：`~/.zcode/cli/plugins/known_marketplaces.json` 中 `zusage-marketplace` 的 `source.url` 改为 `https://github.com/neoblackxt/zcode-token-usage-statusbar.git`（ref 仍为 `plugin-releases`；原值备份在同目录 `known_marketplaces.json.bak-*`）。分叉的 `plugin-releases` 分支必须与 `clean` 同步（宿主市场与插件更新器都从它拉取）：**每次升级后执行 `git push --force-with-lease origin clean clean:plugin-releases`**。这样 ⚙ 面板与宿主插件管理器的"更新"永远拉分叉的 clean 代码，不会误更新到上游官方包。
- **上游引用处置记录**（2026-11-02 扫描全仓；2026-11-03 随 v1.5.10 rebase 复核；
  2026-11-09 随 v1.5.13 rebase 复核——上游重写的更新来源行/更新日志链接再次引入的
  `gitcode.com` 字串仍全部是「来源显示/URL 解析/跳转校验」，无一处安装引导口令，
  分叉用户的市场恒为 GitHub 渠道，GitCode 标签不会出现）：
  - 已转分叉：README×3 的安装口令/Releases 链接/Star History（并声明 clean 分叉无 GitCode 镜像）；`overlay.js` 反馈按钮 issues URL 与"清单回退失败"提示口令（原提示会引导安装上游 GitCode 官方包——已移除该向量）；`marketplace.json` icon CDN（@clean 分支，`tests/test_plugin_contract.py` 断言同步）；`tests/overlay-settings.test.cjs` 的 ISSUES_URL 断言。
  - **有意保留上游引用**：`plugin-release.cjs` 的 `NOTES_GITCODE_MIRROR`（仅更新日志正文的只读回退源，分叉 git 渠道不触发）；`.zcode/skills/`、`docs/`、AGENTS.md、本文件中的上游链接（审计溯源与上游维护流程文档）；`installed_plugins.json` 安装记录里的 `source.url`（历史溯源，非活动指针，宿主下次从分叉市场更新时会自然重写）；测试中的 `tanranran/...` 字符串（通用链接构造器的夹具数据）。
- 合并上游新版本时：`git fetch upstream && git rebase v1.5.x clean`，冲突集中在上述改造点文件；
  rebase 后重跑 `node --check inject-main.cjs plugin-updater.cjs`、
  `python -m py_compile plugins/zcode-token-usage-statusbar/*.py plugins/zcode-token-usage-statusbar/hooks/*.py`、
  `python sync_plugin.py --check`、`node --test tests/*.test.cjs`、
  `python -B -m unittest discover -s tests -p 'test_*.py'`。
  v1.5.10 rebase 实录：冲突 5 处（两份 overlay.js 的 ⚙ 更新分区文案、两份 manifest、
  install-overlay.md），全部按"分叉语义 + 上游新事实"合成；另翻转 `pricing.fetch` 缺省（改造表 #9）、
  修正 ⚙ 自动压缩阈值行的"默认开启"陈旧文案（v1.5.9 起就该是"缺省关"）。
  v1.5.11 rebase 实录（2026-11-05）：冲突 5 处（两份 inject-main.cjs 的引擎补丁门控、
  两份 manifest、overlay_install.py 的 run_hook 自动安装链、test_session_start_channels、
  config.example.json），全部按分叉语义解决；上游对被删自动安装链的改进（关窗≠退出文案、
  PYEXE 真实解释器）中，PYEXE 因在保留代码内自动并入，文案知识由 install-overlay.md 承载；
  另翻转 `pending_zip_patch` 缺省（改造表 #10，仅 macOS 生效但原则不豁免）。
  v1.5.12 rebase 实录（2026-11-05）：四个分叉提交撞冲突——a976d97（两 manifest+
  overlay_install run_hook）、redirect（两份 overlay.js 的 GitCode 渠道引导行：分叉改写
  +上游 v244 relaunchSent 新行合成）、v1.5.10 跟进（两份 overlay.js 的 ⚙ cost 行：分叉
  文案+上游 zu-tip-click 新行合成）、v1.5.11 跟进（两 manifest 的 version 单行）；
  其余提交零冲突。v1.5.12 新面处置：`_revive_orphan_pending` 保留不接线（改造表 #11，
  上游 6 个自愈断言测试反转为分叉契约）；CREATE_NO_WINDOW/Helper bundle/Busy 状态机/
  model-puller 自建规则全部采纳。
  v1.5.13 rebase 实录（2026-11-09）：仅初始 clean 提交撞冲突 10 文件（AGENTS.md 核心节、
  两份 inject-main.cjs 引擎补丁段、两 manifest、overlay_install.py 的 `_nudge`/run_hook/
  plugin_auto_update、test_bridge_pending、test_project_usage），全部按"分叉语义 + 上游
  新事实"合成：`_nudge` 并入上游 label/detail 参数、run_hook 收编上游兼容性预判
  （`unregistered`→`start_auto_update(force=…)`，force 只对显式开启者加速，不旁路缺省关）、
  `_notify_compat_once` 保留不接线（走 `_nudge` 注入，分叉红线）、`_revive_orphan_pending`
  保留上游委托形态但不接线、test_project_usage 双向合并（上游 issue #248 缓存键含目录 +
  碰撞测试类保留，分叉 Windows sqlite 显式关闭修法套用到上游新用例）。**overlay.js
  模块化后分叉 overlay 改动的落点改变**：⚙ 文案/渠道引导/issuesUrl 三类编辑必须改
  `src/overlay/06|09` 分片再 `python3 scripts/build_overlay.py && python3 sync_plugin.py`
  重建产物（`--check` 逐字节门禁），rebase 中三个历史提交的分片镜像已分别 amend 回
  原提交。另翻转 CDP 9229 缺省（改造表 #12，上游 `tests/inject-cdp-guard.test.cjs`
  六分支重写为分叉契约）并补回 rebase 挤掉的 `tests/inject-autocompact.test.cjs`
  `source` 定义。
  v1.5.14 rebase 实录（2026-11-09）：11 个分叉提交重放，仅初始 clean 提交撞两份
  manifest 冲突（分叉侧），其余零冲突；上游 v306 台账升号/轮播主开关默认关/chip
  右键开设置全部自动并入（分片 06 的分叉文案编辑区与上游改动区不相邻，门禁
  `--check` 与 `node --check` 双绿确认）。**教训（老坑复发，正式入册）：rebase 基线
  一律用上游 commit 号（如 371091a），不用本地 tag**——发布时 `git tag -fa vX.Y.Z`
  会把本地 tag 重指到分叉提交，之后 `git rebase --onto 新tag 旧tag clean` 的范围
  就是空集，clean 会被**静默**置成纯上游（本轮 `v1.5.13..clean` 为空即此坑，
  靠 `git log` 检出发现，从 clean-pre-v1514 备份恢复后用 371091a 重做）。
- 测试套件中与"缺省开"相关的断言已随本分叉翻转为"缺省关"（见提交 diff 中 tests/ 部分）。
- 教训（v1.5.13 rebase 沉淀）：`scripts/build_overlay.py --check` 只校验产物与分片
  **逐字节一致**，不做 JS 语法检查——分片被改出语法错误时门禁照样绿。改分片后必须
  `node --check overlay.js`（本轮曾因字符串里真实换行吃进 279 个级联测试失败，教训
  是：改动 overlay 相关面后先跑 node --check 再跑套件）。
