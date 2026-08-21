# Custom 任务与严格白名单购买

## 核验信息

- 最近核验日期：2026-08-21
- 实测环境：maa-cli 0.7.5，MaaCore 6.16.8，国服信用商店，1280×720 归一化界面
- 官方来源：[任务流水线协议](https://docs.maa.plus/en-us/protocol/task-schema.html)、[maa-cli 配置](https://docs.maa.plus/en-us/manual/cli/config.html)
- 边界：本页记录可复用的 Custom/ProcessTask 语义和一次真实界面方案，不承诺其他客户端、分辨率或未来版本保持相同布局。

## 何时使用用户资源

内置任务的参数无法精确表达用户目标时，再使用 Custom 任务和用户资源。例如标准 `Mall` 的优先购买列表不等于“严格只购买白名单”：开启普通购物后，白名单阶段结束仍可能购买其他商品。

在 profile 中启用用户资源：

```toml
[resource]
user_resource = true
```

用户资源属于 maa-cli 原生配置目录，不放入日常 task 文件。先用 `maa dir config --batch` 取得配置根，再把任务定义放入当前版本要求的 `resource/tasks/tasks.json`。修改已有用户资源时同样先读取、备份和合并，不覆盖其他自定义任务。

## ProcessTask 的关键语义

- `next` 是当前任务完成后按顺序尝试识别的候选列表。某个候选未命中时，不会进入该候选自身的 `next`；因此不能把一串未命中的 OCR 节点当作会自动逐层 fall through 的链。
- `B@A` 形式会为派生任务应用命名空间。未显式定义时，MaaCore 可从 `A` 派生并为后续节点增加前缀。
- 显式定义 namespaced task 并使用 `baseTask` 时，不要假设模板名一定继承为基础任务模板。若实际需要基础模板，显式写 `template = "A.png"`，并在真实运行中验证。
- `OcrDetect` 面对多个结果时默认取首个结果。全屏查找同名商品可能一直命中前方已售罄商品，遮蔽后面的可购买商品。

这些行为有版本演进可能。配置与运行现象冲突时，先读取当前任务流水线协议和 MaaCore 日志，不用重复点击掩盖不确定性。

## 严格只买招聘许可的受控示例

[assets/strict-credit-recruit-permit/tasks/tasks.json](../assets/strict-credit-recruit-permit/tasks/tasks.json) 提供一份可选用户资源。它按 1280×720 信用商店的 5×2 商品槽位分别 OCR：

1. 入口先领取当日信用；已经领取时也继续进入扫描。
2. `JustReturn` 节点把十个槽位作为同一层候选，避免未命中节点截断遍历。
3. 只有槽位文字匹配“招聘许可”才点击。
4. 点击后尝试识别购买按钮；商品已售罄时继续扫描后续槽位。
5. 购买成功后关闭“获得物资”弹窗，并从头扫描，直到没有可购买的招聘许可。
6. 其他商品名称不会触发点击。

task 中的入口示例：

```toml
[[tasks]]
name = "信用商店：领取信用并且只买招聘许可"
type = "Custom"
params = { task_names = ["MaaDailyCredit@MallBegin"] }
```

这个资源是受控参考，不是默认日常模板。使用前至少确认：

- 当前客户端信用商店仍是相同 5×2 布局；
- MaaCore 截图归一化坐标仍对应 1280×720；
- `CreditShop-BuyIt.png`、`CreditShop-Bought.png` 和相关内置任务仍存在；
- 已有 `tasks.json` 与示例做对象级合并，而不是整文件覆盖；
- 先在可观察环境验证零目标、多个目标、售罄在前和重复运行状态。

运行后不能只凭 Custom `Completed` 断言“全部买完”。核对每次购买、最终扫描结果与游戏侧商店状态；证据不足时按 [安全授权与结果解释](safety-and-results.md) 保守报告。

当前捆绑资源有一个已确认的待修缺口：`MaaDailyCredit@CrisisPopup` 只声明了 `baseTask = "CrisisPopup"`，没有显式指定 `template = "CrisisPopup.png"`。2026-08-19 的两个真实账号运行都记录 `templ not found MaaDailyCredit@CrisisPopup.png`，随后跳到许可扫描并把 Custom 汇总为 `Completed`。2026-08-21 的双账号运行仍出现相同错误，但后续日志分别记录了 1 次和 2 次完整的 `CreditShop-BuyIt` → `CreditShop-Bought`，证明该错误不一定阻断后续购买，也不能被购买成功反向解释为流程无错误。报告时同时保留内部错误与每次已成立的购买后置条件；修复并复验前，不把当前资源描述为无已知缺口。
