# Custom 任务与严格白名单购买

## 核验信息

- 最近核验日期：2026-11-02（模板绑定与恢复；具体覆盖见末节）
- 实测环境：maa-cli 0.7.5，MaaCore 6.16.8 原基线及 6.21.0 兼容复验，国服信用商店，1280×720 归一化界面
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
- 显式定义 namespaced task 或使用 `baseTask` 时，`template` 缺省为派生节点自己的名称，不自动继承基础模板。不要删掉模板字段来尝试修复，也不要根据节点名猜图片名。捆绑派生节点使用下述绑定组件从实际官方资源取值；隐式 `@` 节点才继承基础模板。
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
- 所需官方节点、模板及实际加载资源层已由下述绑定组件核对，购买识别仍交给 MAA；
- 已有 `tasks.json` 与示例做对象级合并，而不是整文件覆盖；
- 先在可观察环境验证零目标、多个目标、售罄在前和重复运行状态。

运行后不能只凭 Custom `Completed` 断言“全部买完”。核对每次购买、最终扫描结果与游戏侧商店状态；证据不足时按 [安全授权与结果解释](safety-and-results.md) 保守报告。

## 派生模板绑定与升级

`assets` 中的模板字段是已验证旧基线，不是可跨版本直接运行的最终资源。部署检查资源并合并可选商店示例后，使用 `scripts/resource_templates.py` 绑定已部署节点；不再让 Agent 临时手写替换模板脚本：

```text
python <skill-root>/scripts/resource_templates.py bind --config-dir <MAA_CONFIG_DIR> --resource-dir <官方基础resource> --resource-dir <后加载的官方resource>
python <skill-root>/scripts/resource_templates.py check --config-dir <MAA_CONFIG_DIR>
```

`--resource-dir` 按当前运行的实际加载顺序重复传入。通过当前 maa-cli 的目录、配置和资源加载证据核实基础资源、客户端专用层、热更新层；不要把默认目录列表当作事实，不传用户 `config/resource`，不把其它客户端资源混进来。绑定不调用 MAA、不更新官方资源、不自动发现或启用资源层。加载层发生变化后重新核实并绑定。

绑定只修改已部署的受管节点的 `template`，覆盖商店购买结果/信用弹窗和奖励导航的官方模板派生项；保留动作、购买范围、分支、显式 ROI 与其他用户节点。支持模板数组；检查节点算法、缺失图片和同层重复定义，不能解析时拒绝猜测。节点必须与捆绑基线或上次绑定产物完整一致；用户改动冲突时全部不写。变更前备份原 `tasks.json`，本机 `policies/resource-templates.json` 保存资源层和已绑定节点，不提交私人路径。

完整日常和奖励扫描的预检已接入绑定检查：未绑定、官方模板变更、缺文件或部署节点变化时，在游戏操作前失败。更新官方资源后，先 `check`；确有模板变化且节点仍归组件管理时再 `bind`，随后 `daily_checks.py prepare` 和奖励预检。单独运行 Custom 前也调用 `check`，直接 `maa run` 不会自动调用这个 Python 检查。静态检查不证明业务已成功，也不能证明手写 ROI 或整个界面布局仍兼容。

2026-11-02 在 MaaCore 6.21.0 确认：旧购买结果模板已改为官方 `ItemObtainedIcon.png`，`Award` 支持新旧主页模板数组；`CrisisPopup` 的派生文件名缺口也由同一绑定处理。原失败留下的“获得物资”弹窗经绑定节点实际关闭，随后扫描到余额不足并正常结束，无模板错误；另一账号正常入口也完成了一次招聘许可购买及结果弹窗关闭。奖励导航与十档扫描在当前界面通过。并未把此前失败报告改写为成功；信用领取后的可选弹窗等未实际出现的状态仍未覆盖，不能据此声称完整矩阵通过。
