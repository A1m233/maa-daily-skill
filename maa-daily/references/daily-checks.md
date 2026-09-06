# 日常检查与清体力组件

- 最近核验日期：2026-09-07
- 官方来源：[流水线协议](https://docs.maa.plus/zh-cn/protocol/task-schema.html)、[FightTimesTaskPlugin v6.17.1](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.17.1/src/MaaCore/Task/Fight/FightTimesTaskPlugin.cpp)、[任务参数](https://docs.maa.plus/en-us/protocol/integration.html)
- 相关实践：[ArknightsAutoHelper 奖励状态识别](https://github.com/ArknightsAutoHelper/ArknightsAutoHelper/blob/master/imgreco/task.py)。只借鉴状态识别思路；发布包不复制其图片或识别代码。
- 边界：理智读取已通过一个账号关卡准备页的重复实测；奖励计数只覆盖下述已验证的国服十档布局，不代表所有客户端或未来布局。结果是奖励档位状态推断，不是背包增量，也不是任务点数统计。

## 复用入口

脚本使用 Python 3.11+ 标准库。`scripts/daily_checks.py` 不自行启动 MAA、不切号、不执行战斗；`reward_check.py scan` 则会执行下文说明的原生扫描任务。不要由 Agent 临时重写同样的倍率运算、资源片段或任务。

```text
python <skill-root>/scripts/daily_checks.py prepare --config-dir <MAA_CONFIG_DIR>
```

这个写入动作需要配置修改授权：合并自带 `assets/daily-checks/tasks.json` 到用户资源，创建理智、奖励页、通用页面 OCR 和完整档位扫描四个原生检查 task。保留其他键，对不一致的同名键/文件拒绝覆盖；用户资源有变化时先保留备份。同一配置根下没有并发写入者时使用，失败后检查输出和备份，不删除整个目录重试。脚本不修改 profile；按现有原生配置启用 `resource.user_resource = true`，不覆盖连接信息。

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

## 奖励档位：一次调用完成扫描与判定

先过账号身份、可见窗口、设备/profile 和运行授权门禁。起点必须是已登录账号的任务页；通常在最终 Award 后执行，若仍有“获得物资”等弹窗则先按已验证路径关闭，不让检查脚本猜测恢复。脚本不会启动游戏、切号、领奖、补刷或修改 profile。

```text
python <skill-root>/scripts/reward_check.py --layout cn-daily-ten-v1 scan --profile <profile> --output-dir <local-evidence-dir>
```

`--maa <executable>` 可选择当前已核验的 maa-cli。脚本核对已部署的扫描 task 与用户资源和捆绑版本一致，调用薄 runner 包裹一个原生 `maa run maa-daily-reward-scan`，在独立本地子目录写 `evidence.json` 和 `result.json`。不要由 Agent 手工累计回调条数或重新编写同等扫描流程。

本适配器的显式前提：国服日常有十档，合成玉第七档、扫荡券第九档，已领取档位带“已完成”标记并排在未领取档位后面。映射由本次游戏现场与用户确认，不能外推为跨客户端永久协议。`cn-daily-ten-v1` 固定的是游戏界面布局，不含账号、设备或私人路径；其它布局需验证新的适配器，不随意改常量凑结果。

扫描选择日常页，分别滚动到顶部和底部，每个端点重复观察一次。MaaCore 执行全部识别，Python 只消费日志中的文字、位置和分数：

- 按归一化行位置映射到十个列表位置，顶底重叠位置只计一次。
- 两次端点观测及重叠区域必须一致，已领取位置须构成末尾连续区段；低置信、错位、缺页、乱序回调或错误日志返回 unknown。
- 四次观察必须来自同一个成功扫描进程、同一服务器游戏日，报告字节区间哈希必须一致。跨度超过三分钟或跨国服 04:00 换日则拒绝推断。
- 没有任何正向“已完成”标记时返回 unknown，不把 OCR 缺失强行判为零档。只能区分已领/未领，不能区分未解锁与可领未领。

输出 `claimed_tiers`、`unclaimed_tiers`、两项奖励状态、`reminder_required`、`game_day` 和 `observed_at`。已领数达到 7/9 才把对应奖励标为 claimed，否则为 not_claimed；识别不可靠时为 unknown。任何不是 claimed 的关键奖励都需要提醒，规则由[安全与结果](safety-and-results.md#每日关键奖励收尾检查)维护。退出码 0 表示成功取得计数，**不表示两项奖励均已领取**；必须读取 `reminder_required` 和状态。退出码 2 表示结果未知或执行异常，不自动重跑领奖或整个日常。

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

退出码 0 表示完成日志分类，不表示基建全完成；2 表示报告不可用、日志缺失/变化、运行错误或没有完整基建链。失败运行中可解析的动作仍保留作部分证据，但不得覆盖失败。`interval_line` 从本报告区间第一行计数；`observed_at` 保留原报告结束时间，不代表重放时的游戏现状。当前适配本机实测回调结构；未知节点、其他语言标签和新版本语义不猜测映射。超过 64 MiB 的单次区间拒绝读取，不回退扫描整个历史日志。

## 清体力日常

目标是当前授权资源用尽后，余额不足目标关卡单场消耗，不要求余额为零。普通自然理智和用药后的理智使用同一模型；芯片等次数/库存目标只参考本节，不默认清空理智。

取得当前账号、当前关卡的可靠理智 `S`、单场消耗 `C`、实际支持倍率 `M` 后：

```text
python <skill-root>/scripts/daily_checks.py plan --sanity <S> --cost <C> --maximum <M>
```

计算 `N = floor(S / C) = q × M + k`。脚本只生成下一阶段的参数，不执行它：

需要生成原生文件时加 `--stage <stage> --task-file <新文件.json>`，脚本直接生成下一阶段 Fight JSON，无需手工拼装。目标必须不存在；`N=0` 不生成文件。生成前由 Agent 确认 `C` 对应此关卡且理智证据新鲜，生成后仍需 dry-run；脚本不验证关卡开放或代理资格。

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
