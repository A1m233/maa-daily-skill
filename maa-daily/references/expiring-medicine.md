# 临期药策略与统一清体力

- 最近核验日期：2026-09-13
- 官方来源：下文链接的 MaaCore v6.17.1 源码。
- 边界：新统一流程仅源码与离线验证；历史真实样本不能替代新流程实测。

本页拥有用药偏好、固定倍率恢复与收尾检测的契约。执行入口统一为 `drain_sanity.py run`；关卡和游戏日由既有原生选关 task 决定，不复制星期表、不切号、不领奖。标准关卡的原生隔离导航、参数化准备页与 `--cost` 来源见[接入指南](drain-integration.md)；芯片等库存目标不能直接套用清体力语义。

## 持久化两种选择

将 [medicine-policy.example.toml](../assets/medicine-policy.example.toml) 按用户选择存到本机 MAA 配置根的 `policies/`，在日常执行说明记录准确 `--policy` 路径，不依赖会话记忆、不放进原生 `tasks/`、不提交个人配置。

- `off`：不使用临期药，所有战斗的恢复预算为零；仍做收尾检测。
- `use_and_drain`：允许 MAA 使用指定天数范围的临期药；普通药和源石固定为零。

两种模式的 `medicine_expire_days` 都必须是正整数：不用药时仅用于检测提醒，用药时兼作授权范围。不再提供独立 `notify` 模式。省略策略文件时默认 `off`、检测范围为 1，不默认授权用药。

旧候选 `notify` 配置以及 `off + days=0` 会在游戏操作前被拒绝；前者经用户确认迁为 `off` 并保留天数，后者需选择提醒范围。旧 `use_and_drain` 仍可解析，但接入前须说明执行策略已由独立恢复阶段改成下面的固定最大倍率流程。不要自动改写用户配置。

天数沿用 [MAA 原生实现](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.17.1/src/MaaCore/Task/Fight/MedicineCounterTaskPlugin.cpp)：小时/分钟按 1，显示 N 天按 N+1，不自行等同于本地自然日或国服 04:00 换日。这是一次日常的持久化偏好，不授权无人值守或未来任意运行。

## 一次调用

先执行 `daily_checks.py prepare` 部署捆绑资源；Skill 更新不等于资源已部署，冲突拒绝覆盖。`run` 在导航前核对药物检查资源，两模式都需要它。未知关卡的已核实 `--cost` 同时传递给收尾检测，不另猜成本。

```text
python <skill-root>/scripts/drain_sanity.py run --policy <本机策略.toml> --stage AP-5 --profile <profile> --maa <maa-executable> --output-dir <local-evidence> --maximum 10 --max-runs 100 --max-phases 5
```

参数预算是示例，不代表无限清理授权。旧 `medicine_sanity.py run` 已移除，旧恢复阶段/场次参数不再接受；该脚本只保留只读 `check`，不维护第二套清理循环。原有无策略 `drain_sanity.py run` 不会开始用药，但现在会执行收尾检查。

## 不用临期药

自动导航并双读理智 → 按 `floor(S/C)` 计算最大批次或余数 → 核验实际正场次与关卡 → 重新读数 → 还有至少一场则重新计算，否则收尾。

已知仅够九场就直接九连，不先尝试十连。博士升级或自然回复可能抵消消耗，不再单凭理智未下降停止；必须先取得本阶段真实正场次证据，不能用“可能升级”忽略错误或零战斗。总场次、阶段数仍限制循环。升级弹窗若造成识别失败仍停止，不自动关闭或猜测性恢复。

## 使用临期药：固定倍率，不使用 AUTO

1. 先导航并读取当前理智。不先清空已有理智。
2. 提交一次原生固定最大倍率 Fight，携带授权临期范围，普通药和源石为零。`times` 是总预算向下取整到倍率的倍数，不是仅按当前理智算出的可打次数；当前不足整批时仍可能通过授权临期药恢复。不尝试单倍用药或逐档下降。
3. 原生阶段正常结束后，独立核验实际场次和 `UseMedicine` 明细，并重新读理智。即使零场但用了药，也保留消耗；读数失败则停止，不沿用旧值。
4. 未触及原生阶段场次上限时，按新理智调用同一个无药计算循环补尾；例如剩余 275、单场 30，直接九连。补尾所有恢复参数为零。升级后再次重算，不再次进入用药阶段。
5. 不足一场后检测临期药并汇报。检测仍有或未知，不自动重新用药、不逐档试探。

源码依据（MaaCore v6.17.1）：[FightTask](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.17.1/src/MaaCore/Task/Interface/FightTask.cpp) 仅对 `series=0` 启用 `reduce_when_exceed`；固定倍率不启用这个普通理智上限减药逻辑。[MedicineCounterTaskPlugin](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.17.1/src/MaaCore/Task/Fight/MedicineCounterTaskPlugin.cpp) 筛选预选药后执行确认，没有“恢复量必须足够整批才吃药”的判断。因此可以在固定阶段结束后读取实际理智计算补尾，但不能推断全部库存已消耗。

`--max-runs` 限制整个调用的累计已核验场次，`--max-phases` 限制原生用药阶段和后续无药阶段总数。预算不足一批时在用药前停止，不自动缩小测试倍率；原生阶段跑满分配上限时停止并报告 `medicine_run_budget_reached`，不冒充资源耗尽或静默补尾。零战斗、零用药只允许结束原生阶段，不作为完整库存为零的证明，也不重试该阶段。报告错误或未核验场次不能进入补尾。

**场次上限不是药瓶数上限。** 一次确认可能使用多瓶符合范围的药，甚至最终零场；用户必须接受此资源影响。恢复窗口预选、未显示药物和 OCR 仍是原生边界，不承诺最少批次或完整临期库存用光。

## 收尾检测与汇报

两模式在清理成功、理智不足单场后都检测。检测依赖不足理智时点击“开始行动”打开的恢复窗口，不是库存 API。封闭 Custom 链只打开、双读有效期并关闭，不确认吃药或出击；不为检测额外打关。异常/预算停止时不强行检查，保留未检查提醒。

`medicine_sanity.py check --policy <策略> --stage AP-5 --profile <profile> --maa <exe> --output-dir <目录>` 仅供独立只读检查。理智足够时报告前置不满足；`--dialog-open` 仅供已打开窗口的诊断，仍需 MAA 模板确认，不由 Agent 目测。

- `detected`：两次位置、有效期一致的可靠 OCR 确认范围内药物仍存在，提醒用户。
- `unknown`：没有可靠正向结果、空窗口或识别失败，提醒无法确认；不等同于没有。
- 当前不能可靠输出完整库存、药品总恢复量或“所有临期药已用完”；`medicine_goal` 在用药模式保持 `unknown`。

`result.json` 分开记录 `completed_runs`、`medicine_used`、`remaining_sanity`、`medicine_check`、各阶段与内部告警。`completed_with_reminder` 表示理智目标已达成但药物检查有提醒，并非完整用药目标已完成；调用退出 0 后仍须读取这些字段。检查失败不抹掉战斗结果，但终态页面保守报告未知。不用药时也不能省略剩余临期药提醒；用药时汇报已核验瓶数，不能从配置推算实际消耗。

## 验证边界

2026-09-13：之前的有界真实样本已覆盖恢复窗口检测、原生临期药消费、消费后重新读取并无药补尾；另有原生 AUTO 独立样本，但不作为本固定倍率策略的真实验证。

本次统一入口、固定倍率不足整批后的补尾及升级重算使用源码分析和离线测试验证，用户明确不进行游戏侧复验。没有实测证明“固定十连会用光所有临期药”。现有完整库存负向检测缺口继续保留，后续真实使用失败按证据停止，不能为了补齐目标自动回退旧入口。正式 task 和用户策略未由本次代码变更自动迁移。
