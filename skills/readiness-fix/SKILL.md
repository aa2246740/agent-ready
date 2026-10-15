---
name: readiness-fix
description: Make substantive, verified repository improvements from a readiness report, or follow the native no-report choice before fixing selected signals. Use for /readiness-fix or a readiness target.
---

# Readiness Fix

依据证据修复真实缺口。生成任务清单只是准备步骤，不能将计划、空文件或跳过检查当作完成修复。

## 1. 定位和选择范围

从**本 SKILL.md 所在目录**定位脚本：优先 `_runtime/scripts/readiness.py`（自包含安装包），否则 `../../scripts/readiness.py`（源码仓库），解析为绝对路径 `$READINESS_CLI`；目标 Git 根目录为 `$READINESS_TARGET`，目标外的本地报告目录为 `$READINESS_STATE`。读取目标的 agent 指令、当前改动以及对应最新报告，确认报告属于该目标且内容仍适用。

有报告时读取并重新核对失败项；报告过时不代表其中的失败结论仍然成立。没有报告时，脚本保留原生选择：“先生成完整报告，再修复失败信号”或“跳过报告，直接选择信号修复”。已有用户选择时直接继承。生成的任务包含完整报告评估指令；也可参照同级 `../readiness-report/SKILL.md`。前一路完成并存储报告后，有原始请求就语义匹配其失败项，无原始请求就直接展示失败信号供选择，不额外强制重新选 category。后一路有请求则直接探索对应缺口并修复，无请求则先选 category、再单选 signal，不能声称未测量的等级。批量 `--all` 或数值 `--target-level` 是本地扩展，会先建立完整基线报告。

选择遵循已有用户意图：

- 指定 ID、名称或自然语言问题：语义匹配 rubric ID，逐个处理匹配的未通过项；已通过或无法匹配的请求要说明并跳过，即使同一请求中还包含需修复的项目，也不能静默丢掉已通过项。跳过/N/A 项没有被评为失败，应先重新核对其适用条件。`--signals` 是严格 ID 接口，未知 ID 返回错误；自然语言可保留在 `--instructions` 中交由 agent 语义匹配。
- 已有报告、仅调用 `/readiness-fix` 且没有已有范围：按原生交互先选**至少有一个失败项**的 category，再用一个问题选择该 category 中的一个失败 signal。每个选项包含 signal 名称和当前分子/分母，不将原生单选描述为多选。无报告且选择跳过报告时，可以从所有 catalog 类别选择；类别超过 10 个 signal 时，原生要求优先展示最有影响力或最常见的项目，最多 10 个，不自动扩展为轮流批量修复。宿主工具上限更小时遵守实际能力，说明其余候选，保留已有选择和授权。
- 已授权全部修复、明确目标等级或多项修复：继承范围直接继续，不重复要求逐个确认。`--all`、`--target-level` 和迭代复评是此实现的明确扩展，不伪称原生默认行为。

创建供 agent 执行的任务说明，以下按已确定范围择一运行；以实际子命令帮助为准：

```sh
python3 "$READINESS_CLI" fix "$READINESS_TARGET" --state-dir "$READINESS_STATE" --signals lint_config,type_check
python3 "$READINESS_CLI" fix "$READINESS_TARGET" --state-dir "$READINESS_STATE" --instructions "Fix linting and type checking; leave CI unchanged"
python3 "$READINESS_CLI" fix "$READINESS_TARGET" --state-dir "$READINESS_STATE" --all
python3 "$READINESS_CLI" fix "$READINESS_TARGET" --state-dir "$READINESS_STATE" --target-level 4
```

如脚本无 signal 参数时只显示可选范围，继续完成选择，不将其当作已执行修复。读取实际输出的任务 Markdown 和相应规则，之后**由 agent 进行修改**。

## 2. 实施并验证

1. 先查看失败证据、具体代码、已有约定和应用边界，判断适用性与根因。共享库不因被 workspace 列出就变成独立服务，也不能为提高分数给 CLI 硬塞无用途的生产设施。
2. 对每个选定 signal 做能够改善真实开发工作流的改动；保留用户修改，不重写无关模块。需要新的依赖或配置时使用项目现有工具，确保命令与配置实际可运行。
3. 运行与改动相关的验证：修 linter 要实际检查相关代码，新增测试要包含有意义的断言并运行相应测试，修改 CI 要校验其配置和可执行命令。修复阶段允许必要的真实测试；只读报告阶段仍遵守原生的有界 collection / 单测试命令契约。
4. 将无法完成的事项保留为明确阻碍。例如分支保护、GitHub 组织权限、外部服务配置、持续部署统计或真实事故演练可能无法靠提交文件完成；说明具体需要的权限、负责人操作或观察周期。已有授权及可用凭据可复用，敏感值不得写进报告。继续完成不受阻碍的本地工作。

禁止通过以下方式提高分数：

- 空测试、没有有意义断言的测试、占位文档或不会执行的配置。
- 技术上勉强满足规则、却没有实际价值的最小实现或无意义改动。
- 关闭 lint / type-check / security 规则、添加全局 skip / `@ts-nocheck`、伪造 CI 和测试成功记录。
- 将原本失败或未知的 signal 改成未经 rubric 允许的 N/A，删除应用、改小分母或改写 rubric。
- 将仅在 README 中描述的外部设置当作已经生效，或把尚不存在的部署/恢复数据写成完成。

## 3. 重新评估直到完成或遇到真实阻碍

每批改动通过相关验证后，选择**新的目标外工作目录** `$READINESS_WORK_NEXT`，重新生成模板和快照：

```sh
python3 "$READINESS_CLI" prepare "$READINESS_TARGET" --output "$READINESS_WORK_NEXT" --state-dir "$READINESS_STATE"
# 依据新工作区重新完成全部 criterion；只复用仍然有效并已核对的证据。
python3 "$READINESS_CLI" report "$READINESS_TARGET" --input "$READINESS_WORK_NEXT/evaluation.json" --state-dir "$READINESS_STATE"
```

不要只把旧报告中的失败分子改为 1；新分子必须由当前快照的实际证据支持。修改可能引入回归，复评必须覆盖全部 rubric。

指定 `--target-level` 时，以新有效报告的计算结果判断是否达到目标；未达到且有已授权、可落实的缺口就继续修复。指定 `--all` 时完成所有适用失败项或逐项记录外部阻碍。没有新证据或可执行进展时不要无限重试，也不要把“目标无法保证”改写成成功。

最终汇报：实际变更、已运行验证、修复前后分数与等级、有效报告路径，以及未完成项和原因。明确区别“产生任务清单”“实施了修改”“验证通过”和“复评已通过”；只有最后一步能够证明相应 readiness 结果改善。

## 本地适配

原生 `/readiness-fix` 使用 Factory 最近报告，默认 category → 单 signal 交互，并要求实质改进和验证。本项目以本地有效报告和可审查任务 Markdown 替代 Factory 工具；当前 agent 负责执行，Python CLI 自身不会替用户修代码。完整修复、目标等级循环和强制修复后复评是扩展功能。不能保证任何仓库都达到 L5，特别是取决于外部治理或历史运行数据的条目；应给出真实达成状态和可执行的剩余要求。
