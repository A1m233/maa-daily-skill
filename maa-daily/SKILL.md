---
name: maa-daily
description: 指导 Agent 使用 MaaAssistantArknights 的 maa-cli 检查环境、发现模拟器与 ADB、创建或维护原生 task/profile、dry-run 并安全运行一次个性化明日方舟日常。Use when 用户提到 MAA、maa-cli、明日方舟日常、MuMu 连接、MAA 自定义任务、配置日常、执行一次日常或排查单次 MAA 运行；不负责定时、无人值守或多设备编排。
---

# MAA 个性化日常

把 maa-cli 当作外部运行时，把本 Skill 当作指导。优先理解用户已有环境和配置，不机械重走完整流程，也不把示例版本、路径或步骤当成永久契约。

## 按需读取资料

- 安装、版本、目录或部分安装判断：读取 [references/install-and-discovery.md](references/install-and-discovery.md)。
- task、profile、variants、已有文件保护或 dry-run：读取 [references/native-config.md](references/native-config.md)。
- Windows MuMu、模拟器自带 ADB、实例与端口：读取 [references/mumu-windows.md](references/mumu-windows.md)。
- Custom 任务、用户资源、局部状态恢复或严格白名单购买：读取 [references/custom-tasks.md](references/custom-tasks.md)。
- 授权、资源风险、长任务等待和结果分类：读取 [references/safety-and-results.md](references/safety-and-results.md)。
- 需要一个低风险起点时，复制并按用户偏好修改 [assets/daily.toml](assets/daily.toml)；不要未经 review 原样执行模板。

## 完成一次日常

1. **判断用户意图。** 区分只读检查、首次配置、修改日常、dry-run 和立即执行。只追问会改变 task 内容、设备目标或资源风险的信息。
2. **发现当前环境。** 先使用当前 `maa --help`、`maa version` 和 `maa dir`。命令不在 PATH 时再检查官方默认位置或模拟器目录。分别判断 CLI、MaaCore、配置、profile、ADB、模拟器实例和设备连接，不把部分安装笼统报成“没装”。
3. **优先复用原生资产。** 读取 `<MAA_CONFIG_DIR>/profiles` 与 `<MAA_CONFIG_DIR>/tasks`。用户已有成熟 profile/task 时优先复用，不强制迁移到固定名称。
4. **形成个性化 task。** 用 maa-cli 原生 TOML、YAML 或 JSON 表达用户选择。考虑真实起始界面：如果运行可能从客户端未启动、登录页或黄色 `START` 页开始，在业务任务前配置当前版本支持的 `StartUp`；只有能证明已在可识别主界面时才可省略。默认不使用源石；不要猜测当前版本不确定的参数名或枚举值，必要时核对当前官方文档和命令帮助。
5. **保护真实配置。** 新 task 直接属于 maa-cli 原生 `tasks` 目录。修改已有文件前展示影响并保留可恢复副本；只操作精确目标，不改无关配置。
6. **按价值验证。** 当前版本支持且验证有意义时，先用 `maa list` 确认发现，再用 `maa run <task> --dry-run --batch --profile <profile>` 检查解析。明确 dry-run 不连接设备，也不能证明所有 MaaCore 参数或真实任务正确。maa-cli 可能在解析前执行资源热更新；网络错误时先用 verbose 区分更新失败与配置失败，不把二者混报。
7. **补足必要授权。** 用户已明确要求运行一个已知日常时不要重复机械确认。首次安装、更新、实质修改配置、设备目标不唯一或存在源石等显著资源风险时，先说明影响并取得相应同意。
8. **执行一次。** 使用当前版本实际支持的命令运行一次 task。让普通非交互命令保持 pending 直到退出、超时、取消或运行时失联；不要用高频 Agent/LLM 轮询观察长任务，也不要因暂时零输出重复启动同一任务。
9. **基于证据汇报。** 结合退出码、summary、stderr 和日志区分成功、部分完成、正常跳过、配置错误、连接错误和任务错误。`Completed` 只说明任务链结束，不自动证明“全部买到”“全部领取”等业务后置条件；涉及购买、领取或资源消耗时补充日志或游戏侧证据。记录必要日志路径，但不要把用户日志或设备信息写入 Skill 仓库。

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
- 不承担定时、无人值守、长期后台运行、持久授权、多设备或多账号调度。
