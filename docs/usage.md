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

然后由 agent 阅读输出的 `assessment.md`（唯一依据，按原生 Phase 1–5 组织，末尾 “Local adapter” 说明本地字段）、`snapshot.json` 和 `evaluation.json`，发现独立应用，完成全部 84 项和证据。初始 `UNASSESSED` 不能提交。完成后运行：

```sh
python3 scripts/readiness.py report /path/to/project \
  --input /tmp/project-audit-1/evaluation.json --state-dir /tmp/project-reports
python3 scripts/readiness.py show /path/to/project --state-dir /tmp/project-reports
python3 scripts/readiness.py show /path/to/project --state-dir /tmp/project-reports --json
```

正式报告校验仓库身份、HEAD、工作区内容指纹、应用目录、84 个精确规则键、分母、分子、允许跳过条件及证据。`report` 一次列出全部问题（规则、证据序号、字段）和总数，全部修正后重跑即可。目标变化后须重新 prepare；不能改快照字段来掩盖变化。

### 填写要点

- **分母**：模板中应用级条目的分母为 `null`，`report` 按应用数 N 补齐；仓库级为 1。手写的非空分母必须正确。跳过的条目（分子 `null`）分母同样补成 N。
- **应用级 N/A**：与原版一致，分子是通过的应用数，分母固定为 N，没有逐应用 N/A；`null` 只能整条跳过，且仅用于标有 `[Skippable]`、按其自身文字不适用的条目。
- **命令证据的 `cwd`**：填命令实际运行的目录，目标相对路径（`.` 为目标根）或绝对路径均可。宿主 shell 从目标之外启动时，记录那个绝对目录，并在 `command` 里保留 `cd /abs/target && ...`。
- **输出**：`output` 是真实输出的相关摘录；`unit_tests_runnable` 需包含 runner 自己的列出、收集或结果行。该条的命令限制（不用管道、`2>&1`、`head` 等）只适用于它自己的通过证据，`documentation_freshness` 等规则照常记录原生命令。
- **环境**：把构建输出放到目标之外（如 `CARGO_TARGET_DIR`），设置 `PYTHONDONTWRITEBYTECODE=1`，保持目标只读。`gh --json` 输出被着色时用 `--jq` 或 `NO_COLOR=1 CLICOLOR_FORCE=0`。
- 与原版的差异见[范围与限制](fidelity.md)。

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
