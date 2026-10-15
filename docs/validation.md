# 验证范围

本目录的检查针对 0.1.0 的运行、计分、证据验证、MCP 提交、skill 安装和报告历史。测试使用合成输入，不产生新的真实仓库评分；本轮不调用 GLM。

本次执行 **217 项测试，0 失败、0 错误、0 跳过**，耗时约 44 秒。包含安装后从独立目录运行 skill，以及报告保存、读回、修复任务和重评历史。检查结果与文件绑定见 [evidence/validation.json](../evidence/validation.json)。

```sh
python3 -m unittest discover -s tests -v
```

需要 Python、Git；Python collection 回归使用环境内的 pytest。实际 Go 回归需要可用 Go，或 `GO_TEST_BINARY=/absolute/path/to/go`。依赖清单在 `requirements-dev.txt`。缺少必要运行时导致跳过时，应如实报告跳过数量。

公开测试不依赖 Node.js、第三方 CLI 二进制或原始研究记录；两项原版函数差分测试及其他研究测试，共 174 项保留在维护工作区，未计入公开目录的 217 项通过数。仓库当前没有配置 GitHub Actions CI，不声称已有远端 CI 结果。

规则文件和运行代码与维护版本的内容绑定、测试计数及不包含旧 Git 历史的检查分别记录。版本号变为 0.1.0 仅用于新发行线，不能把历史验证说成本轮重新执行。部分真实模型路径有既有维护证据，但当前首次公开目录没有重新进行模型实验。
