# Windows MuMu 与 ADB

## 核验信息

- 最近核验日期：2026-08-17
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
- 启动成功至少区分三层证据：实例/虚拟机进程、ADB/监听端口、用户预期的窗口可见性。任务只需要 ADB 时前两层可能足够，但不能据此宣称“可见窗口已打开”。

2026-08-17 的明日方舟专版 MuMu 12 实测中，隐藏启动遗留了一个虚拟机和 ADB 均正常、但系统应用控制无法枚举窗口的实例。再次点击快捷方式只向旧实例发送 wake 消息，没有创建可见窗口。对 0 号实例执行下列管理器命令后，它能有序退出；随后用普通桌面应用启动方式重新打开，窗口恢复可见：

```powershell
& '<MuMuRoot>\shell\MuMuManager.exe' api -v 0 shutdown_player
```

`0` 是本次实测的实例编号，不是通用默认值。执行前从多开器、进程、端口或当前 MuMu 资料确认精确实例；关闭实例会终止其中运行的游戏，必须被当前用户请求覆盖。不要在能够使用管理器有序关闭时直接强杀虚拟机进程。

Agent 自己启动了 MuMu 时，在运行前确认用户希望任务结束后：

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
