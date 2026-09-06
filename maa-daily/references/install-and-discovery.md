# maa-cli 安装与环境发现

## 核验信息

- 最近核验日期：2026-09-06（补充识别失败后的版本与修复核查；首装实测仍为 2026-08-17）
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

已经连接设备，但识别或导航持续失败时，先从本次日志定位最后成功动作与首个失败节点。结合匹配结果、实际动作和后续状态判断；模板名出现在日志中不代表它匹配失败，入口点击成功也不能代替进入目标页面的证据。

核对本次实际加载的 MaaCore 版本、基础资源与热更新资源路径，再对照官方发布说明或修复代码，查找与失败节点对应的已知问题。maa-cli、MaaCore 与资源分别核验；`Hot update completed successfully` 不能证明 MaaCore 和全部基础资源已经更新，也不能把 Skill 的实测版本当成应永久保留的版本。

发现本地尚未包含的对应修复时，优先考虑按当前官方流程更新相关组件，再复验原来失败的任务。更新与真实复验按[实际影响授权](safety-and-results.md#按实际影响授权)，已有授权足够时直接继续。更新后核对新日志实际加载的版本、资源与业务结果；官方已修复或更新命令成功，都不能单独证明本机问题已经解决。没有对应修复证据时继续定位，不把所有识别失败都变成强制升级。

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
