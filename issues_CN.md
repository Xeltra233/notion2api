# 问题排查指南

> 常见错误和解决方案

---

## Q1: 503 Service Unavailable - "请求过多"

**错误信息:**
```
503 Service Unavailable: Notion 账号被限流
```

**原因:**
- Notion AI 有请求频率限制（为了保护你的账号），连续快速提问会触发 Notion 返回 429
- 之前冷却时间过长（60秒），导致频繁出现 503
- 已修复：现在冷却时间仅为 10 秒

**解决方案:**

1. **等待几秒后重试**（推荐）
   - Notion 的限流通常在 10-30 秒后恢复

2. **如果配置了多账号**，系统会自动切换到其他账号
   ```bash
   # 在 .env 中添加更多账号以提高稳定性
   NOTION_ACCOUNTS='[{"token_v2":"..."}, {"token_v2":"..."}]'
   ```

3. **降低请求频率**
   - Lite 模式：最多 30 次/分钟
   - Standard 模式：最多 25 次/分钟
   - Heavy 模式：最多 20 次/分钟

**预防建议:**
- 避免连续快速发送请求
- 使用 Standard 模式以获得更好的稳定性
- 如有可能，配置多个账号

---

## Q2: 405 Method Not Allowed - "方法不被允许"

**错误信息:**
```
API Error: 405 Method Not Allowed
```

**原因:**
- 请求的端点不支持使用的 HTTP 方法
- 常见原因：Claude Code 或其他工具使用了错误的端点或方法

**Notion2API 支持的端点:**

| 端点 | 方法 | 描述 |
|------|------|------|
| `/v1/chat/completions` | POST | 聊天接口（主要端点） |
| `/v1/models` | GET | 获取可用模型列表 |
| `/v1/conversations/{id}` | DELETE | 删除对话（Heavy 模式） |
| `/health` | GET | 健康检查 |

**解决方案:**

1. **检查端点 URL**
   - 确保使用 `/v1/chat/completions`（带 `/v1` 前缀）
   - 而不是 `/chat/completions`（没有前缀）

2. **检查 HTTP 方法**
   - 聊天接口只支持 **POST** 方法
   - 不要在 `/v1/chat/completions` 上使用 GET、PUT、DELETE

3. **关于 Claude Code**
    - Claude Code 使用 Anthropic 原生 API 格式，与 Notion2API **不兼容**
    - Notion2API 提供 OpenAI 兼容的聊天接口
    - 用户消息现在支持纯文本，或 OpenAI 风格的多模态 `content` 数组（当前支持 `text` 和 `image_url`）
    - 图片输入目前已能被 API 层接收，但发往 Notion 上游时仍会先降级为包含图片 URL 的文本提示
    - 它无法读取文件、执行命令或使用工具
    - **不支持 Claude Code** - 请使用 OpenCode 或其他兼容工具

### Q2.1: 400 Bad Request - 多模态内容格式错误

**常见原因：**
- `messages[].content` 是数组，但包含了当前不支持的 block 类型
- `image_url.url` 为空，或不是 `http(s)` URL / `data:image/...` URI
- 非 user 消息使用了非文本 block

**支持格式示例：**

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "请描述这张图片"},
    {"type": "image_url", "image_url": {"url": "https://example.com/demo.png"}}
  ]
}
```

**注意：**
- 当前支持的用户内容块类型：`text`、`image_url`
- `system`、`developer`、`assistant` 仍建议使用纯文本或仅 `text` block

### Q2.2: `/v1/responses` 输入被拒绝

`POST /v1/responses` 目前接受以下几种 `input` 形态：

- `input: "纯文本"`
- `input: [content blocks]`
- `input: [message objects]`

合法 content block 示例：

```json
[
  {"type": "text", "text": "请描述这张图片"},
  {"type": "image_url", "image_url": {"url": "https://example.com/demo.png"}}
]
```

如果传 message objects，则仍需遵循 `/v1/chat/completions` 的 `role/content` 规则。

---

## Q3: 401 Unauthorized - "认证失败"

**错误信息:**
```
401 Unauthorized: Notion upstream returned HTTP 401
```

**原因:**
- 你的 `token_v2` 已过期或失效
- Notion 账号已退出登录
- Notion 更新了认证方式

**解决方案:**

1. **重新获取 token_v2**（推荐）
   - 打开 https://www.notion.so/ai 并确保已登录
   - 按 `F12` 打开开发者工具
   - 切换到 **Application** 标签
   - 左侧找到 **Storage → Cookies → https://www.notion.so**
   - 找到 `token_v2`，复制其 **Value**
   - 更新 `.env` 文件中的 `token_v2`
   - 重启服务

2. **检查 Notion 账号状态**
   - 在浏览器中打开 https://www.notion.so/ai
   - 确保已登录
   - 尝试手动使用 Notion AI

**预防建议:**
- 定期刷新 token_v2
- 服务运行时不要退出 Notion 账号登录

---

## Q4: Admin 接口现在返回脱敏值，这是 bug 吗？

**不是。** 这是后台接口安全收敛后的预期行为。

**当前规则：**
- `/v1/admin/accounts/safe` 默认返回安全视图，敏感字段会被 masked
- `/v1/admin/accounts` 与 `/v1/admin/accounts/{account_id}` 是显式原始管理视图
- `/v1/admin/accounts/export` 默认也是安全导出；只有显式 `?raw=true` 才返回原始导出
- `/v1/admin/config`、`/v1/admin/snapshot`、`/v1/admin/report` 会返回脱敏后的 settings/accounts 视图
- alerts / operations / request templates / diagnostics 这类工具接口会通过 `response_mode` 标记自己的语义，而不是返回明文 secret

**如何判断当前返回的是什么：**
- 看 `view_mode`
- 看 `export_mode`
- 看 `redaction_mode`
- 看 `response_mode`
- 看 `contains_secrets`

**常见误解：**
1. **字段被 masked 不是字段丢了**
   - 后端通常还会补 `has_*` presence 标记，例如 `has_api_key`、`has_token_v2`
2. **列表是 safe，不代表单账号详情也是 safe**
   - 管理页默认先用 safe 列表，再在需要编辑某个账号时单独读取 raw 详情
3. **操作日志不是异常日志**
   - 原始列表读取、原始导出等敏感操作会进入 `operations` / `logs`，属于审计信息

**排查建议：**
- 先确认自己调用的是 safe 视图还是 raw 视图
- 再检查响应体里的模式字段，而不是只看某个 token 是否被打码
- 如果前端展示异常，优先确认它是否按新的模式字段读取后端响应

## 更多问题待续...

---

*最后更新: 2026-03-13*
