# 逐条件证据要求

本版修复两类“有配置就判通过”的误判，保持 84 条规则及计分不变。

| 规则 | 通过需要分别证明 |
| --- | --- |
| dependency_update_automation | updater 配置，以及该 updater 已为此仓库创建依赖升级 PR；不要求已合并、自动合并或部署 |
| alerting_configured | 告警规则、已接通的自动触发、通知出口；手动单次成功发送不等于自动运行 |

正分 evidence 需要 `support` 对象：`condition`、逐字 `quote` 和 `facts`。一个 record 对应一个条件，同一文件可分开引用。完整字段和指令由 prepare 自动生成到 assessment.md。

| criterion | condition | facts 必需键 |
| --- | --- | --- |
| dependency_update_automation | dependency_configuration | updater |
| dependency_update_automation | dependency_pull_request | updater、url、author、dependency |
| alerting_configured | alert_rule | handler、notifier |
| alerting_configured | automatic_trigger | kind、registration、handler |
| alerting_configured | alert_delivery | notifier |

`kind` 为 `schedule/event/process/service`，其余 facts 必须出现在 quote 中。文件 quote 须从实际引用行开始匹配；命令 quote 须存在于实际成功命令的记录输出。验证器不执行这些命令。

### facts 的匹配方式

- 比较不区分大小写。
- `updater` 还可以匹配所引用文件的路径：updater 名称常常只出现在文件名里，例如 `.github/dependabot.yml`、`renovate.json`、`.github/renovate.json5`、`.renovaterc`，此时无需在配置正文里重复写出它。
- PR 作者的常见形式 `dependabot[bot]`、`app/dependabot`、`renovate[bot]`、`app/renovate` 等同于 updater `dependabot` / `renovate`；两个 `updater` 事实仍要对应同一个工具。
- 其余 facts（`url`、`dependency`、`handler`、`notifier`、`registration` 等）仍须出现在 quote 中。
- 触发条件的启发式按所引文件类型识别注释，只把结构性的手动触发信号视为手动触发；它们只用来发现诚实的失误，不判断工程实践好坏。
- 错误信息会列出规则、条件、证据序号和缺失或不一致的 fact，全部问题一次返回。

两个 updater 要对应；触发 handler、规则 handler、规则 notifier、通知出口 notifier 要形成连接。告警是 application scope，多应用时 support 必须包含 `application`，按每个应用分别形成链，不能把一个应用的证明算成多个通过。共用设施可以覆盖多个应用，但必须有对应接线证据。

CLI 和 MCP 使用同一校验逻辑。MCP 还核对引用内容确实在当前会话工具读回中；读取过文件头不等于读过正文。缺项、引用错误以及全部超长/非法 rationale 会及时返回，模型在同一会话内纠正重提，不通过默认值补齐。

其他规则不因此加严：接线的永久 kill switch、文档化 flag 清理流程、人工审批/触发后的自动发布步骤、接入应用的页面分析，分别按原规则判断。没有强制增加开关数量、事件数量或历史成功部署要求。

这些检查仍不是任意代码语义或外部历史真实性证明。旧封存记录不重写；旧报告当作新评估提交时需要满足新契约。验证范围见[验证说明](validation.md)。
