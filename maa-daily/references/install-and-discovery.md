# maa-cli 安装与环境发现

## 核验信息

- 最近核验日期：2026-11-01（核对识别失败诊断与官方界面适配；首装实测仍为 2026-08-17，本次未做更新后实机复验）
- 实测环境：Windows x64，maa-cli 0.7.5，MaaCore 6.16.8
- 官方来源：[maa-cli 安装](https://docs.maa.plus/en-us/manual/cli/install.html)、[使用说明](https://docs.maa.plus/en-us/manual/cli/usage.html)、[maa-cli 仓库](https://github.com/MaaAssistantArknights/maa-cli)
- 边界：以下路径、版本、磁盘占用和现象来自一次真实 Windows 首装，是诊断线索而非永久契约。其他平台直接遵循当前官方文档。

## 先判断状态

按当前宿主提供的安全命令能力检查：

```powershell
Get-Command maa -ErrorAction SilentlyContinue
maa --version
maa version --batch
```

Windows 官方脚本截至核验日默认安装 CLI 到：

```text
%LOCALAPPDATA%\Programs\maa-cli\maa.exe
```

`maa` 不在 PATH 时检查这个位置，但不要只凭该路径不存在就断言其他安装方式不存在。安装脚本修改用户 PATH 后，已经运行的终端或 Agent 宿主可能仍看不到新命令；刷新环境、打开新终端或暂时使用已确认的绝对路径。

区分状态：

- `maa --version` 成功、`maa version --batch` 同时列出 CLI/Core：完整基础运行时。
- CLI 可运行，但 `maa dir library`、`maa dir resource` 或 Core 版本失败：通常是 MaaCore/资源尚未安装，不能报告为 CLI 缺失。
- CLI 和官方默认位置都不可用：再建议安装。

## 发现权威目录

不要自己拼配置路径。使用当前 CLI：

```powershell
maa dir config --batch
maa dir data --batch
maa dir library --batch
maa dir resource --batch
maa dir hot-update --batch
maa dir log --batch
maa dir cache --batch
```

本次 Windows 实测结果的泛化形态：

```text
config      %APPDATA%\loong\maa\config
data        %APPDATA%\loong\maa\data
library     %APPDATA%\loong\maa\data\lib
resource    %APPDATA%\loong\maa\data\resource
hot-update  %APPDATA%\loong\maa\data\MaaResource
log         %APPDATA%\loong\maa\data\debug
cache       %LOCALAPPDATA%\loong\maa\cache
```

只安装 CLI 时部分目录尚不存在，`library`/`resource` 查询可能失败。把失败保留为“部分安装”的证据。

## 按官方方式安装

真实安装是网络、磁盘和 PATH 变更，先取得用户授权。Windows 预编译安装的可审查方式：

```powershell
Invoke-WebRequest `
  -Uri "https://raw.githubusercontent.com/MaaAssistantArknights/maa-cli/main/install.ps1" `
  -OutFile "install.ps1"
Get-Content -Raw -Encoding UTF8 .\install.ps1
.\install.ps1 -Channel stable
```

运行远程脚本前确认来源、下载目标、哈希校验和 PATH 行为仍与当前官方说明一致。不要把下载和执行隐藏成无法审查的管道。

Windows 安装 MaaCore 前，官方要求 VC++ x64 Runtime。缺失时需要管理员权限：

```powershell
winget install "Microsoft.VCRedist.2015+.x64" `
  --override "/repair /passive /norestart" `
  --uninstall-previous --accept-package-agreements --force
```

随后安装稳定版 MaaCore 与资源：

```powershell
maa install stable --batch --test-time 0
```

`--test-time 0` 跳过镜像测速，是实测可用选择，不是所有环境的强制参数。

## 真实首装观察

- maa-cli 0.7.5 的安装包约 8 MB。
- MaaCore 6.16.8 完成后 data 约 632 MB，下载 cache 约 268 MB；未来版本会变化。
- `maa install` 下载约 268 MB 时可能长时间没有控制台进度，但 `.partial` 文件仍增长。
- 同一安装重试在实测中复用了 partial；不要因此承诺所有版本都支持可靠断点续传。
- zip 完成后还会 clone/update MaaResource，不能只凭下载完成判断整个命令结束。

让安装命令保持 pending，依赖宿主的超时、取消和低频进程观察。不要每秒触发 Agent/LLM 轮询，不要因为暂时零输出并发启动第二个安装。

## 运行前热更新与代理

maa-cli 0.7.5 实测在 `maa run`（包括 `--dry-run`）进入 task 解析前可能访问 `api.maa.plus` 更新 hot-update 资源。本机代理提前断开连接时，命令会报告 `Network error: unexpected end of file`，此时不能据此断言 task/profile 有错。

使用 `-v` 或 `-vv` 分层观察：如果日志停在 `Updating hot update files` 和 HTTP 请求阶段，先处理网络路径；如果已经打印 task summary 或 MaaCore 装配错误，再按配置问题处理。需要绕过有问题的代理时，只为当前 maa-cli 子进程临时移除代理环境变量，并取得与网络变化相称的用户同意；不要静默修改系统或持久代理配置。

## 识别失败后的版本与修复核查

本节拥有已进入 MaaCore 的识别/导航故障诊断路径，不是每天必做的版本检查，也不改变 runner 的失败停止条件。连接和截图正常但原生入口失败时使用；多个入口同时失败，或当前 OCR 已读到目标页面特征但原生任务仍无法通过时，应优先核查界面适配，不把“回首页再试”作为默认补救。

1. **定位失败边界。** 确认原进程结束，保留已完成业务和原报告；从本次日志区间找最后成功动作、首个失败节点及后续状态。尚未进入 MaaCore 的更新网络错误、截图/触控建立失败、登录失效分别走对应路径，不混为页面识别失败。模板名出现不等于匹配失败，入口点击成功不等于到达目标页。已有 OCR 能反驳“完全不在该页”的猜测，但不能替代账号、遮挡/弹窗及业务前置核验；需补充当前页面证据时，仍遵循[无点击诊断边界](safety-and-results.md#客户端更新检查)，不要求 Agent 具备看屏幕能力。
2. **核对实际版本和资源。** 使用本次同一个 maa 可执行文件核对 CLI/Core 版本，按上文目录发现结果与本轮加载日志确认基础、热更新、profile 用户资源及组件隔离资源。检查缺失、忽略或加载失败提示，不仅检查目录是否存在。`Hot update completed successfully` 不能证明 MaaCore、基础资源或实际加载的资源已更新；缺目录告警也不能单独证明它就是根因。不要把 Skill 的实测版本当成永久目标版本。
3. **查对应的官方适配。** 对照[官方 Release](https://github.com/MaaAssistantArknights/MaaAssistantArknights/releases)和相关节点修复，检查近期游戏界面、主题或控件变化是否已获适配；区分官方已确认的问题、与本轮吻合的推断和仍缺的证据。不能仅因版本旧就定案，也不规定固定最低版本或失败即升级。
4. **检查自定义覆盖。** 用户资源、Skill 检查组件可能固定了旧模板、ROI、继承节点或布局。若对应失败路径有覆盖，对照部署内容与当前官方资源，区分更新 Skill、`prepare` 部署资源和更新 MaaCore 的职责；捆绑派生节点按[模板绑定](custom-tasks.md#派生模板绑定与升级)核对并适配，不临时猜图片名。部署一致不代表适配新界面。不要删除全部用户资源、静默改节点或降低阈值来放行。修复涉及哪一层，就在既有授权内处理哪一层。
5. **修复后只做有界复验。** 找到对应修复时，按当前官方流程更新相关正式组件及配套资源；先保留可恢复配置，按[实际影响授权](safety-and-results.md#按实际影响授权)判断是否已获更新和游戏操作授权，足够时直接继续。修复完成、无并发且环境/账号前置仍可信后，先核对新加载版本和资源，再做一次针对原失败入口的最小复验；可用已验证的只导航/识别入口时优先使用，不能为验证商店入口整轮重跑购买。复验仍失败就停止，保留前后版本、资源路径、节点、当前页面证据和新旧报告；没有新的可验证修复条件时，不原样重试或要求用户再次回首页、重启。官方修复记录或更新命令成功都不等于本机已恢复。

恢复后按[日常恢复边界](daily-execution.md#汇报与恢复)核对游戏日和已完成项，只补未完成且获授权的业务；保留本次临时“不用药”等限制，读回补跑入口的实际参数，不因正式配置仍允许用药而恢复旧策略。跨游戏日或当前账号证据失效时重新对账，不拼接成同一轮完成。检查组件受游戏更新影响时独立核验，不能以 startup 成功推断领奖、奖励识别等后续环节全部兼容。

2026-09-05 的一次 Depot 运行使用 MaaCore 6.16.8：主题入口匹配并点击成功，后续仓库页签识别失败；[官方 v6.17.1 发布说明](https://github.com/MaaAssistantArknights/MaaAssistantArknights/releases/tag/v6.17.1) 已记录游戏新增页签后的对应识别修复。这支持先核查版本与修复，而非仅凭主题模板名断言未适配；该次失败证据不包含更新后的成功复验，也不构成固定最低版本要求。

## Windows 日志文件

官方 CLI 支持裸 `--log-file` 或 `--log-file=<path>`。maa-cli 0.7.5 在 Windows 的一次实测中，裸 `--log-file` 因自动文件名不符合 Windows 路径规则而在任务开始前报 `os error 123`。需要 CLI 文件日志时，显式给出不含冒号的文件名，例如：

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
maa run maa-daily --batch --profile default --log-file="maa-$stamp.log"
```

这是一条版本化兼容提示，不代表后续版本仍存在该问题。即使指定了 CLI 日志文件，MaaCore 的识别与任务链细节通常仍应从 `maa dir log --batch` 指向的 `asst.log` 核对；终端 summary、CLI 日志和 MaaCore 日志承担的证据范围可能不同。

## 初始化 profile

MaaCore 完整安装后，可以按用户选择初始化：

```powershell
maa init --batch --format toml
```

实测生成 `<config>/profiles/default.toml`，包含通用 ADB/MaaTouch 默认值，但不会自动写入当前 MuMu ADB 路径或实例端口。先读取生成结果，再决定是否需要设备相关修改。
