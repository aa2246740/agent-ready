---
name: readiness-report
description: Perform a read-only Git repository assessment using the versioned 84-criterion readiness rubric and persist a complete, evidence-backed local JSON and Markdown report. Use for /readiness-report or agent-readiness audits.
---

# Readiness Report

运行一次有证据的只读审计。目标是复现固定版本的 rubric、应用计数和评分，不是凭静态文件数量估算“看起来不错”的等级。

## 准备

从**本 SKILL.md 所在目录**定位脚本：优先 `_runtime/scripts/readiness.py`（自包含安装包），否则 `../../scripts/readiness.py`（源码仓库），解析为绝对路径 `$READINESS_CLI`，不要依赖调用时的工作目录。将目标 Git 仓库根目录记为 `$READINESS_TARGET`，目标外的新工作目录记为 `$READINESS_WORK`，本地报告目录记为 `$READINESS_STATE`。读取目标适用的 agent 指令及用户约束，并检查已有改动。

`$READINESS_CLI` 是准备、校验和保存评估的宿主工具，不是目标应用的启动入口。实际 QA 尝试必须从 `$READINESS_TARGET` 的文档和代码定位并运行该快照的应用，或运行由该快照构建的产物；即使目标也是 readiness CLI，也不能用宿主工具或另一个 checkout 的成功替代目标运行证据。外部 fixture 可以提供输入或文档允许的 mock 服务。`interactive_qa_exists` 仍只查文档、不运行应用；`interactive_qa_runnable` 在环境确实无法运行时仍按原生完整文档 fallback 判断。

```sh
python3 "$READINESS_CLI" prepare "$READINESS_TARGET" --output "$READINESS_WORK" --state-dir "$READINESS_STATE"
```

读取生成的 `assessment.md`、`evaluation.json` 和 `snapshot.json`。生成的完整 rubric 及模板是本轮操作依据；保留 `repository` 快照元数据，不手工伪造 commit、工作区状态或版本。默认评估当前工作区，不自行切换分支或丢弃修改；报告应明确当前分支和未提交变更。这是本地实现对原生“默认分支”假设的显式适配。

## 1. 有边界的仓库扫描

只在目标 Git 根目录以内搜索被评估代码。从子目录发起时仍覆盖根目录以内的上级目录。排除 `.git`、依赖目录和生成输出，逐步按应用或模块缩小范围；每次递归源码列表或搜索结果必须少于 200 条。不要遍历仓库外目录或跟随软链接取得目标外源码。目标外的工具、模板和输出目录不属于被评估证据。

先读语言清单、锁文件、构建脚本、CI、文档和测试目录，记录发现的主要语言：例如 JS/TS 的 package.json/tsconfig、Python 的 pyproject.toml/setup.py/requirements.txt、Rust 的 Cargo.toml、Go 的 go.mod、Java 的 Maven/Gradle 文件、Ruby 的 Gemfile/gemspec。Java 项目先读根及模块构建文件、wrapper、source/test 结构和 CI，再做有界源码搜索；排除 `target`、`out`、`.gradle`、`.m2`，不要无界枚举所有 `.java` 文件。这些限制在后续每个 criterion 的评估期间继续有效。

## 2. 先固定应用集合

先完成应用发现，再开始任何 criterion 评分。应用是具有独立部署生命周期、能独立构建运行、直接为最终用户或其他系统提供功能的**目录**，不是文件，也不等同于每个 workspace package。关键判断：如果移到独立仓库，这个目录是否仍能工作？共享库和工具包、共享基础设施的示例不单独计数；Maven/Gradle 模块只有存在独立运行**或**部署生命周期的证据时才可能单独计为应用。多模块 Java 库即使有多种根构建入口，通常也只计根应用。单一库或 showcase/tutorial 集合通常算一个根应用；找不到独立应用时也将 `.` 计为一个。

原生所说的独立部署单元可以与其他代码分别部署；monorepo 按符合定义的独立服务计数。原生用途示例包括“面向用户界面的主 Next.js 应用”和“本地开发 CLI 工具”。CLI 的用途示例不免除独立生命周期检查，也不能仅凭目录名固定应用数量。

根据 README、package 描述、目录用途和 scripts 为每个应用写简短说明。在评分之前输出这个发现检查点，然后写入模板：

```text
APPLICATIONS_IDENTIFIED: N

Applications:
1. [仓库相对目录] - [简短用途]
...
```

把每个应用的仓库相对路径和具体用途写入模板的 `apps`，先固定总数 N 再开始评分：

- repository scope：分母固定为 1，分子为 0 或 1。
- application scope：所有条目分母固定为 N，分子为实际通过的应用数。不能只保留通过应用或对每条规则另缩分母。
- `null` 仅用于 rubric 明确标记可跳过、且满足其跳过条件的整个条目；必须提供依据。不能把未评估、无权限、命令报错或不利结果随意转成 N/A。

## 3. 逐条执行 rubric

填写 `evaluation.json` 中**全部原始 criterion ID**，不新增、更名、删除条目。每条均须具体 `rationale` 和真实证据；解释不超过 500 个 UTF-16 code units（与原生 JavaScript 字符串长度一致，部分 emoji 占两个单位）。模板初始 `UNASSESSED` 不代表失败或跳过，必须完成评估后替换。

文件证据用仓库相对路径、实际行号和解释；命令证据保留实际命令、工作目录、退出码和相关输出。多应用条目应解释各应用结论以及计数，足以复核每个通过和未通过的应用。按原生偏好，能够用存在性或配置检查判断时不擅自升级为深层语义审计；但需要运行证据的规则不能用文件存在替代。证据模糊时判失败，除非该条明确允许且实际满足跳过条件。

命令证据的 `cwd` 是执行工具实际启动该条完整命令的目录，换算为目标根目录内的相对路径；只有真实从目标根目录启动时才写 `.`。`command` 保留实际执行的完整 shell 内容，包括 `cd`、必要的非秘密环境赋值和控制流，保留真实退出码与相关输出；后续 `cd` 不会追溯改变初始 `cwd`，也不能用末尾 `echo` / `ls` 的成功退出代替应用状态。

若命令实际从目标外启动，当前本地 schema 无法表达该 `cwd`，保留原始轨迹并说明这一限制，不能把字段改成 `.`、删掉 `cd`，或只在 `output` 注记真实目录来通过验证。需要补充证据时，实际重新执行一次有界命令，并让执行工具的工作目录真实位于目标内；外部 fixture 使用绝对路径，需要切换目录则在 `command` 中完整保留。`unit_tests_runnable` 的直接、有界命令限制不因此放宽。

外部 fixture 使用 `$READINESS_WORK` 下通过 `mktemp -d` / `tempfile.mkdtemp` 唯一分配的新目录，记录实际路径。不要删除或重用 `/tmp/qa-demo` 这样的固定全局目录。区分 fixture 输入与被运行的目标应用，保持目标仓库只读。

遇到 GitHub 设置、组织权限、近期部署、事故、CI 时长或其他外部指标，按规则检查已有可用访问与证据。缺少访问时记录具体失败命令及局限；只有规则明确允许且满足条件才可跳过。描述尚未验证的控制，不能称其已开启。不要读取、打印或保存凭据值。

评估阶段不修改目标，只运行对应 criterion 要求的有界 listing、collection 或测试命令；不安装缺失语言运行时，也不运行完整测试套件。运行时缺失与仓库质量失败分开解释，严格遵守对应 criterion 的回退或跳过条件。

### `unit_tests_runnable` 的命令契约

以下是固定 rubric 的关键约束，完整规则仍以生成的 `assessment.md` 为准：

- 使用一次直接、有界的命令；通过执行工具的超时能力限制运行。不要在命令中加 shell `timeout`、重定向 `2>&1`、管道、`head`、`tail` 或 `tee` 来截取输出。保留 runner 的真实退出码。
- TS/JS 先检查 `package.json`，只收集或列举测试，不执行测试。Jest 可用 `npm test -- --listTests`；Vitest 可用 `npx --no-install vitest list`。确认输出列出测试，且没有实际测试 PASS/FAIL 或执行数量。向任意 package script 追加单文件参数不保证有界。
- Python 从已找到的样本选择一个实际测试文件，运行 `python -m pytest --collect-only <test-file>`，成功收集即可。如果 uv/tox 不可用，但 Python/pytest 已安装，采用直接命令。
- Java 选择一个实际测试类。Gradle 使用 `./gradlew --rerun-tasks test --tests <TestClass>`，必要时将 `test` 换为 `<module>:test`；Maven 使用 `./mvnw -Dtest=<TestClass> test`，或 `./mvnw -f <module>/pom.xml -Dtest=<TestClass> test`。只有命令退出零且选定测试真正执行才通过；UP-TO-DATE、FROM-CACHE、SKIPPED、NO-SOURCE、dry-run、列任务和只编译都不足以通过。发现明显 JDK 环境问题时修正一次并重试，仍缺少所需 JDK 才按规则跳过。
- 其他生态仅在语言运行时不可用时按该规则跳过；普通 shell 或构建失败不能伪装为缺少运行时。

如果当前执行工具无法满足有界执行契约，记录工具限制，不伪造成功的命令证据。

### 存在历史报告时

读取 `previous-report.json` 以及任务内的历史参考，保留其日期、commit 和已记录的本地变更状态；未知元数据保持未知。以旧应用集合为预期，仅在仓库结构确实改变时增删，并解释原因。重新探索仓库、复核**每个** signal；旧 rationale 只提示去哪里找，不决定本轮答案。

证据仍存在或仍缺失时保持原分数并更新说明；证据变化时在对应 criterion 的 rationale 明确写出原结果、现结果和变化依据。未变的文件或配置应得到相同结论，不能产生没有依据的判断漂移。最终变化摘要只列发生变化的 criterion 或 application，省略未变化项。

## 4. 验证和生成报告

当前本地版本对 `dependency_update_automation` 和 `alerting_configured` 的通过结论增加逐条件证据契约，完整字段与例子在 `prepare` 生成的 `assessment.md` 中。升级自动化必须分别举证配置和实际创建的升级 PR；不能只引用 Renovate/Dependabot 配置。告警必须分别举证告警规则、已接通的自动触发和通知出口；手动单次发送成功不能代替自动触发。为相应文件/命令证据添加 `support`（condition、原文 quote、具体 facts）；文件 quote 必须匹配实际行号，条件间的 updater/handler/notifier 必须对应。使用盲评提交器时，quote 还必须出现在本会话实际读回的内容中。缺项会在提交时返回，不自动补分。

这些是本地证据约束，不改原生规则和计分；不能证明所有引用都来自真实外部运行，agent 仍负责语义核对。不要把同样的运行要求扩展到仅要求配置/文档的规则：自定义永久 kill switch、文档化 flag 清理流程、审批/手动触发后的自动发布流程，以及已接入的页面分析各自按原规则判断。历史报告按原契约读回；新提交按新契约检查，不改旧报告凑通过。

检查每条应用 scope 分母均为 N、仓库 scope 分母均为 1，分子不大于分母，所有通过结论有支持证据，并确认审计命令未意外修改目标。只撤销本轮工具引入的意外变化；保留用户修改。若目标已发生实际变更，重新准备快照并针对新快照评估，不篡改旧元数据。

在本地扩展字段 `actionItems` 中填写 2–3 个高影响、具体且可实现的下一步，每项形如 `{"criterionId":"失败项的准确ID","action":"针对本仓库的具体改进","reason":"为何优先处理，引用已有证据"}`。只能引用已经评为失败的 criterion（包括部分应用未通过），不能引用通过或跳过项。失败项不足两个时按实际数量填写；没有失败项时保留空数组。模板中的初始 `actionItems: []` 不是存在失败时已经完成的建议，不应以机械抄写规则描述或虚构完成工作替代真实分析。此字段仅保存在本地，不改变原生 report 内的 criterion 结构。

```sh
python3 "$READINESS_CLI" report "$READINESS_TARGET" --input "$READINESS_WORK/evaluation.json" --state-dir "$READINESS_STATE"
```

修正验证器指出的问题直到报告成功存储。验证失败时不得宣称已生成有效报告。

由工具从确切提交的结果计算：`score = 100 × mean(numerator / denominator)`，排除合法 `null`，每个 signal 等权；不是汇总分子除以汇总分母。等级边界为 L1 `[0,20)`、L2 `[20,40)`、L3 `[40,60)`、L4 `[60,80)`、L5 `[80,100]`。条目所属 `level` 仅用于分类，不施加“每级 80%”门槛，也没有 Level 6。

存储成功后按 `# Level`、`# Applications`、`# Criteria`、`# Action Items` 提供简明报告；criteria 按 catalog 类别组织，给出分子/分母和理由，特别说明失败项。存在历史报告时增加 `# Changes Since Last Report`。链接实际生成的本地 JSON / Markdown，给出 2–3 个具体、可实现且影响较大的下一步，以及跳过条件和证据局限。未达到阈值就如实报告；不自动进入修改阶段。

## 与原生的差别

此流程使用原生 0.240.0 的 rubric 和评分语义，但由当前 agent 执行审计；本地 Python 工具处理模板、校验、计算和持久化。Factory 的 `store_agent_readiness_report` 及云端报告链接在这里由本地文件替代，额外保存可审查证据和快照。没有调用 Factory 服务时，不声称云端报告创建成功或端到端输出已与真实服务验证一致。
