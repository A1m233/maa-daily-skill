# maa-daily-skill

一个指导 Agent 使用 [MaaAssistantArknights](https://github.com/MaaAssistantArknights/MaaAssistantArknights) 的 `maa-cli` 配置并运行一次个性化明日方舟日常的 Skill，支持单账号，也支持在一个已确认设备上按固定顺序为多个已登录账号运行共用或各自指定的日常配置。

它不捆绑 MAA、maa-cli、MaaCore、ADB 或模拟器，也不提供定时和无人值守能力。使用者保留自己的 profile、task、设备信息和运行日志。

## 安装

把仓库中的 [`maa-daily`](./maa-daily) 目录复制到你的 Agent 所使用的 skills 目录，或者使用宿主支持的标准 Skill 安装方式从本仓库安装 `maa-daily`。

安装后的目录应保持：

```text
<skills-root>/maa-daily/
├── SKILL.md
├── agents/openai.yaml
├── scripts/
├── references/
└── assets/
```

不同 Agent 的 skills 根目录、审批和命令执行模型不同，请以对应产品说明为准。本项目首版在 Codex 上验证，但 Skill 本身不写死 Codex 专有工具。

## SkillHub 分发与发布

GitHub 是唯一维护源。SkillHub 只接收 GitHub Release 对应提交的构建产物，不在平台上另外维护一份 Skill。现有 GitHub 安装方式不变。

维护者首次设置：在 [腾讯 SkillHub](https://skillhub.cn) 登录并完成实名认证，在 [API Key 页面](https://skillhub.cn/dashboard/keys) 创建发布 Token，将其保存到本仓库 Actions Secret **`SKILLHUB_KEY`**。不要把 Token 放进仓库、命令参数或聊天记录。

- 在 Actions 中手动运行 **Publish SkillHub**：只构建和预检，不上传，不需要 Token。
- 发布正式 GitHub Release（标签格式 `v0.1.0`）：自动从该提交构建、验证并提交 SkillHub；普通 push、草稿和 prerelease 不发布。首次版本拟为 `v0.1.0`，尚未发布前不要把预检当作上架成功。
- Actions 产物保留 30 天，包含完整 ZIP、源提交和逐文件 SHA-256 清单；真实提交还保存平台响应。`pending_review` 表示等待审核，必须以平台最终状态确认是否已上架。
- 上传超时或响应不明时，先在 SkillHub 控制台核对版本是否已创建，再决定是否重试同一工作流，避免盲目重复提交。

构建只读取指定 Git 提交中的 `maa-daily/` 与根 `LICENSE`，不带入本机未提交配置、日志或账号资料。平台要求的 `slug/version/displayName` 只注入发布包，源 `SKILL.md` 保持现有格式。后续更新只需维护 GitHub，并发布新的版本号。

本地无上传预检（输出目录必须尚不存在）：

```sh
python scripts/package_skillhub.py --ref HEAD --version 0.1.0 --output dist/skillhub
python scripts/fetch_skillhub_cli.py --output dist/skillhub-cli
python dist/skillhub-cli/skills_store_cli.py --skip-self-upgrade publish dist/skillhub/maa-daily-0.1.0.zip --dry-run --json
```

官方 CLI 固定为 `2026.8.5`，下载后验证 SHA-256，不安装其附带插件、不改变全局偏好。官方 dry-run 主要验证元数据，完整文件集由本仓库的构建清单和测试核验；它不证明账号权限、平台审核或真实上传成功。发布接口依据[官方指南](https://skillhub.cn/ai/release.md)。

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

账号需要不同关卡、用药策略或业务参数时，按[本机账号配置清单](./maa-daily/references/multi-account.md#本机账号配置清单)保存对应关系；相同的原生 task 仍可复用。清单解析与配置隔离已通过离线测试，双账号不同关卡已完成无药战斗及奖励收尾的有界实测；未覆盖的差异组合见该页验收记录。

Skill 会把当前环境证据放在预设示例之前：先读取当前 `--help`、版本、`maa dir`、已有配置和设备状态，再决定哪些指导仍适用。

## 已验证参考

最近核验日期：**2026-08-27**

| 项目 | 实测版本或环境 |
|---|---|
| Windows | x64 |
| maa-cli | 0.7.5 |
| MaaCore | 6.16.8 |
| 模拟器 | 明日方舟专版 MuMu 12 |
| ADB | MuMu 自带 Android Debug Bridge 34.0.1 |
| Agent | Codex |

这些版本和路径只表示本项目实际踩过并验证过的环境，不是硬版本锁，也不是兼容性矩阵。版本不同仍可参考，但应先核对当前官方文档和运行时行为。

同一环境已完成受控真实 smoke：除 `StartUp` 和保守 `Award` 外，还覆盖了公招容量与高星确认策略、信用商店严格白名单购买、基建总览收取与原生队列轮换、按服务器游戏日选择关卡、不使用理智药/源石的停止行为、多账号登录态过期时的失败关闭、登录态有效时完整 A→B 官方切号、最大倍率批量后按剩余理智精确补尾、临期药已消耗但十连零战斗后的精确补尾，以及薄 runner 对独立真实进程的退出状态和 MaaCore 日志边界采集。该结果证明这些特定路径曾跑通，也暴露并固化了 `Completed`、在途 Fight、资源动作、Custom 任务与 Windows 日志的边界；不承诺其他模拟器、账号状态、客户端版本、分辨率或日常组合自动兼容。

详细来源和实测边界见 Skill 的 [`references/`](./maa-daily/references)。主要上游入口：

创建任务从[创建指南](./maa-daily/references/create-task.md)进入。清体力计算和奖励收尾见[日常检查组件](./maa-daily/references/daily-checks.md)：`reward_check.py scan` 通过 MaaCore 扫描已验证的国服十档列表、跨屏去重并输出合成玉/扫荡券提醒。它是最终 Award 后的独立检查，不领取奖励；其它布局或证据不完整时返回未知。计算器可离线使用，理智读取的实测边界见组件说明。

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
- 多账号仅支持单设备、固定账号列表和一次性显式请求；每个账号先用官方 `maa startup --account-name` 切换，核验身份后再以对应配置运行独立业务进程（支持共用配置或本机账号清单指定的差异配置），不使用视觉点击或自定义切号 helper，也不形成未来批次的持久授权。

任何会下载组件、改变用户配置或操作真实游戏的行为，仍服从用户所用 Agent 的审批与命令治理能力。

## 开发验证

仓库测试只使用 Python 标准库，不是 Skill 的运行依赖：

```powershell
py -3 -m unittest discover -s tests -v
```

再使用目标 Agent 的 Skill 校验器检查 `maa-daily/`。真实设备 smoke 不属于普通自动化测试，必须在明确的模拟器实例和用户授权下执行。

## 第三方与许可证

本仓库自己的 Skill、文档、模板和测试使用 [MIT License](./LICENSE)。maa-cli、MaaAssistantArknights、MuMu 和其他第三方组件保持各自许可证与使用条款；本仓库不复制或分发它们的二进制、源码和资源。
