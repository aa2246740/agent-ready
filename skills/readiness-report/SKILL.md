---
name: readiness-report
description: Perform a read-only Git repository assessment using the versioned 84-criterion readiness rubric and persist a complete, evidence-backed local JSON and Markdown report. Use for /readiness-report or agent-readiness audits.
---

# Readiness Report

对目标做一次有证据的只读审计，复现固定版本的 rubric、应用计数和评分。

## 步骤

1. 按 `../agent-readiness/SKILL.md` 定位 `READINESS_CLI`，确定 `READINESS_TARGET`、目标之外的新任务目录 `READINESS_WORK` 和报告目录 `READINESS_STATE`。读目标的 agent 指令和现有改动；默认评估当前工作树，不切分支、不丢弃修改。
2. 准备任务：

```sh
python3 "$READINESS_CLI" prepare "$READINESS_TARGET" --output "$READINESS_WORK" --state-dir "$READINESS_STATE"
```

3. 通读生成的 `assessment.md`，它是唯一依据：Phase 1–5 给出扫描、应用发现、84 条规则和计分，末尾 “Local adapter” 一节给出 `evaluation.json` 字段、证据格式、cwd 规则、条件证据和 report 命令。
4. 完成 `evaluation.json` 后提交：

```sh
python3 "$READINESS_CLI" report "$READINESS_TARGET" --input "$READINESS_WORK/evaluation.json" --state-dir "$READINESS_STATE"
```

`report` 一次列出全部问题。逐条修正后重跑，直到存储成功；失败时不要宣称已有报告。
5. 存储成功后按 `# Level`、`# Applications`、`# Criteria`、`# Action Items` 汇报（有历史报告加 `# Changes Since Last Report`），链接实际生成的 JSON/Markdown，并说明跳过项和证据局限。

## 要点

- 先完成应用发现并输出 `APPLICATIONS_IDENTIFIED: N`，再评分；`.git`、依赖和生成目录不计入，递归列表少于 200 条。
- `null` 只用于标有 `[Skippable]`、且按其自身文字不适用的条目（跳过条件、PREREQUISITE 未通过、规则列出的 N/A），并在 rationale 说明是哪一种。不要把适用的失败、规则未列为跳过理由的权限缺失、未评估项改成跳过。
- 应用级条目的分子是通过的应用数，分母是 N；没有“单个应用 N/A”。分母留 `null` 即由 `report` 补齐。
- 证据要真实：命令保留实际文本、退出码、相关输出和真正的执行目录（cwd 规则见 `assessment.md`）；不记录凭据或环境转储。
- 运行类证据用目标自己的入口；评估阶段不安装运行时、不跑完整测试，目标保持只读。
- 有历史报告时以旧应用集合为预期，逐项重新核对，只在证据变化时改分并解释。

## 与原生的差别

由当前 agent 执行审计，本地 CLI 负责模板、校验、计分和存储；Factory 云报告由本地文件替代。差异清单见仓库 `docs/fidelity.md`。
