---
name: agent-readiness
description: Evaluate a Git repository against the versioned 84-criterion readiness rubric, produce an evidence-based local report, and route authorized remediation to readiness-fix. Use for /agent-readiness or requests to assess or make a repository agent-ready.
---

# Agent Readiness

这是本项目的总入口。已检查的 Droid 0.240.0 command registry 有 `/readiness-report` 和 `/readiness-fix`，未找到 `/agent-readiness`；本 skill 将后者提供为便携别名，不声称它是原生提取出的第三条命令。

## 定位工具与目标

从**本 SKILL.md 所在目录**定位工具：若 `_runtime/scripts/readiness.py` 存在则使用它（安装包）；否则使用 `../../scripts/readiness.py`（源码仓库）。解析为绝对路径，记为 `$READINESS_CLI`。不要相对用户当前工作目录寻找脚本，也不要把它误认为被评估仓库的脚本。使用 Python 3；脚本无需第三方依赖。参数以实际 `python3 "$READINESS_CLI" --help` 和各子命令帮助为准。

确定用户指定的 Git 仓库根目录，记为 `$READINESS_TARGET`。在目标仓库之外选择工作目录 `$READINESS_WORK` 和报告存储目录 `$READINESS_STATE`，避免报告文件污染被评估快照。同一轮审计固定目标、rubric 版本和应用集合；保留用户已有修改。

## 执行路由

1. 用户只要求检查、评分或报告：阅读同级 `../readiness-report/SKILL.md`，完成只读评估并生成实际报告。
2. 用户要求修复、补齐缺口或让仓库 agent-ready：阅读 `../readiness-fix/SKILL.md`，在已授权范围内落实修复并复评。有报告时重新核对失败项，语义匹配用户给出的 ID、名称或自然语言问题；说明并跳过已经通过的请求。无报告时保留“先评估”或“直接检查所选信号并修复”的原生分支，生成的任务内附完整评估指令。批量和目标等级请求先建立完整基线。已有明确选择和范围时直接继承，不重复询问。
3. 未指定等级的报告只展示实测等级；“修到 Level 4”是明确的目标策略，不能把所有“agent-ready”请求自动解释为改写全部运维或组织策略。对宽泛修复请求，先处理已经识别、适用且可验证的缺口，继续直到授权范围完成或具体外部前置条件阻塞。

常用命令：

```sh
python3 "$READINESS_CLI" prepare "$READINESS_TARGET" --output "$READINESS_WORK" --state-dir "$READINESS_STATE"
# 按 assessment.md 完成 evaluation.json，保留真实文件与命令证据。
python3 "$READINESS_CLI" report "$READINESS_TARGET" --input "$READINESS_WORK/evaluation.json" --state-dir "$READINESS_STATE"
python3 "$READINESS_CLI" fix "$READINESS_TARGET" --state-dir "$READINESS_STATE" --all
```

最后一条命令输出供 agent 执行的修复任务。只有 agent 完成代码或配置修改、运行相关验证，并重新 `prepare` / `report` 后，才能报告已修复或已达到目标等级。

## 忠实性边界

- 本项目固定参照 Droid 0.240.0 的规则和命令行为；后续 Droid 版本可能变化。
- 原生评分按所有非跳过 signal 的 `numerator / denominator` 求等权平均。20%、40%、60%、80% 是等级分界；条目的 `level` 字段是分类元数据，不能套用社区实现的逐级 80% 门槛。
- `prepare` 不自动断言规则通过，`report` 验证已填写的证据报告，`fix` 不自动修改仓库。评估与实际修复由运行本 skill 的 agent 完成。
- 原生报告使用 Factory 工具及服务存储；此实现使用目标仓库之外的本地 JSON / Markdown，不上传、不伪造 Factory 报告 URL。离线本地存储、严格证据校验、`--all` 和目标等级循环是本实现适配或扩展。
- 无需 Factory 账户、Droid 安装或 airgap 环境变量即可运行本工具；模型能力由当前 agent 提供。不要将本实现描述为已经验证了 Factory 服务端或 Droid BYOK 的端到端兼容性。
