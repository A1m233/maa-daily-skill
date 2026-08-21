# maa-daily-skill

一个指导 Agent 使用 [MaaAssistantArknights](https://github.com/MaaAssistantArknights/MaaAssistantArknights) 的 `maa-cli` 配置并运行一次个性化明日方舟日常的 Skill，支持单账号，也支持在一个已确认设备上按固定顺序为多个已登录账号复用同一套 task。

它不捆绑 MAA、maa-cli、MaaCore、ADB 或模拟器，也不提供定时和无人值守能力。使用者保留自己的 profile、task、设备信息和运行日志。

## 安装

把仓库中的 [`maa-daily`](./maa-daily) 目录复制到你的 Agent 所使用的 skills 目录，或者使用宿主支持的标准 Skill 安装方式从本仓库安装 `maa-daily`。

安装后的目录应保持：

```text
<skills-root>/maa-daily/
├── SKILL.md
├── agents/openai.yaml
├── references/
└── assets/
```

不同 Agent 的 skills 根目录、审批和命令执行模型不同，请以对应产品说明为准。本项目首版在 Codex 上验证，但 Skill 本身不写死 Codex 专有工具。

## 使用

可以直接告诉 Agent：

```text
帮我检查这台电脑上的 MAA、MuMu 和 ADB，然后根据我的偏好配置并运行一次日常。
```

也可以缩小范围：

```text
只检查 maa-cli 是否完整安装，不要启动模拟器。
```

```text
读取我已有的 maa-cli task，告诉我今天执行会做什么，不要运行。
```

```text
为 default profile 创建一个不使用源石的日常，先 dry-run，确认后再运行一次。
```

```text
在 default profile 上，把同一套 daily-main 依次给账号 A 和账号 B 各跑一次；两个账号都已登录，不使用源石。
```

Skill 会把当前环境证据放在预设示例之前：先读取当前 `--help`、版本、`maa dir`、已有配置和设备状态，再决定哪些指导仍适用。

## 已验证参考

最近核验日期：**2026-08-22**

| 项目 | 实测版本或环境 |
|---|---|
| Windows | x64 |
| maa-cli | 0.7.5 |
| MaaCore | 6.16.8 |
| 模拟器 | 明日方舟专版 MuMu 12 |
| ADB | MuMu 自带 Android Debug Bridge 34.0.1 |
| Agent | Codex |

这些版本和路径只表示本项目实际踩过并验证过的环境，不是硬版本锁，也不是兼容性矩阵。版本不同仍可参考，但应先核对当前官方文档和运行时行为。

同一环境已完成受控真实 smoke：除 `StartUp` 和保守 `Award` 外，还覆盖了公招容量与高星确认策略、信用商店严格白名单购买、基建总览收取与原生队列轮换、按服务器游戏日选择关卡、不使用理智药/源石的停止行为、多账号登录态过期时的失败关闭、登录态有效时完整 A→B 官方切号，以及最大倍率批量后按剩余理智精确补尾。该结果证明这些特定路径曾跑通，也暴露并固化了 `Completed`、在途 Fight、Custom 任务与 Windows 日志的边界；不承诺其他模拟器、账号状态、客户端版本、分辨率或日常组合自动兼容。

详细来源和实测边界见 Skill 的 [`references/`](./maa-daily/references)。主要上游入口：

- [maa-cli 安装](https://docs.maa.plus/en-us/manual/cli/install.html)
- [maa-cli 使用](https://docs.maa.plus/en-us/manual/cli/usage.html)
- [maa-cli 配置](https://docs.maa.plus/en-us/manual/cli/config.html)
- [MAA 连接设置](https://docs.maa.plus/zh-cn/manual/connection.html)
- [MAA 集成任务参数](https://docs.maa.plus/en-us/protocol/integration.html)

仓库同时提供两个 task 起点：[`daily.toml`](./maa-daily/assets/daily.toml) 只有启动与保守领奖，[`full-daily.example.toml`](./maa-daily/assets/full-daily.example.toml) 覆盖公招、严格信用商店、保守基建、刷图和领奖。完整模板仍需部署其注明的用户资源、核对版本与个人策略后再运行。

## 安全边界

- 不默认使用源石。
- 不无提示覆盖已有 task/profile。
- 不把 dry-run 成功当成真实游戏任务成功。
- 不把 PATH 中缺少 `maa` 或 `adb` 当成软件不存在。
- 不为了只读问题启动模拟器、连接设备或运行任务。
- 不处理定时、无人值守、长期后台或多设备编排。
- 多账号仅支持单设备、固定账号列表和一次性显式请求；每个账号先用官方 `maa startup --account-name` 切换，成功退出后再用新的 `maa run` 执行共享业务 task，不使用视觉点击或自定义切号 helper，也不形成未来批次的持久授权。

任何会下载组件、改变用户配置或操作真实游戏的行为，仍服从用户所用 Agent 的审批与命令治理能力。

## 开发验证

仓库测试只使用 Python 标准库，不是 Skill 的运行依赖：

```powershell
py -3 -m unittest discover -s tests -v
```

再使用目标 Agent 的 Skill 校验器检查 `maa-daily/`。真实设备 smoke 不属于普通自动化测试，必须在明确的模拟器实例和用户授权下执行。

## 第三方与许可证

本仓库自己的 Skill、文档、模板和测试使用 [MIT License](./LICENSE)。maa-cli、MaaAssistantArknights、MuMu 和其他第三方组件保持各自许可证与使用条款；本仓库不复制或分发它们的二进制、源码和资源。
