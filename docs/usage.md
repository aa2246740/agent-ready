# 使用与报告历史

本工具需要 Python 3.10+、Git。宿主 agent 提供模型、文件工具、命令工具及获授权后的代码编辑能力。单独运行 CLI 不会替你阅读项目或凭空产生判断。

## 评估

最简单的方法是让 agent 阅读 [agent-readiness skill](../skills/agent-readiness/SKILL.md)，给出目标仓库的绝对路径、输出位置，并说明“只评估，不修复”。三个 skill 可以直接从源码使用，也可以通过 `tools/install_skills.py --destination <新技能目录>` 安装为自包含目录。

安装目录用于本地使用；需要转交这些目录时，应同时保留 [LICENSE](../LICENSE)、[LICENSE-SCOPE.md](../LICENSE-SCOPE.md) 和 [NOTICE.md](../NOTICE.md)。安装器不会另行授予第三方内容的再分发权利。

手动控制时，先运行：

```sh
python3 scripts/readiness.py prepare /path/to/project \
  --output /tmp/project-audit-1 --state-dir /tmp/project-reports
```

然后由 agent 阅读输出的 `assessment.md`、`snapshot.json` 和 `evaluation.json`，发现独立应用，依据完整规则完成全部 84 项和证据。初始 `UNASSESSED` 不能提交。完成后运行：

```sh
python3 scripts/readiness.py report /path/to/project \
  --input /tmp/project-audit-1/evaluation.json --state-dir /tmp/project-reports
python3 scripts/readiness.py show /path/to/project --state-dir /tmp/project-reports
python3 scripts/readiness.py show /path/to/project --state-dir /tmp/project-reports --json
```

正式报告校验仓库身份、HEAD、工作区内容指纹、应用目录、84 个精确规则键、分母、分子、允许跳过条件及证据。目标变化后须重新 prepare；不能改快照字段来掩盖变化。

## 修复与重评

```sh
python3 scripts/readiness.py fix /path/to/project \
  --signals lint_config,readme --state-dir /tmp/project-reports
```

`fix` 生成任务，agent 才是实际实施者。可以用 `--all` 或 `--target-level 4` 表达已授权范围；不带选择时提供类别/信号选择。没有旧报告时保留“先评估”或“直接修复所选信号”流程，批量和目标等级流程需要完整基线。

修改后先独立验证，再用新的 task 目录完整 prepare/评估/report。存储会追加新报告并计算与旧报告的变化；不覆盖旧结论。外部权限、历史记录和实际运维设施无法靠添加文件满足时，应保留具体限制。

## 存储位置

显式 `--state-dir` 最便于管理。默认使用 `AGENT_READINESS_STATE_DIR`；未设置时为 `$XDG_STATE_HOME/agent-readiness`，再未设置则为 `~/.local/state/agent-readiness`。状态和任务目录都必须位于目标之外。

报告可能包含目标文件名及命令摘录，应按该项目的保密要求保存。本工具不上传目标仓库，但宿主模型可能接收你允许它读取的内容；模型服务的隐私设置由宿主管理。

## 受限模型会话

`tools/blind_submit_mcp.py` 提供只接受完整 JSON 的受限提交工具，由控制器指定目标、模板、状态、输出、读取轨迹和任务预算。它不暴露任意写路径、命令或网络访问。需要部署时应沿用真实工具权限隔离，不只写 prompt 禁止越界。

提交错误会在当前会话内返回，修正后重提。任务配置可将 `deadlineEpoch`、`maxSubmissions` 设为 null；不存在所有项目固定 20 分钟或 3 次提交的评分限制。只读会话只能引用其实际读取的文件，不能伪造不可执行的命令证据。
