# 模型接入

本地 CLI 不直接调用模型。使用 Codex、其他能读取 skill 的 agent，或你已经配置的 BYOK 宿主即可；模型 key 留在宿主的安全配置中，不提交到 Git。

固定兼容宿主的已观察路线是 Droid 0.240.0、`FACTORY_AIRGAP_ENABLED=1`、`customModels` 和真实 GLM。以下保留必要技术标识，不能全局替换名称，否则会改变配置或破坏旧报告兼容。它们不是对所有版本认证、订阅、遥测或可用权益的承诺；宿主的使用仍应符合适用许可和条款。来源见 [NOTICE](../NOTICE.md)。

| 配置 | 已验证值 |
| --- | --- |
| model | `glm-5.3-flash` |
| baseUrl | `https://open.bigmodel.cn/api/coding/paas/v4` |
| provider | `generic-chat-completion-api` |
| maxOutputTokens | `16384`，单次模型输出配置，并非整次评估时限 |
| key | 已有安全注入的 `READINESS_MODEL_API_KEY`，目标域 `open.bigmodel.cn` |

该 Coding Plan 路径不额外追加 `/v1`。隔离宿主配置时，`FACTORY_HOME_OVERRIDE=<目录>` 对应 `<目录>/.factory/settings.json`。配置只保存环境变量引用：

```json
{
  "customModels": [{
    "model": "glm-5.3-flash",
    "displayName": "Repository Review",
    "baseUrl": "https://open.bigmodel.cn/api/coding/paas/v4",
    "apiKey": "${READINESS_MODEL_API_KEY}",
    "provider": "generic-chat-completion-api",
    "maxOutputTokens": 16384
  }]
}
```

不要将真实 key 写进文件、命令参数、日志或报告。已验证版本支持该引用，不应改为明文持久化。使用上述宿主并不是运行本地评估器的必要条件；agent 读取 skill 后使用的是相同本地 CLI 和规则。

固定版本的 airgap 模式缺少原生报告存储工具，本项目明确用本地 JSON/Markdown 替代，不声称成功调用第三方云存储。若宿主对非只读 MCP 提交工具要求较高执行级别，须同时保留精确工具白名单和只读隔离；不要启用跳过权限检查的选项。
