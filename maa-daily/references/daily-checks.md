# 日常检查与清体力组件

- 最近核验日期：2026-09-11
- 官方来源：[流水线协议](https://docs.maa.plus/zh-cn/protocol/task-schema.html)、[FightTimesTaskPlugin v6.17.1](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.17.1/src/MaaCore/Task/Fight/FightTimesTaskPlugin.cpp)、[任务参数](https://docs.maa.plus/en-us/protocol/integration.html)
- 相关实践：[ArknightsAutoHelper 奖励状态识别](https://github.com/ArknightsAutoHelper/ArknightsAutoHelper/blob/master/imgreco/task.py)。只借鉴状态识别思路；发布包不复制其图片或识别代码。
- 边界：理智读取已通过一个账号关卡准备页的重复实测；奖励计数只覆盖下述已验证的国服十档布局，不代表所有客户端或未来布局。结果是奖励档位状态推断，不是背包增量，也不是任务点数统计。

## 复用入口

脚本使用 Python 3.11+ 标准库。`scripts/daily_checks.py` 不自行启动 MAA、不切号、不执行战斗；`reward_check.py scan` 则会执行下文说明的原生扫描任务。不要由 Agent 临时重写同样的倍率运算、资源片段或任务。

```text
python <skill-root>/scripts/daily_checks.py prepare --config-dir <MAA_CONFIG_DIR>
```

这个写入动作需要配置修改授权：合并自带 `assets/daily-checks/tasks.json` 到用户资源，创建理智、奖励页、通用页面 OCR 和完整档位扫描四个原生检查 task。保留其他键，对不一致的同名键/文件拒绝覆盖；用户资源有变化时先保留备份。同一配置根下没有并发写入者时使用，失败后检查输出和备份，不删除整个目录重试。脚本不修改 profile；按现有原生配置启用 `resource.user_resource = true`，不覆盖连接信息。

同时合并 `assets/drain-sanity/tasks.json` 的目标关卡确认及自动导航节点，供[动态清体力候选组件](drain-integration.md)使用；部署这些节点不代表导航或战斗已经通过真实验证。

检查仍通过 maa-cli 和薄 runner 执行：

```text
python <skill-root>/scripts/run_with_evidence.py --report-file <local>/sanity.json -- maa run maa-daily-check-sanity --profile <profile> --batch
python <skill-root>/scripts/daily_checks.py inspect --report <local>/sanity.json
```

- `maa-daily-check-sanity`：起点必须是目标关卡准备页。确认“开始行动”区域后两次 OCR 理智，仅在高置信且一致时输出读数，绝不点击开战。它暂不承载从主页自动选关。
- `maa-daily-check-rewards`：旧的单页 OCR 诊断入口，不承担档位计数；完整收尾改用下文的 `reward_check.py scan`。
- `maa-daily-check-screen`：仅 OCR 当前页面，供未知起点诊断，不点击、不导航，不从通用 OCR 直接确认理智或奖励状态。
- `inspect`：仅读取该 runner 报告的日志字节区间，并校验区间哈希。错误、日志变化、缺少识别结果均不能当作零理智或奖励已领取。报告只放本地，不提交账号页面 OCR。

`daily_checks.py inspect` 只提取单次 OCR/理智，其奖励字段仍为 unknown。不要把这个低层入口当成奖励检查结果；档位计数与提醒使用下面的专用脚本。`AwardFinished` 或 `ReceiveAward` 仍不足以单独证明关键奖励已领取。

## 开跑前的部署预检

在首个账号业务执行前调用，不必先进入游戏任务页：

```text
python <skill-root>/scripts/reward_check.py --layout cn-daily-ten-v1 preflight --maa <maa-executable>
```

复用扫描入口的安装校验，只执行同一个 maa-cli 的 `dir config --batch` 并读取该配置根下的扫描 task 与资源，不启动游戏、不扫描、不写文件。退出 0 / `status=installed` 仅证明捆绑扫描资源一致，输出明确保留 `profile_checked=false`、`game_state_checked=false`。退出 2 时读取 stderr 中的缺失/冲突原因；不要用它代替奖励状态。

随后由 Agent 读取本轮实际 profile 确认 `resource.user_resource = true`，并 dry-run `maa-daily-reward-scan`；继承或覆盖配置按原生配置规则核对。需要部署时用上文 `prepare`，再重复预检。扫描时仍会再次校验资源，防止预检后变化。部署授权与不能静默降级的责任由[收尾检查依赖预检](safety-and-results.md#收尾检查依赖预检)维护。

## 奖励档位：一次调用完成扫描与判定

先过账号身份、可见窗口、设备/profile 和运行授权门禁。起点必须是已登录账号的任务页；通常在最终 Award 后执行，若仍有“获得物资”等弹窗则先按已验证路径关闭，不让检查脚本猜测恢复。脚本不会启动游戏、切号、领奖、补刷或修改 profile。

```text
python <skill-root>/scripts/reward_check.py --layout cn-daily-ten-v1 scan --profile <profile> --output-dir <local-evidence-dir>
```

`--maa <executable>` 可选择当前已核验的 maa-cli。脚本核对已部署的扫描 task 与用户资源和捆绑版本一致，调用薄 runner 包裹一个原生 `maa run maa-daily-reward-scan`，在独立本地子目录写 `evidence.json` 和 `result.json`。不要由 Agent 手工累计回调条数或重新编写同等扫描流程。

本适配器的显式前提：国服日常有十档，合成玉第七档、扫荡券第九档，已领取档位带“已完成”标记并排在未领取档位后面。映射由本次游戏现场与用户确认，不能外推为跨客户端永久协议。`cn-daily-ten-v1` 固定的是游戏界面布局，不含账号、设备或私人路径；其它布局需验证新的适配器，不随意改常量凑结果。

扫描选择日常页，分别滚动到顶部和底部，每个端点重复观察一次。MaaCore 执行全部识别，Python 只消费日志中的文字、位置和分数：

- 按归一化行位置映射到十个列表位置，顶底重叠位置只计一次。
- 先映射位置，再核对两次端点观测及重叠区域。在限定的左侧奖励标记区域，文字含“完成”、置信度不低于 0.8 且矩形/行位置符合布局时计为正向标记，不要求完整“已完成”；“未完成”“未领取”“可领取”等明确反义或未领状态仍拒绝推断。不能拿右侧任务描述中的“完成”计数。局部乱码或低分标记仍保留为未知；仅当另一端点两次都在同一重叠位置可靠识别出正向标记时消歧。
- 消歧后两次端点的可靠位置集合及重叠区域仍须一致；缺页、错位、重复位置、端点不一致、明确反义、乱序回调或错误日志返回 unknown，不借数量上下界掩盖布局和执行冲突。仍无法识别的位置不计入已领下界，也不凭末尾连续性补齐。输出 `observed_claimed`、`resolved_ambiguities`、`visible_claimed` 保留证据，`uncertain_positions` 使用从 1 开始的列表位置（不是奖励等级）。
- 四次观察必须来自同一个成功扫描进程、同一服务器游戏日，报告字节区间哈希必须一致。跨度超过三分钟或跨国服 04:00 换日则拒绝推断。
- 没有任何可靠正向标记时返回 unknown，不把 OCR 缺失强行判为零档。未领取档位可以没有文字，但 OCR 漏识别也会没有文字，因此本扫描不以缺字证明未领取。

输出已领上下界 `claimed_tiers_min/max`、未领上下界 `unclaimed_tiers_min/max`、`count_precision`、两项奖励状态、`reminder_required`、`game_day` 和 `observed_at`。已领下界是四次扫描去重后可靠标记的数量，上界为十档；未领范围为 0 到十减下界。只有上下界相等才填写精确的 `claimed_tiers` / `unclaimed_tiers`，否则留 null。即使空白位置在复读中稳定缺字，也不能据此把范围收紧为精确数量。

已领下界达到 7/9，分别把合成玉/扫荡券标为 claimed；否则为 unknown，而不是猜测 not_claimed。例如已领范围 9–10 时两项均已领；8–10 时合成玉已领、扫荡券未知。不支持当前布局或缺少完整扫描时上下界也为 null。任何不是 claimed 的关键奖励仍需提醒。退出码 0 表示取得有效精确或区间结果，**不表示两项奖励均已领取**；必须读取状态和 `reminder_required`。退出码 2 表示结果未知或执行异常，不自动重跑。

只读回放已有同次扫描报告：

```text
python <skill-root>/scripts/reward_check.py --layout cn-daily-ten-v1 evaluate --report <evidence.json>
```

回放保留原观察时间，不把旧结果归到当前账号或今天。账号身份和运行期间没有其他操作者仍由调用者保证；脚本不从十档列表识别登录账号。日志不在时不能仅凭旧结果文件重新建立证据。

## 基建日志检查

请求包含基建时，在统一收尾清单中直接调用：

```text
python <skill-root>/scripts/infrast_check.py inspect --report <本账号本轮的-evidence.json>
```

无需安装额外资源。脚本仅读取本地报告及其哈希校验通过的日志字节区间，不启动 MAA、不操作游戏、不写配置。使用薄 runner 的精确报告而非模糊时间范围；多个独立进程分别检查，不能用后一轮成功覆盖前一轮失败。调用者仍需保证账号身份，并对照本次配置的 mode、facility 和用户目标判断适用项；脚本不识别账号，也不推测哪些项目本来应该执行。

输出按 Infrast `taskid` 分组：收取动作的产物、订单、信赖分类及未分类动作；轮换/整理动作；已完成的基建设施子任务；通知入口后无收取动作直接退出的 `possible_collection_skip`。只统计 `SubTaskCompleted` 中的实际点击，不把 SubTaskStart、DoNothing 或其他任务链的同名节点计入。收取 OCR 仅对高置信完整标签分类，部分文字留为 unclassified，不能据此断言具体产物已经入账。

`action_observed` 只表示动作已执行，子任务 completed 也不证明设施或待办全部处理完。第一版没有游戏侧空状态检查，因此 `all_work_completed` 保持 unknown、`reminder_required` 保持 true；Agent 复用输出，无需每次手动翻日志，只对用户要求且仍未核验的结果补证或提醒。无动作不等于零待办，也不自动判成失败。

错误分为 `error_groups.infrast`、`other_chains`、`unassigned`：回调用明确的任务身份归属；普通 ERR/CRT 仅在同一日志进程/线程上有唯一活动任务链时按执行区间归属，不能从错误名称猜测。链外、不同线程或多链歧义的错误保留为未归属告警，不自动归给基建。分组保留行号与归属依据，不将其他任务的错误称为无害。

`status` / 每条链的 `evidence_status` 表示基建动作证据是否可分类；内部错误不抹掉已有动作，回调解析异常或缺少完整基建链时仍为 unknown。全部异常保留在 `error_groups`，`business_review_required=true` 要求核验受影响的用户目标，不表示异常已被证明无害。独立的 `execution` 描述执行边界，`run_status` 保留 clean、warnings、failed 分类。退出码 0 只表示日志可分类且执行未失败，2 表示证据未知或执行失败；两者都不能证明基建全完成。`continuation` 为 blocked_execution 或 requires_business_preconditions，后者不能代替 Agent 的业务前置核验。旧报告的执行重分类与后续门槛见[结果分层](safety-and-results.md#执行状态内部异常与业务结果分层)。

`interval_line` 从本报告区间第一行计数；`observed_at` 保留原报告结束时间，不代表重放时的游戏现状。当前适配本机实测回调结构；未知节点、其他语言标签和新版本语义不猜测映射。超过 64 MiB 的单次区间拒绝读取，不回退扫描整个历史日志。

## 基本关卡单场理智

唯一机器事实源是 [assets/stage-costs.json](../assets/stage-costs.json)，来自 MAA v6.17.1 的 [stages.json](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.17.1/resource/stages.json) 中 `apCost`，最近核对 2026-09-11。可离线查询，不启动游戏：

```text
python <skill-root>/scripts/daily_checks.py stage-cost --stage LS-6
```

省略 `--stage` 列出全部。常用值：AP-5、CA-5、SK-5 为 30；CE-6、LS-6 为 36；PR-A/B/C/D 的 1 级为 18、2 级为 36；1-7 为 6。表内还包含资源关卡的较低等级。以脚本读取值为准，不另外手写一份运行时映射。

`plan --stage` 和 `drain_sanity.py` 自动取已收录消耗。保留旧 `--cost` 调用，但已知关卡传入冲突值会在生成 task / 导航前拒绝，而不是覆盖事实。表外关卡只有通用 plan 接受经当前资源或游戏核实的显式 `--cost`；不能把未知默认为 30。纯算术调用不带 stage 时仍须给 cost。

该表只记录正常单场消耗，不证明开放、解锁、代理资格或动态清体力支持。剿灭返还、特殊活动和后续版本变化不套用；发现与游戏冲突时停止并核实更新事实源，不为绕过保护手改参数。

## 清体力日常

需要把下面的“读取→计算→执行→重读”串成一次调用时，使用[既有任务接入指南](drain-integration.md)中的 `drain_sanity.py`。它目前是限定资源关卡的候选，不自动修改既有 business task，也不把未经 smoke 的导航当成生产能力。

目标是当前授权资源用尽后，余额不足目标关卡单场消耗，不要求余额为零。普通自然理智和用药后的理智使用同一模型；芯片等次数/库存目标只参考本节，不默认清空理智。

取得当前账号、当前关卡的可靠理智 `S`、实际支持倍率 `M` 后，优先按关卡自动查询单场消耗 `C`：

```text
python <skill-root>/scripts/daily_checks.py plan --sanity <S> --stage LS-6 --maximum <M>
```

计算 `N = floor(S / C) = q × M + k`。脚本只生成下一阶段的参数，不执行它：

需要生成原生文件时加 `--task-file <新文件.json>`（必须有 `--stage`），脚本直接生成下一阶段 Fight JSON，无需手工拼装。目标必须不存在；`N=0` 不生成文件。未知关卡先核实再显式给 `--cost <C>`；不带 stage 的纯算术调用也必须给 cost。生成前确认理智证据新鲜，生成后仍需 dry-run；脚本不验证关卡开放或代理资格。

- `N = 0`：`next_fight = null`，不创建 `times=0` 或 `series=0` 伪探测。
- `0 < N < M`：一次 `series=N, times=N`。
- `N >= M`：`series=M, times=q*M`，不给额外的不足理智最大批次尝试。
- 每阶段结束重新观察理智，再调用计算器；输出的余数只是估计，不能直接排队执行。自然回复、升级或药物都会改变余额。

`plan` 明确只适用于无后续恢复预算阶段，输出的普通药、临期药和源石预算均为零。仍需使用授权药物时，先按[原生配置](native-config.md)处理并核验恢复阶段，不能把当前 `N=0` 当成资源目标已经完成。不要把一次预算重复带入多个生成 task。

没有可信理智读数时保留未知。检查原型未验证或起点不满足时，不机械强制它；使用已有经过验证的批量后读终态补尾路径，或在明确范围内验证原生 AUTO。不能将缺失输入默认为零。

v6.17.1 的 `series=0` 新倍率路径，在初始状态优先选最大允许次数；在恢复相关状态后按理智和单场消耗调整倍率。不能把它简化为“始终退到单倍”，也不等于“首次就按当前理智选择倍率”。是否优先采用 AUTO 取决于当前版本真实测试，而不是强制自定义实现。

## 验证与接入门槛

2026-09-07 在 maa-cli 0.7.5 / MaaCore 6.17.1 / Windows MuMu 上，以隔离配置完成页面 OCR 原生 task 的 dry-run 和真实读取，runner 区间及解析输出有效；当时游戏持续停留加载页，没有进入关卡或每日任务页。该 smoke 只验证通用页面采集，不证明理智读取、奖励状态识别或完整导航已通过；没有切号、开战、用药或领取动作。

同日用户手动登录后，继续以当前账号验证：捆绑奖励页组件成功读取每日任务及进度，但输出不足以确认两项奖励已领取。关卡准备页的理智组件连续两次运行均取得两次一致的 `205/205`，开始行动节点均为 `DoNothing`。发现并修正了回调 `details.task` 去掉命名空间导致解析器漏认的兼容问题：仅在 `Custom` 且 `first` 明确属于本组件时恢复名称。修正后原始有界日志回放与新一次真实运行均返回正确理智。导航使用本地临时 task，尚不属于正式可复用入口；无战斗、用药、切号或领奖动作。

离线验证覆盖运算边界、资源冲突保护、日志区间/错误/未知结果。真实验证单独覆盖：零理智、不足一批、整批与余数、恢复预算；奖励运行前已领、可领未领、未达成、识别失败与跨游戏日。只导航的 smoke 不证明战斗或奖励状态分类已验证。

后续实测取得已领 1、7、8、9、10 档五组带人工真值的样本；旧“已领取”图片曾在局部试验命中，但最终组件仅使用 MaaCore OCR 的“已完成”与行几何，不需要外部图片。仓库测试只保留脱敏后的标记垂直中心投影；旧样本顶部只扫描一次，回放测试中的顶部复读是合成的，不能称五种状态都经过新完整协议实测。

完整脚本在相同环境、当前全领取状态真实执行成功，自动输出 10/10 和两项 claimed；没有 Agent 手工汇总，未执行领取或资源动作。多账号、其它主题/布局、零档和自然换日仍未真实覆盖；未知条件保持保守提醒。正式 business task 的战斗策略未修改，检查由最终 Award 后的这个独立进程承载。

2026-09-11 的重叠消歧修订：两份既有完整扫描日志回放中，一端同一位置的乱码由另一端两次精确标记支持，均恢复为 9/10；另一个 8/10 样本保持不变。新增合成反例覆盖缺少支持、两端都歧义、错位、重复行和明确相反状态。修订后一次真实扫描完成全部节点，但没有正向标记，保守返回 unknown；这验证了扫描链与未知保护，不等于新消歧分支已经在现场重新复现。运行与用户数据只留本地。
