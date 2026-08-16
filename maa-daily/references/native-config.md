# maa-cli 原生配置与日常 task

## 核验信息

- 最近核验日期：2026-08-16
- 实测环境：maa-cli 0.7.5，MaaCore 6.16.8
- 官方来源：[maa-cli 配置](https://docs.maa.plus/en-us/manual/cli/config.html)、[使用说明](https://docs.maa.plus/en-us/manual/cli/usage.html)、[MAA 集成任务参数](https://docs.maa.plus/en-us/protocol/integration.html)
- 边界：maa-cli 与 MaaCore 参数会演进。以下示例用于理解当前形态，生成真实配置前核对当前帮助和官方任务参数。

## 原生目录

从 `maa dir config --batch` 获得 `<MAA_CONFIG_DIR>`。maa-cli 原生组织为：

```text
<MAA_CONFIG_DIR>/
├── profiles/
│   ├── default.toml
│   └── <profile>.toml
└── tasks/
    ├── maa-daily.toml
    └── <task>.toml|yaml|json
```

profile 保存 MaaCore、设备连接和资源选择；task 保存按顺序执行的日常任务。不要把设备地址塞进 task，也不要为本 Skill 再造第二套用户配置格式。

列出可用 task：

```powershell
maa list --batch
```

运行文件名为 `maa-daily.toml` 的 task：

```powershell
maa run maa-daily --batch --profile default
```

`--batch` 会跳过 task 中的交互提示并使用默认值。只有确认默认值符合用户意图时才使用；不要把 batch 当成自动同意高风险参数。

## 先复用，再创建

1. 列出并读取已有 profile/task。
2. 用户已有成熟 task 时解释其行为并复用，不强制改名。
3. 新建时直接写入原生 `tasks` 目录；`maa-daily` 只是建议名称。
4. 同名文件存在时先读取，判断是否真的需要修改。
5. 需要修改时展示候选变化并取得与影响相称的授权。

不要扫描或编辑无关文件。不要把用户配置复制进 Skill 仓库。

## 安全修改已有 task

选择当前宿主支持的可靠文件能力，保持这些语义：

- 在同一文件系统准备候选，例如 `maa-daily-candidate.toml`。
- 备份原文件时使用不会被 maa-cli 当作 task 的扩展名，例如 `maa-daily.toml.bak-<timestamp>`。
- 对候选运行 list/dry-run；确认通过后再替换精确目标。
- 替换失败时保留原文件和候选，不用猜测性移动清理扩大损失。
- 成功后精确删除候选；备份是否保留由用户决定。

临时 `MAA_CONFIG_DIR` 可以用于特殊隔离，但它需要复制必要 profile，并可能与真实配置产生差异，因此不作为默认路径。

## task 形态

TOML task 由有序的 `[[tasks]]` 组成：

```toml
[[tasks]]
type = "StartUp"
params = { client_type = "Official", start_game_enabled = true }

[[tasks]]
type = "Award"
params = { award = true, mail = true }
```

常见日常类型包括 `StartUp`、`Recruit`、`Infrast`、`Mall`、`Award` 和 `Fight`。每种 `params` 属于 MaaCore 任务协议；不要根据字段名字面含义猜值。

task 是否需要 `StartUp` 取决于真实起始界面。2026-08-16 的 Windows + MuMu 实测中，游戏停在黄色 `START` 登录页时直接执行 `Award`，ADB、截图与触控均成功，但 MaaCore 无法从该页开始奖励流程；在前面加入上述 `StartUp` 后，能先进入主界面再完成 `Award`。如果用户明确保持在可识别主界面，可以按实际流程省略；不要把 `StartUp` 机械加到所有局部任务。

variants 可以按时间、星期或日期选择参数。只有用户确实需要条件化日常时才引入，避免把简单偏好变成难维护规则。多个 variant 匹配时注意当前 `first`/`merge` 策略。

## 检查与 dry-run

先确认 task 可发现：

```powershell
maa list --batch
```

当前版本支持：

```powershell
maa run maa-daily --dry-run --batch --profile default
```

dry-run 的证明边界：

- 能证明 maa-cli 找到了 task/profile，并完成当前解析与装配路径。
- 不连接模拟器或游戏。
- 不证明 ADB、截图、识别或触控正常。
- maa-cli 不静态验证所有 MaaCore task 参数；部分错误只会在运行时出现。

maa-cli 0.7.5 实测可能在进入 dry-run 解析前检查并更新 hot-update 资源。因此 `Network error` 不自动等于 task 解析失败。先增加 `-v`/`-vv`，确认错误发生在资源更新还是 task 装配；如果是代理或网络问题，只对当前命令采用用户允许的临时网络绕行，不修改全局代理设置。

因此报告“配置通过 dry-run”，不要报告“日常已验证成功”。当前版本不支持相同选项时，读取 `maa run --help` 并选择等价的只读检查；不存在等价方式时明确记录验证缺口。

## 个性化信息

只收集会改变当前日常的信息，例如：

- 使用哪个 profile、客户端和明确设备；
- 是否启动/关闭游戏；
- 公开招募的刷新、确认和加急策略；
- 基建换班偏好；
- 信用商店购买优先级与黑名单；
- 奖励领取范围；
- 刷图关卡、次数、理智药和停止条件。

源石始终单独处理：默认禁用，只有用户明确知情同意才配置非零使用。对不确定字段先查当前官方文档，不把过期示例写进用户配置。
