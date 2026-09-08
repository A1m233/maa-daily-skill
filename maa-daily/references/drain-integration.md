# 既有日常接入动态清体力

- 最近核验日期：2026-09-08
- 官方来源：[FightTask v6.17.1](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.17.1/src/MaaCore/Task/Interface/FightTask.cpp)、[StageNavigationTask v6.17.1](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.17.1/src/MaaCore/Task/Fight/StageNavigationTask.cpp)、[原生配置](https://docs.maa.plus/en-us/manual/cli/config.html)。
- 边界：这是清体力日常的接入指南和候选组件，不是任意 task 的自动拆分器。当前只提供 AP-5、CE-6、LS-6 导航；CE-6 已完成首页导航、双读数和 10 连后动态 2 连补尾实测，但补尾掉落识别失败后保守停止。AP-5、LS-6 导航及新流程双账号端到端尚未实测。未经对应 smoke，不把旧入口迁移成已验证完成态。

## 首次接入：明确三段，不复制日期规则

先读取旧 task 并说明将替换哪些普通清体力 Fight。剿灭、指定场次、掉落或库存目标不是普通清体力，不按类型为 Fight 就一起删掉。取得配置修改授权、备份后，再制作候选；不要在本轮跑过旧 Fight 后又执行新循环。

按现有业务顺序形成三个原生文件，文件名由用户现有约定决定：

1. **前段业务**：保留公招、商店、基建，以及普通清体力之前的剿灭日期条件与参数。不要因拆分改变资源优先级。
2. **选关策略**：只保留一份普通关卡的原生 variants，包含无条件默认和服务器游戏日。用 dry-run 让 maa-cli 解析当天最终关卡，由 Agent 把该关卡传给组件；脚本不重写星期逻辑、不解析人类日志来猜日期。在每个 params/variant 中剥离旧批量/补尾参数，统一为 `times=0, series=1, medicine=0, medicine_expire_days=0, stone=0`，移除 drops 等旧目标字段。该文件只供 dry-run，不能执行来探测理智或导航；v6.17.1 的 times=0 会跳过全部 Fight 子任务。
3. **收尾业务**：保留最终 Award 和用户其他明确的后置步骤。奖励扫描在最后一次实际战斗、最终 Award 后独立调用，不塞到前段。

旧批量与补尾的关卡规则若不一致，先澄清目标，不擅自挑一份。候选应核对一周分支覆盖、资源预算和重复执行风险。验证通过后，在既有授权范围内把旧完整入口移为不可发现的备份，明确下次调用的进程顺序；不要同时把旧完整 task 与新流程都列为日常执行步骤。模板仍保留独立静态运行的保守方案，不能仅更新 Skill 就声称已迁移用户 task。

## 每个账号的执行顺序

环境和检查资源预检 → 官方切号并核对身份 → 前段业务（按条件优先剿灭）→ 确认当天普通关卡与起点 → 清体力组件 → 收尾业务 → 基建与奖励检查及提醒 → 下一个账号。

前段有失败或需要恢复时不进入清体力。剿灭回退、用药等仍由各自授权及结果核验负责；组件只接收已经没有后续恢复预算的阶段。不能用清体力成功覆盖前段失败。

## 可调用入口

先按[检查组件](daily-checks.md#复用入口)用 `daily_checks.py prepare` 部署资源；这次还会添加三个只读关卡确认节点，不改 profile。已有相同部署可重复执行；冲突拒绝覆盖。

先验证只导航和读取，不打关：

```text
python <skill-root>/scripts/drain_sanity.py probe --stage AP-5 --start-at home --profile <profile> --output-dir <local>
```

经过当前环境有界验证后，无药清体力：

```text
python <skill-root>/scripts/drain_sanity.py run --stage AP-5 --cost 30 --maximum 10 --start-at home --max-runs 100 --max-phases 5 --profile <profile> --output-dir <local>
```

参数是示例，不是用户默认授权。`--start-at` 必填：home 表示稳定主界面；terminal 表示终端总览；prepared 表示目标关卡准备页。未知、弹窗或其他关卡不猜测恢复，由 Agent 用已经验证的 MAA 路径先恢复。`--cost`、倍率和代理资格由 Agent 按当前游戏核验；阶段条件变化、代理不可靠或还有恢复预算时不能调用 run。芯片/活动关卡与剿灭不在候选导航支持列表内，不套用。

组件通过官方 Custom `Terminal-Entry` 和资源关卡节点导航，随后独立核验目标关卡名、“开始行动”区域和两次一致的理智。确认节点均为 DoNothing，不执行 Fight times=0 探路。run 使用已有 `plan` 运算，每轮只提交有理智支付的批次、全部恢复预算为零；核对实际 Fight 次数和掉落关卡，再重读理智。理智不足一场停止；读数未知、零战斗/次数不符、错误链、无理智下降或预算不足时停止，不自动重试。

每个真实进程仍由薄 runner 包裹，运行同步等待，不并发执行。脚本在实际 MAA tasks 目录创建唯一名称的临时 JSON，执行 dry-run 后才运行，显式使用 `--user-resource`；需要该目录和本地证据目录的写权限及用户资源加载授权。当前保留生成文件，不自动删除；`processes.json` 列出精确 task 和报告路径，可按原有清理规则处理。`result.json` 逐阶段更新；中断后不能把 running 视为完成，也不自动续跑，应先确认没有遗留 maa 进程及在途战斗。

默认最多五个战斗阶段、累计一百场；超限在下一次 Fight 前停止而不是截断后谎报清空，按本次授权可显式调整。CLI 退出 0 仅表示 probe 读数成立或 run 达到不足单场的终态，不能代表完整日常完成；2 表示停止或结果未知。内部告警保存在各进程报告和 `warning_report_files`，必须随业务结果一起核验，不称全部无错误。组件不领奖、不切号、不吃药、不承担整套日常调度。

异常报告中，`completed_runs` 只累计已通过场次与关卡证据核验的战斗，不是所有可能实际完成的场次。未核验完的阶段标为 `unverified`；开战后、成功重读前 `remaining_sanity` 为 null，旧读数只保留在 `last_known_sanity`，不得据此补跑。`processes.json` 保留失败进程的报告路径、退出码和错误行，`warning_report_files` 也纳入失败进程。先检查本次失败报告；确认无在途战斗且仍在目标准备页时，可独立调用 probe 复查，但不能用后来读数抹掉原失败或自动重跑未核验场次。

## 验证顺序

先对每个计划使用的关卡验证主页/终端到准备页的只导航入口、关卡名和理智读数；再在明确战斗授权下验证不足单场、少于最大倍率、整批和余数、战斗失败/中断。最后检查迁移后的双账号完整流程：旧 Fight 不再执行、最后 Award 没前移、两项关键奖励逐账号检查。离线测试不替代这些真实验证，用户未授权时停在候选交付。

2026-09-08 的 CE-6 smoke：首次读取足够支付一批 10 连，结算后重读触发 2 连补尾；最后出现 `RecognizeDrops` / `StageDropsTask` 子任务错误，runner 返回 75，循环停止且没有重试。独立 probe 确认最终不足单场。该结果验证了倍率选择和错误停止，不等于无异常完成；异常报告修订通过离线回归，尚未在修订后重新执行真实战斗。旧完整 business task 未由此自动迁移，普通流程的双账号成功也不替代候选端到端验证。未知页面和跨日弹窗仍需先恢复稳定起点，不把单独调用内部 QuickSwitch 节点当作已验证的通用恢复入口。
