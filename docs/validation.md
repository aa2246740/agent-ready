# 验证范围

本轮检查 0.1.0 的运行、计分、证据验证、MCP 提交、skill 安装、源码分发和报告历史。测试使用合成输入，不产生新的真实仓库评分；本轮没有调用真实模型或 GLM。

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

公开测试不依赖 Node.js、第三方 CLI 二进制或原始研究记录。两项原版函数差分测试及其他研究测试，共 174 项保留在维护工作区，未计入本轮公开测试。仓库没有配置 GitHub Actions CI，不声称已有远端 CI 结果。

规则文件、84 条规则 ID、rubric/schema 标识和计分代码保持不变。运行文件、工具、测试和配置的当前 SHA-256、四组日志哈希、环境和精确计数见 [evidence/validation.json](../evidence/validation.json)。日志、独立探针脚本及其逐项结果保存在本轮临时验证目录，不随公开源码提交；源码稳定性检查在刷新本文件与证据文件之前完成。历史研究验证不计作本轮重新执行，来源与许可边界仍以 NOTICE.md 为准。
