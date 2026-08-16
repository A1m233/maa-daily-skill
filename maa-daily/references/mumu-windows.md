# Windows MuMu 与 ADB

## 核验信息

- 最近核验日期：2026-08-16
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
