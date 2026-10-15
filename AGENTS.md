# Agent instructions

先读 [README.md](README.md)、[docs/fidelity.md](docs/fidelity.md) 和 [NOTICE.md](NOTICE.md)。

- 保留 84 条规则、criterionId、报告 schema/rubric 标识、计分、跳过条件及历史读回兼容。`rules.json` 是运行依赖。
- 评估默认只读，实际修复须在用户授权范围内并通过必要验证。
- 不使用虚构证据、空测试、无效配置或关闭检查的方式增加分数。
- 开发检查：`python3 -m unittest discover -s tests -v`；实际 Go 回归需要可用 Go 或 `GO_TEST_BINARY`。不得将跳过当作已执行通过。
- 不提交凭据、私有报告、原始研究资料、第三方二进制或提取函数。公开目录不包含原版差分验证，不声称公开检查覆盖这些测试。
- 回复使用简体中文。保留真实来源说明，不将改名或公开可见性当作第三方授权。
