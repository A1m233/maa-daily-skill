---
name: maa-daily
description: 指导 Agent 使用 MaaAssistantArknights 的 maa-cli 检查环境、发现模拟器与 ADB、创建或维护原生 task/profile、dry-run，并为单个账号或单设备上的固定多账号列表安全运行一次个性化明日方舟日常。Use when 用户提到 MAA、maa-cli、明日方舟日常、MuMu 连接、MAA 自定义任务、配置日常、执行一次日常、切号、多账号依次跑同一 task 或排查单次 MAA 运行；不负责定时、无人值守或多设备编排。
---

# MAA 个性化日常

把 maa-cli 当作外部运行时，把本 Skill 当作指导。优先理解用户已有环境和配置，不机械重走完整流程，也不把示例版本、路径或步骤当成永久契约。

## 按需读取资料

- 安装、版本、目录或部分安装判断：读取 [references/install-and-discovery.md](references/install-and-discovery.md)。
- task、profile、variants、已有文件保护或 dry-run：读取 [references/native-config.md](references/native-config.md)。
- 单设备切号、多个已登录账号依次复用同一 task：读取 [references/multi-account.md](references/multi-account.md)。
- Windows MuMu、模拟器自带 ADB、实例与端口：读取 [references/mumu-windows.md](references/mumu-windows.md)。
- Custom 任务、用户资源、局部状态恢复或严格白名单购买：读取 [references/custom-tasks.md](references/custom-tasks.md)。
- 授权、资源风险、长任务等待和结果分类：读取 [references/safety-and-results.md](references/safety-and-results.md)。
- 需要低风险起点时使用 [assets/daily.toml](assets/daily.toml)；需要覆盖常见日常组件的参考时使用 [assets/full-daily.example.toml](assets/full-daily.example.toml)。两者都必须先按用户偏好和当前版本 review，不得未经检查原样执行。

## 完成一次日常请求

1. **判断用户意图。** 区分只读检查、首次配置、修改日常、dry-run、单账号执行和单设备多账号依次执行。只追问会改变 task 内容、账号或设备目标、执行顺序或资源风险的信息。
2. **发现当前环境。** 先使用当前 `maa --help`、`maa version` 和 `maa dir`。命令不在 PATH 时再检查官方默认位置或模拟器目录。分别判断 CLI、MaaCore、配置、profile、ADB、模拟器实例和设备连接，不把部分安装笼统报成“没装”。需要由 Agent 启动桌面模拟器时，默认使用普通可见模式；只有用户明确选择后台或隐藏运行时才隐藏窗口。
3. **优先复用原生资产。** 读取 `<MAA_CONFIG_DIR>/profiles` 与 `<MAA_CONFIG_DIR>/tasks`。用户已有成熟 profile/task 时优先复用，不强制迁移到固定名称。
4. **形成个性化 task。** 用 maa-cli 原生 TOML、YAML 或 JSON 表达用户选择。考虑真实起始界面：如果运行可能从客户端未启动、登录页或黄色 `START` 页开始，在业务任务前配置当前版本支持的 `StartUp`；只有能证明已在可识别主界面时才可省略。按星期选择关卡时必须显式判断使用本地自然日还是服务器游戏日；存在凌晨换日差异时使用当前版本支持的游戏服务器时区，并从 verbose dry-run 核对最终关卡。多账号请求只使用已登录且能由唯一 `account_name` 区分的账号；切号使用 maa-cli 官方预定义命令 `maa startup <client> --account-name <name>`，共享业务 task 不再包含第二个无账号目标的 `StartUp`。用户明确要求从本机现有日志发现账号时，先按多账号参考执行 `maa dir log` 和限定范围的日志检索；只有日志仍不能唯一辨认账号时才询问用户，不得未经检查就断言命令行无法发现。具体隔离方式见多账号参考。默认不使用源石；不要猜测当前版本不确定的参数名或枚举值，必要时核对当前官方文档和命令帮助。
5. **保护真实配置。** 新 task 直接属于 maa-cli 原生 `tasks` 目录。修改已有文件前展示影响并保留可恢复副本；只操作精确目标，不改无关配置。
6. **按价值验证。** 当前版本支持且验证有意义时，先用 `maa list` 确认发现，再用 `maa run <task> --dry-run --batch --profile <profile>` 检查解析。明确 dry-run 不连接设备，也不能证明所有 MaaCore 参数或真实任务正确。maa-cli 可能在解析前执行资源热更新；网络错误时先用 verbose 区分更新失败与配置失败，不把二者混报。
7. **补足必要授权。** 用户已明确要求运行一个已知日常时不要重复机械确认。首次安装、更新、实质修改配置、设备目标不唯一或存在源石等显著资源风险时，先说明影响并取得相应同意。
8. **执行一次。** 单账号使用当前版本实际支持的命令运行一次 task。多账号属于同一次用户请求，但必须按固定顺序为每个账号先运行 `maa startup <client> --account-name <唯一登录名>`；该进程成功退出只是必要条件，还要检查本次日志中是否出现登录过期、重新认证、回退到最近账号等反证，账号身份可信后才用新的 `maa run` 执行共享业务 task。不要把多个账号塞进同一个 MaaCore 运行队列，也不要用自定义切号 helper、虚构的未匹配账号或视觉点击代替官方切号命令。任一账号切换失败、身份不确定、业务任务失败或遗留状态无法安全恢复时停止后续账号，不猜测性继续。让普通非交互命令保持 pending 直到退出、超时、取消或运行时失联；不要用高频 Agent/LLM 轮询观察长任务，也不要因暂时零输出重复启动同一任务。Agent 启动了模拟器时，按用户选择在任务后保持可见、恢复可见或正常关闭，并报告实际生命周期；不要遗留一个无窗口但会拦截后续启动的隐藏实例。
9. **基于证据汇报。** 结合退出码、summary、stderr 和日志区分成功、部分完成、正常跳过、配置错误、连接错误和任务错误。多账号逐账号报告切换、任务与业务后置条件，不把一部分账号完成概括成全部完成。`Completed` 只说明配置中的任务链结束：`StartUp Completed` 不证明目标账号身份，`Recruit Completed` 不证明受保护的高星槽位已经开招，`Infrast Completed` 不证明未配置的基建入口已处理；涉及购买、领取、招募、基建轮换或资源消耗时补充日志或游戏侧证据。记录必要日志路径，但不要把用户日志、账号标识或设备信息写入 Skill 仓库。

## 保持判断空间

- 把上述流程作为推荐判断框架。用户已完成某些步骤、当前版本提供更合适的命令或宿主有等价安全能力时，合理跳过、调整或替换步骤。
- 把 references 中的日期、版本、路径和命令当成已验证参考。当前环境证据优先；契约明显变化时停止照抄旧示例并说明差异。
- 不为了回答只读问题安装组件、启动模拟器、启动 ADB server、连接设备或运行游戏任务。
- 不通过连续猜测性点击、换端口或补偿重试掩盖不确定性。目标不唯一或影响不清楚时停止并询问。

## 硬性安全边界

- 未经用户明确知情同意，不配置或消耗源石。
- 不无提示覆盖已有 task/profile，不把设备地址、账号、偏好、日志或备份提交到公开仓库。
- 不把 dry-run 成功描述成设备连接或真实日常成功。
- 不把 PATH 中缺少 `maa`/`adb` 等同于软件不存在。
- 不承担定时、无人值守、长期后台运行、持久授权或多设备编排。多账号只支持同一设备上的固定账号列表和一次性显式请求，且逐账号隔离运行；不得把本次授权外推到未来批次。
