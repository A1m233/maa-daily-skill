# Windows MuMu 与 ADB

## 核验信息

- 最近核验日期：2026-08-21
- 实测环境：明日方舟专版 MuMu 12，安装根 `C:\Program Files\YXArkNights-12.0`
- 实测 ADB：`shell\adb.exe`，Android Debug Bridge 34.0.1
- 官方来源：[MAA 连接设置](https://docs.maa.plus/zh-cn/manual/connection.html)、[Windows 模拟器支持](https://docs.maa.plus/en-us/manual/device/windows.html)、[maa-cli 配置](https://docs.maa.plus/en-us/manual/cli/config.html)
- 边界：安装目录、进程名、ADB 版本和端口会随发行版、地区版、多开实例和更新变化。以下信息用于发现，不用于硬选目标。

## 不要把 PATH 当成安装清单

`Get-Command adb` 失败不代表系统没有 ADB。MuMu 通常自带 ADB。先从运行进程、卸载信息、快捷方式或常见安装根发现 MuMu，再在根目录下寻找 `adb.exe`。

本次实测形态：

```text
C:\Program Files\YXArkNights-12.0\
├── shell\MuMuPlayer.exe
├── shell\MuMuManager.exe
└── shell\adb.exe
```

其他官方文档列出的 MuMu 根名称还包括 `MuMu Player` 和 `MuMuPlayerGlobal-12.0`。不要只检查一个固定目录。

找到候选后先做不连接设备的版本检查：

```powershell
& '<MuMuRoot>\shell\adb.exe' version
```

`adb devices` 可能启动 ADB server；只在用户要配置或连接设备、且该影响已包含在当前请求时执行。

## 可见启动与生命周期

MuMu 是用户会直接操作的桌面应用，不要把它当作普通后台 helper。Agent 需要启动模拟器时遵循：

- 默认使用普通可见启动，不向进程传递隐藏窗口状态；窗口可以被其他应用遮挡，但应存在可供用户恢复和操作的顶层窗口。
- 只有用户明确选择后台、隐藏或无界面运行时才隐藏窗口，并提前说明当前 MuMu 版本是否真正支持该模式。
- `MuMuVMMHeadless.exe` 是正常架构中的虚拟机后端；它存在不代表用户选择了 headless 模式，也不能代替对 MuMu shell 窗口的检查。
- 启动成功至少区分四层证据：桌面 shell/窗口、管理器报告的 Android 与启动错误状态、虚拟机后端、ADB/监听端口。默认或既有偏好是前台可见的真实日常中，四层都要与用户选择和当前实例一致；不能因为窗口已出现或某一层可用就跳过其余门禁。

真实执行前检查桌面 shell 进程及其可恢复顶层窗口。Windows 上可以把 `MuMuPlayer.exe` 与非零 `MainWindowHandle` 作为当前实测信号；其他发行版按其等价 shell 与窗口证据判断。只有 `MuMuPlayerService.exe`、`MuMuVMMHeadless.exe`、`MuMuVMMSVC.exe` 和在线 ADB 时，应判为“后端实例运行但前台壳缺失”，而不是“模拟器已经正常打开”。先暂停日常；需要有序关闭现有实例再恢复窗口时，说明影响并取得授权。

反过来也一样：只有 `MuMuPlayer.exe` 和非零窗口句柄时，不能证明 Android 已经启动。2026-08-21 的一次实测中，管理器已创建可见窗口并报告 `player_state = "start_finished"`，但同时仍为 `is_android_started = false`、`launch_err_code != 0`，没有 `MuMuVMMHeadless.exe`、ADB 监听或 device。VBox 日志进一步记录 Windows error 1455（页面文件太小）和 `VERR_UNRESOLVED_ERROR`。此状态应分类为“前台壳已打开，但虚拟机启动失败”，不得更新 profile 后强行运行 MAA。

遇到该形态时，先读取当前管理器状态和本次 VBox/MuMu 日志，并用系统错误文本、可用物理/虚拟内存与页面文件状态区分资源不足和其他启动故障。若失败实例是 Agent 在当前已授权工作流中刚启动、游戏实际未运行，可以有序关闭该失败实例后做一次有限重试；重试前后都重新过四层门禁。同一错误复现时停止，不自动调整系统页面文件，也不擅自关闭用户的其他程序来释放内存。

2026-08-19 的明日方舟专版 MuMu 12 实测中，隐藏启动遗留了一个虚拟机和 ADB 均正常、但系统应用控制无法枚举窗口的实例。再次点击快捷方式只向旧实例发送 wake 消息，没有创建可见窗口。当前管理器先尝试有序关闭已确认的 0 号实例，再用管理器普通启动，能够恢复 `MuMuPlayer.exe` 的非零顶层窗口：

```powershell
& '<MuMuRoot>\shell\MuMuManager.exe' control -v 0 shutdown
& '<MuMuRoot>\shell\MuMuManager.exe' control -v 0 launch
```

管理器命令会随版本变化；旧版资料可能使用 `api -v 0 shutdown_player`。先读取当前 `MuMuManager.exe` 帮助并核对返回码，不混用版本语法。`show_window` 返回错误不能单独证明实例无法恢复；本次实测中 `launch` 最终创建了可见 shell。`0` 是本次实测的实例编号，不是通用默认值。执行前从多开器、进程、端口或当前 MuMu 资料确认精确实例；关闭实例会终止其中运行的游戏，必须被当前用户请求覆盖。不要在能够使用管理器有序关闭时直接强杀虚拟机进程。

Agent 启动 MuMu，或复用一个已经存在的 MuMu 后端实例时，在运行前确认用户希望任务结束后：

- 保持普通可见窗口运行；
- 后台运行并保留可恢复入口；
- 或正常关闭实例。

用户没有指定时，默认保持普通可见窗口，并在结果中说明仍在运行。不得留下“无可见窗口、再次启动又被旧实例拦截”的状态。

## 发现实例与端口

只读问题不要启动 MuMu。真实执行需要一个明确运行实例：

1. 查看 MuMu 主窗口或多开器显示的当前实例和 ADB 端口。
2. 必要时使用已确认的 MuMu ADB 执行 `adb devices`。
3. 将 device 状态、运行进程和监听端口交叉确认。
4. 多个 device/实例存在时让用户选择，不默认使用第一项。

官方列出的 MuMu 常见候选端口包括：

```text
127.0.0.1:16384
127.0.0.1:16416
127.0.0.1:16448
127.0.0.1:16480
127.0.0.1:16512
127.0.0.1:16544
127.0.0.1:16576
```

这些端口只是诊断线索。多开、网络桥接和新版 MuMu 可能不同，以当前实例证据为准。

可见恢复也可能改变同一编号实例的 ADB 端口。2026-08-19 实测中，恢复前 profile 与旧后端使用 `127.0.0.1:16384`；可见 `launch` 后，当前管理器 `info -v 0` 报告新实例端口为 `16385`，旧地址离线，新地址才是唯一 `device`。2026-08-21 再次从完全关闭状态启动同一编号实例时，端口又从 `16385` 返回 `16384`。这说明端口变化可以双向发生，不是一次性迁移。因此每次关闭、重启或恢复实例后：

1. 用当前版本管理器的实例信息读取该已确认实例的运行状态和 ADB 端口；
2. 只连接管理器报告的精确地址，并以 `adb devices`/`get-state` 复核；
3. 备份后更新目标 maa-cli profile，再重新过窗口、ADB 和进程隔离门。

不要因为实例编号没变就假设 ADB 端口不变，也不要让仍在监听的旧后端地址覆盖当前管理器证据。

MuMu 已运行且端口已监听时，`adb devices` 仍可能暂时没有设备条目。2026-08-17 的明日方舟专版 MuMu 12 实测中，确认当前实例拥有 `127.0.0.1:16384` 监听端口后，对该已确认地址执行一次：

```powershell
& '<MuMuRoot>\shell\adb.exe' connect 127.0.0.1:16384
```

随后设备正常注册。空的 `adb devices` 不能单独证明模拟器未启动；先交叉核对窗口/实例、进程和监听端口，再只连接已确认地址。不要把这条经验变成对常见端口的盲扫。

## 原生 profile 示例

确认真实路径和端口后，在用户选择的 maa-cli profile 中表达连接：

```toml
[connection]
adb_path = 'C:\Program Files\YXArkNights-12.0\shell\adb.exe'
address = "127.0.0.1:16384"
config = "General"

[instance_options]
touch_mode = "MaaTouch"
deployment_with_pause = false
adb_lite_enabled = false
kill_adb_on_exit = false
```

这是示例，不要原样覆盖已有 profile：

- `adb_path` 和 `address` 必须来自当前机器。
- `config`、触控和增强截图选项根据当前官方文档、MuMu 版本和实际表现选择。
- `MuMuPro` 是 maa-cli 的特定 preset，不要仅因用户使用普通 MuMu 12 就自动套用。
- profile 已有其他资源或实例选项时保留无关配置。

## 连接失败时

区分证据：

- ADB 可执行文件不存在或不能运行；
- ADB server 启动失败；
- device 为 `offline`/`unauthorized`；
- 端口没有对应运行实例；
- MaaCore profile 解析失败；
- 连接成功但截图、识别或触控失败。

不要对一组常见端口连续盲试真实操作。可以先做有限、只读的设备发现；目标仍不明确时向用户报告候选与证据。

不同 ADB 版本可能互相重启 server。发现系统中已有其他 ADB 工具时，不擅自结束进程或强制替换 ADB；说明冲突后让用户决定。
