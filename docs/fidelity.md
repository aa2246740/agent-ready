# 功能与边界

## 已保留的运行功能

- 三个 skill：agent-readiness、readiness-report、readiness-fix；CLI 提供 catalog、prepare、report、show、fix、score。
- 84 条规则、9 类、5 个等级；44 条 repository scope、40 条 application scope，30 条可按明确条件跳过。
- 非跳过条目的 `numerator / denominator` 等权平均；20/40/60/80 为 Level 2–5 下界，规则分类等级不是逐级解锁条件。
- 完整输入、快照、证据和逐条件校验，本地 JSON/Markdown 报告、修复任务、重评与历史。
- 自包含 skill 安装器和受限 MCP 提交工具；宿主提供实际模型、读取、命令和获授权的编辑能力。

发行版本为 0.1.0，规则数据文件为 `agent_readiness/data/rules.json`。规则内容、`droid-0.240.0` rubric 标识和报告 schema 保持既有值，版本编号重置不会转换或删掉旧报告。

## 本地实现与原版的差异

“与原生兼容”只指规则文本、应用计数、评分公式和等级边界。以下各处本实现更严格或做法不同，使用报告时应知道：

| 位置 | 原版 0.240.0 | 本地实现 |
| --- | --- | --- |
| 报告存储 | Factory 工具写入云端并给出 dashboard URL | 目标之外的本地 JSON/Markdown，不上传，不生成 Factory URL |
| 评估对象 | 默认分支 | 当前工作树，含未提交与未跟踪的未忽略文件；快照指纹必须与提交时一致 |
| 证据 | 报告只含分子、分母、理由 | 每条规则至少一条文件或命令证据，校验器检查结构，不执行命令、不证明语义真伪 |
| 命令证据 `cwd` | 无此字段 | 目标相对路径或绝对路径均可；必须是命令实际运行的目录，保留 `cd` |
| 分母 | 工具 schema 要求显式整数 | `prepare` 模板中应用级为 `null`，`report` 补成 N；写错的非空分母算错误 |
| 校验反馈 | schema 拒绝整份报告 | 一次列出全部问题（规则、证据序号、字段）和总数 |
| `dependency_update_automation`、`alerting_configured` | 按模型判断 | 通过需要逐条件的 `support` 证据，见[条件证据](condition-evidence.md)；其余规则不增加这类约束 |
| `unit_tests_runnable` | 命令契约只写在提示里 | 对通过证据做本地检查：退出码 0、`timeoutSeconds`、非空输出、单条直接命令、runner 在目标内、PYTHONPATH 不指向目标外；仅作用于该条证据 |
| 其他生态的测试命令 | 提示只给出 TS/JS、Python、Java 示例 | Go、Rust、.NET、ctest 等接受有界执行或能列出至少一个测试的 list 模式；仅编译、help、dry-run、零测试不通过。这是本地解释 |
| 规则前置关系 | 仅提示文字 | 校验器机械检查 `interactive_qa_runnable` 须不超过 `interactive_qa_exists`、`agents_md_validation` 须有 `agents_md`；`dead_feature_flag_detection` 的 PREREQUISITE 仍由 agent 判断并说明 |
| 跳过 | 仅 `[Skippable]` 条目可为 `null` | 同；另外 `monorepo_tooling` 在多应用时不得跳过 |
| 应用级 N/A | 无逐应用 N/A，分母固定为 N | 同原版，无差异 |
| `actionItems` | 只在汇报文字中给出 | 额外的可选本地字段，仅引用失败条目 |
| 修复流程 | `/readiness-fix` 的 category → signal 选择 | 保留；`--all`、`--target-level` 与强制复评是本地扩展 |

## 本地替代与限制

本地文件替代第三方云报告存储。生成修复任务不等于完成修改；需由 agent 实施、验证和重评。当前工作树而非固定默认分支是本地评估输入，准备后变化必须重新绑定快照。

模型可能对应用边界、继承配置和证据语义作出不同判断。引用与哈希不认证所有外部记录，也不证明任意代码语义。缺少外部权限或运行历史时须如实记录，不能靠添加配置伪造实际运行。

不承诺全部输入穷举、随机模型输出一致或任意仓库均能满分。官方云服务、账号同步、Mission、整套第三方 CLI 不在交付范围。复杂 Gradle 分支、所有交互选择与任意并发/文件系统故障没有穷举。

## 公开目录与维护材料

公开目录保留运行功能及普通回归测试，排除上游提取函数、原版差分测试、二进制探测/批量对照/研究档案导入工具及其研究测试。这些不是用户运行三个 skill 的依赖；它们在原维护工作区保留。公开测试不能独立重现全部历史研究验证。

新仓库采用独立根提交，不引入旧 `.git`、旧分支或旧报告。但规则及部分指令的上游来源仍需按 [NOTICE](../NOTICE.md) 理解；新历史不表示内容已获得新的许可。
