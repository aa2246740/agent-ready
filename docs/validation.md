# 验证范围

Round 4 在 56958d3 基础上验证 F1–F12。规则文件、criterion ID、rubric/schema 标识和计分保持不变；未调用新模型或 Factory 云服务。以下为 2026-10-19 实跑结果，0.1.0 初次交付记录另列在后。

- `GO_TEST_BINARY=/Volumes/Workspace/Scratch/agent-ready-validator-hbrl7ali/go/bin/go python3 -m unittest discover -s tests -v`：ran 380，failed 0，errors 0，skipped 0，170.885 秒。Go 为 `go version go1.27.2 darwin/arm64`，7 项原有真实 Go 子进程回归均执行。
- r3 的八个复现案例均得到预期结果：无行号 fix、环境/全局选项下的 JS list、标题含 Bun 标记的列表、Cargo package ID 通过；空列表拒绝；后台 capture 记 124；非法 cwd 在执行前报错。后者的旧脚本仍尝试对空 stdout 做 JSON 解码，因而脚本本身退出 1；应用已按预期返回 2 和 cwd 错误。
- 另以真实 Vitest 跑了 `--json`、`--filesOnly`、`--filesOnly --json` 三种列表，均退出 0 并接受。r2 的已捕获真实 Bun 执行证据仍被拒绝。capture 的真实子进程回归覆盖后台 EOF、TERM 忽略、超时清理及限时内完整输出。
- 重放 33 份已完成真实评估，最终 33/33 接受。原样在当前目标重放为 31/33；FX task-1、task-2 是旧提交，按当前目标提交时正确拒绝。随后在新的临时副本检出各自原提交，核对 head/fingerprint 与原记录相同，重绑副本元数据，并在新 cwd 重新采集唯一的 `unit_tests_runnable` pytest 收集证据，两份均接受。其他判断和证据未改；原输入、目标和 runs/*/state 均未写入。AS3 两个目录没有已完成的 evaluation，不计入分母。
- Local adapter 按 r3 的分割方式计算 UTF-8 字节，6233 → 6123，目标 ≤ 6144；含 `## Local adapter` 标记则为 6249 → 6139。全部原规则文字与其他提示词契约由公开套件核对。

开发中的第一轮全套测试为 380 项、1 failure、0 errors、0 skipped，183.107 秒，失败输出为 `'There is no per-application N/A' not found`。已恢复原句后运行上述最终套件。早期 capture 定向测试还发现 TERM 后未回收 shell 导致的 `PermissionError: [Errno 1] Operation not permitted`；回收 shell 后再发组信号已修复，21 项 capture 测试通过。失败日志与最终日志均保留在本轮临时目录，未将失败或跳过记为通过。

当前源码哈希、精确计数、日志哈希及历史记录见 [evidence/validation.json](../evidence/validation.json)。测试使用合成输入；真实评估重放验证的是存储与证据兼容，没有重跑模型判断。公开源码不提交私有评估、目标快照或研究脚本。

## 0.1.0 初次交付验证历史，2026-10-17

以下保留初次交付的记录，所述命令、计数、探针与打包结果均为历史，不计入 Round 4 的执行结果。

当时检查 0.1.0 的运行、计分、证据验证、MCP 提交、skill 安装、源码分发和报告历史。测试使用合成输入，不产生新的真实仓库评分；当时没有调用真实模型或 GLM。

四组指定验证在同一份固定源码上通过，0 失败、0 错误、0 跳过。全量测试发现 251 项，其中 7 项真实 Go 子进程回归均已执行。

| 命令 | 通过数 | 耗时 |
| --- | ---: | ---: |
| `python3 -m unittest discover -s tests -v` | 251 | 107.545 秒 |
| `TMPDIR=/tmp python3 -m unittest discover -s tests -v` | 251 | 102.966 秒 |
| `python3 -m unittest tests/test_subdirectory_cd.py` | 10 | 1.895 秒 |
| `pytest --import-mode=importlib` | 251 | 107.342 秒 |

以上命令均设置 `GO_TEST_BINARY`，指向外置盘临时目录中的官方 Go 1.27.2 darwin/arm64。工具链下载归档已核对官方 SHA-256，没有修改系统 Go 安装。复跑时需安装可用 Go，或设置 `GO_TEST_BINARY=/absolute/path/to/go`；未设置且本机没有 Go 时，7 项 Go 回归会跳过，不能算作执行通过。Python collection 回归使用环境内的 pytest；依赖清单见 `requirements-dev.txt`。

独立对抗探针 88/88 通过，覆盖普通 cwd 的环境与目录改写、五种 wrapper、非测试/只编译/帮助/任务列表命令、跨应用 Go/Maven/Gradle 归属、单应用目录越界、pytest 收集、MCP 完整分隔符伪造与 YAML frontmatter、写入故障回滚、Unicode、权限及安装回滚。84 条文件证据的成员验证实际只启动 2 个 Git 子进程。特殊 filter 名称、process filter 和父仓库触发子模块 filter 均有真实 Git 回归；新校验不重新裁决历史报告，旧本地报告目录仅在核对 checkout 根目录后读回。

源码分发包在独立临时目录离线构建、解包，核对 tools、scripts、skills、文档、规则和三份许可/来源声明；解包后的 MCP 模块 41 项测试通过。该检查验证本地源码分发，不代表新增远端 CI、目标环境或真实模型验收。

公开测试不依赖 Node.js、第三方 CLI 二进制或原始研究记录。两项原版函数差分测试及其他研究测试，共 174 项保留在维护工作区，未计入当时公开测试。仓库没有配置 GitHub Actions CI，不声称已有远端 CI 结果。

规则文件、84 条规则 ID、rubric/schema 标识和计分代码保持不变。运行文件、工具、测试和配置的当前 SHA-256、四组日志哈希、环境和精确计数见 [evidence/validation.json](../evidence/validation.json)。日志、独立探针脚本及其逐项结果保存在当时临时验证目录，不随公开源码提交；源码稳定性检查在刷新本文件与证据文件之前完成。历史研究验证不计作当时重新执行，来源与许可边界仍以 NOTICE.md 为准。
