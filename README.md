# Agent Ready

让 agent 对本地 Git 仓库进行有证据的评估、生成报告，并在授权后修复和重评。**0.1.0** 是本仓库的首次独立交付版本；运行功能来自已有实现，版本号重置不表示已经穷举所有输入。

支持 84 条规则、9 类、5 个等级，多应用计数、逐项证据、允许条件下的跳过、本地 JSON/Markdown 报告及历史。模型和文件/命令工具由宿主 agent 提供；CLI 负责准备、验证、计分与保存。

## 开始使用

需要 Python 3.10+、Git，以及可读取文件、执行命令的宿主 agent。运行时没有第三方 Python 包依赖。

```sh
git clone https://github.com/aa2246740/agent-ready.git
cd agent-ready
python3 scripts/readiness.py --version
```

把以下任务交给你的 agent，替换绝对路径：

> 读取 `/absolute/path/agent-ready/skills/agent-readiness/SKILL.md`，只读评估 `/absolute/path/my-project`。完整检查 84 条规则，保留文件和实际命令证据，把 JSON/Markdown 报告保存到目标仓库之外。本次不要修复代码。

三个入口：

| Skill | 功能 |
| --- | --- |
| [agent-readiness](skills/agent-readiness/SKILL.md) | 评估与已授权修复的总入口 |
| [readiness-report](skills/readiness-report/SKILL.md) | 只读评估并保存报告 |
| [readiness-fix](skills/readiness-fix/SKILL.md) | 实质修复、验证、重评 |

宿主支持技能目录时，可以安装三个自包含 skill；安装器不会复制测试或覆盖已有同名目录：

```sh
python3 tools/install_skills.py --destination /absolute/path/to/agent/skills
```

`/命令` 的注册方式取决于宿主。直接让 agent 读取 SKILL.md 即可使用本项目。

## CLI

```sh
python3 scripts/readiness.py prepare /path/to/project \
  --output /tmp/readiness-task-1 --state-dir /tmp/readiness-state
# Agent 按 assessment.md 完成全部 evaluation.json；prepare 不会自动评分。
# 可选：capture 在目标内运行一条命令，输出带真实 cwd/退出码的命令证据，并可原子追加到 evaluation.json。
python3 scripts/readiness.py capture --target /path/to/project --timeout 60 \
  --append-to /tmp/readiness-task-1/evaluation.json --criterion lint_config -- ruff check .
python3 scripts/readiness.py report /path/to/project \
  --input /tmp/readiness-task-1/evaluation.json --state-dir /tmp/readiness-state
python3 scripts/readiness.py show /path/to/project --state-dir /tmp/readiness-state
python3 scripts/readiness.py fix /path/to/project \
  --signals lint_config --state-dir /tmp/readiness-state
```

`report` 一次列出全部问题，修正后重跑；成功时默认打印简短摘要（等级、分数、通过/失败/跳过数、报告路径），`--json` 输出 `{report, markdown, summary}` 回执，含证据的完整评估在 `receipt.report` 指向的文件中。`capture` 只是本地便利工具（不写目标，校验不依赖它）。`fix` 生成任务，实际修改和验证由 agent 完成。重评使用新的目标外任务目录，追加历史，不覆盖旧结论。

使用细节见[运行与历史](docs/usage.md)、[模型接入](docs/byok.md)、[证据要求](docs/condition-evidence.md)、[本地实现与原版的差异](docs/fidelity.md)。规则文件名为 `rules.json`；原有规则和报告版本标识保留，以兼容既有输入。

## 开发与来源

```sh
python3 -m unittest discover -s tests -v
```

公开目录包含普通回归测试，不包含提取的原版函数、第三方二进制或原始研究包。测试范围和结果见[验证说明](docs/validation.md)。

本项目有权授权的自有代码采用 [Apache-2.0](LICENSE)；规则文本、适配提示词等第三方内容明确排除，见 [许可范围](LICENSE-SCOPE.md)和[来源说明](NOTICE.md)。本仓库并非所有文件都采用 Apache-2.0，公开可见性和新建 Git 历史不改变第三方授权状态。本项目独立维护，不隶属于 Factory AI，也不代表第三方背书。
