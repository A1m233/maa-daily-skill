# maa-cli 原生配置与日常 task

## 核验信息

- 最近核验日期：2026-08-22
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

`StartUp` 也不是任意界面的通用恢复原语。2026-08-17 实测从信用商店“获得物资”弹窗启动时，`StartUp` 无法识别该局部状态并最终报错。已知流程可能停在结果弹窗或其他业务中间态时，把对应恢复节点放在 Custom 任务入口前部，再进入常规导航；只有确实需要处理客户端未启动、登录页或主页导航时才依赖 `StartUp`。

variants 可以按时间、星期或日期选择参数。只有用户确实需要条件化日常时才引入，避免把简单偏好变成难维护规则。多个 variant 匹配时注意当前 `first`/`merge` 策略。

`Weekday` 条件默认按运行机器的本地自然日判断。对国服等凌晨才完成游戏日换日的客户端，本地日期在 `00:00` 到游戏换日前可能已经进入下一天，而关卡仍按前一游戏日开放。当前基线应在这类 variant 上显式使用 `timezone = "Official"`，并用 `-vv` dry-run 核对合并后最终选中的 `stage`。2026-08-18 凌晨实测中，缺少该字段会把仍处于周一游戏日的客户端按周二选择关卡，导致导航失败；不要把它误诊为 OCR 或 ADB 故障。

需要表达关卡优先级时，可以用有序、可能重叠的 weekday variants 配合 `strategy = "merge"`，让后匹配的参数覆盖前值。它表达的是当前日期下的参数选择，不是运行时关卡失败后的 fallback。关卡开放日会变化，实际生成前核对当前游戏与 MaaCore 资料，并从日志确认最终选择的 stage。

`Fight` 的“清理理智”通常表示重复执行到下一次战斗已无法支付，而不是保证余额恰好为零。`medicine = 0`、`stone = 0` 时仍可能打开恢复理智界面后正常关闭；只要没有消耗对应资源且任务按配置停止，不应误报为异常。

`times` 是最多战斗次数，不要求精确完成；`series` 是每次开始行动采用的固定连战倍率。固定 `series = N` 不会在当前理智只够更小倍率时自动降档：MaaCore 会按“单场理智 × N”判断整批消耗，理智不足时尝试配置允许的恢复方式，药和源石预算均为零则结束任务。`series = 0` 的官方 AUTO 会按剩余 `times` 调整倍率，但理智不足以支付其选定倍率时仍采用相同的恢复/结束逻辑，因此它不是“按当前自然理智选择最大可负担倍率”。

2026-08-19 的真实双账号运行中，CE-6 单场 36 理智、`series = 6` 被计算为整批 216；两个账号均为 205/205 理智，日志三次记录 `FightTimes.times_finished = 0`，最终 Fight 仍显示 `Completed`，且没有使用理智药或源石。它证明固定倍率不能自行完成余数收尾，但不意味着日常只能永久使用单倍。

### 单关卡清理理智的统一批量模型

普通自然理智与使用理智药后的超量理智属于同一个问题。设当前理智为 `S`、关卡单场消耗为 `C`、当前客户端和资源实际支持的最大连战倍率为 `M`，则可完成场数为 `N = floor(S / C)`，并可写成 `N = q × M + k`，其中 `0 <= k < M`。目标是先用最大倍率完成 `q` 批，再只用一次 `k` 连完成余数；不要把余数拆成 `5 → 1 → 1 ...`，也不要在 Agent 能读取运行结果时静态铺开 `M → M-1 → ... → 1` 并产生多余的不足理智检查。

当前 MaaCore 没有供此工作流独立调用的只读理智查询。没有可信 `S` 时，把最大倍率 Fight 同时当作批量阶段和探测阶段：

1. 从当前官方资料、资源和真实界面能力确认 `M`；不要把一次验证得到的 `10` 外推到所有客户端或旧资源。
2. 运行 `series = M`、足够大的 `times`、`medicine = 0`、`stone = 0`。若理智能支付整批，它会重复最大倍率直到不足；若一开始就不足，实际战斗为 0 次并正常结束。
3. 等该 maa-cli 进程退出后，从同一次运行的 `SanityBeforeStage`、`FightTimes`、关卡消耗和资源动作取得最终剩余理智 `R`。不要在战斗仍进行时根据暂时的 `times_finished = 0` 计算余数。
4. 计算 `k = floor(R / C)`。`k > 0` 时生成一个 `series = k, times = k` 的精确补尾 Fight；`k = 0` 时跳过补尾 Fight。把最终 `Award` 放在该收尾进程中，确保奖励发生在全部战斗后。

如果运行前已有与当前账号、当前时点对应的可靠理智证据，可以直接计算 `N`：`N < M` 时跳过零战斗的最大倍率探测，直接执行一次 `series = N, times = N`；`N >= M` 时仍按最大倍率批量和一次余数补尾处理。用户另有场次上限时，先把 `N` 截断到本次允许的剩余场数，再做同样分解。

2026-08-22 的国服 Official 资源与真实 TO-5 运行验证了 `M = 10` 的这条路径：单场 12 理智，账号从 943 理智开始，先完成 7 批十连并剩余 103，再以 `series = 8, times = 8` 完成一次八连，最终剩余 7；全程没有使用理智药或源石。该实测支持上述编排形态，但最大倍率仍以当前环境证据为准。

这是一项跨 maa-cli 进程的工作流，不是单个静态 task 文件能够表达的运行时条件分支。公开 TOML 模板继续使用 `series = 1` 作为无需 Agent 编排的保守起点；需要兼顾效率与清空理智时，由 Agent 或经验证的确定性 runner 负责批量、读取余量和生成精确补尾。

`Recruit.times` 是尝试上限，还会受到账号已开放槽位和当前槽位状态限制。配置四次而账号只有三个可用槽位时，实际执行三次属于受容量限制的结果；报告“最多四次”而不是承诺精确完成四次。

`Recruit.select` 与 `Recruit.confirm` 不是同一个开关。`select` 允许 MaaCore 选择对应星级的标签组合，只有星级同时在 `confirm` 中才会自动点击确认并开始招募。例如 `select = [4, 5]`、`confirm = [3, 4]` 会识别并保留五星组合而不自动开招；只有用户明确授权五星也自动开招时，才可改为 `confirm = [3, 4, 5]`。因此 `Recruit Completed` 仍可能留下一个受保护的高星槽位。

`Infrast` 使用 `facility = []`、`drones = "_NotUse"`、`continue_training = false` 在 MaaCore 6.16.8 实测会停留在基建总览并执行批量收取，可领取干员信赖、制造产物和订单，不换班、不使用无人机。这个模式不覆盖线索、疲劳处理等其他基建事务，不要称为“完成全部基建日常”。

`mode = 20000` 在 MaaCore 6.16.8 中是一次原生轮换路径：进入控制中枢总览，点击右下区域的游戏内队列轮换，执行整理，再返回一次。它不是左下角“待办事项/队列轮换”入口的直接映射，也不会循环到所有底部徽标归零；`facility = []` 时还没有逐设施的后续处理。实测即使该链显示 `Completed`，左下角仍可能保留队列轮换或干员调整数量。不要因为用户说“把下面都点掉”就自动映射为 `mode = 20000`；当前原生配置不能证明能完全表达这一后置条件，应明确标记能力缺口并让用户手动处理或另行设计、验证受控 Custom 流程。

模板分两层：[低风险模板](../assets/daily.toml) 只含启动与保守领奖；[完整参考模板](../assets/full-daily.example.toml) 演示常见日常组合，但仍故意使用 `mode = 0, facility = []`、零理智药和零源石，并默认保护五星公招。完整模板中的严格信用商店任务依赖 [Custom 用户资源](custom-tasks.md)，不能只复制 TOML 就直接运行。多账号复用时还要移除开头的无账号 `StartUp`，按 [单设备多账号的一次性运行](multi-account.md) 逐账号切换和独立执行。

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
