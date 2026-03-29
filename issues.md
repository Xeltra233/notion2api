# Issues & Troubleshooting

> Common errors and solutions

---

## Q1: 503 Service Unavailable - "Too Many Requests"

**Error Message:**
```
503 Service Unavailable: Notion account rate limited
```

**Cause:**
- Notion AI has rate limits (to protect your account), and rapid consecutive requests trigger 429 from Notion
- Previously, the cooldown period was too long (60 seconds), causing frequent 503 errors
- Fixed: Now cooldown is only 10 seconds, usually won't happen.

**Solution:**

1. **Wait a few seconds** and retry (recommended)
   - Notion's rate limit usually recovers within 10-30 seconds

2. **If using multiple accounts**, the system will automatically switch to another account
   ```bash
   # Add more accounts in .env to improve reliability
   NOTION_ACCOUNTS='[{"token_v2":"..."}, {"token_v2":"..."}]'
   ```

3. **Reduce request frequency**
   - Lite mode: max 30 requests/minute
   - Standard mode: max 25 requests/minute
   - Heavy mode: max 20 requests/minute

**Prevention:**
- Avoid sending multiple requests in quick succession
- Use Standard mode for better stability
- Configure multiple accounts if possible

---

## Q2: 405 Method Not Allowed

**Error Message:**
```
API Error: 405 Method Not Allowed
```

**Cause:**
- The requested endpoint does not support the HTTP method used
- Common cause: Claude Code or other tools might be using wrong endpoint or method

**Notion2API Supported Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | Chat completion (main endpoint) |
| `/v1/models` | GET | List available models |
| `/v1/conversations/{id}` | DELETE | Delete conversation (Heavy mode) |
| `/health` | GET | Health check |

**Solution:**

1. **Check the endpoint URL**
   - Make sure you're using `/v1/chat/completions` (with `/v1` prefix)
   - Not `/chat/completions` (without prefix)

2. **Check the HTTP method**
   - Chat endpoint only supports **POST** method
   - Do not use GET, PUT, DELETE on `/v1/chat/completions`

3. **For Claude Code specifically**
    - Claude Code uses Anthropic's native API format, which is **incompatible** with Notion2API
    - Notion2API provides an OpenAI-compatible chat API
    - User messages now support plain text or OpenAI-style multimodal content arrays with `text` and `image_url`
    - Image inputs are currently accepted by the API layer and forwarded to Notion as a text fallback that includes the image URL
    - It cannot read files, execute commands, or use tools
    - **Claude Code is NOT supported** - use OpenCode or other compatible tools instead

### Q2.1: 400 Bad Request - Invalid multimodal content

**Typical Causes:**
- `messages[].content` is an array but contains unsupported block types
- `image_url.url` is empty or not an `http(s)` URL / `data:image/...` URI
- Non-user messages use non-text content blocks

**Supported format:**

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "Describe this image"},
    {"type": "image_url", "image_url": {"url": "https://example.com/demo.png"}}
  ]
}
```

**Notes:**
- Supported user content block types: `text`, `image_url`
- `system`, `developer`, `assistant` messages should still use plain text or text-only content blocks

### Q2.2: `/v1/responses` input rejected

`POST /v1/responses` currently accepts these shapes:

- `input: "plain text"`
- `input: [content blocks]`
- `input: [message objects]`

Examples of valid content blocks:

```json
[
  {"type": "text", "text": "Describe this image"},
  {"type": "image_url", "image_url": {"url": "https://example.com/demo.png"}}
]
```

If you send message objects, use the same `role/content` rules as `/v1/chat/completions`.

---

## Q3: 401 Unauthorized - "Token Expired"

**Error Message:**
```
401 Unauthorized: Notion upstream returned HTTP 401
```

**Cause:**
- Your `token_v2` has expired or become invalid
- Notion account was logged out
- Notion updated authentication methods

**Solution:**

1. **Refresh your token_v2** (recommended)
   - Open https://www.notion.so/ai and make sure you're logged in
   - Press `F12` to open Developer Tools
   - Go to **Application** tab
   - Expand **Storage → Cookies → https://www.notion.so**
   - Find `token_v2` and copy its **Value**
   - Update the `token_v2` in your `.env` file
   - Restart the service

2. **Verify Notion account status**
   - Open https://www.notion.so/ai in your browser
   - Make sure you're logged in
   - Try manually using Notion AI

**Prevention:**
- Periodically refresh your token_v2
- Don't log out of Notion while the service is running

---

## Q4: Admin APIs now return masked values - is that a bug?

**Typical Symptoms:**
```json
{"token_v2": "********", "has_token_v2": true}
```

**Cause:**
- This is expected on safe admin surfaces
- The project now separates default safe admin views from explicit raw recovery/editing views
- Safe endpoints return masked secrets plus presence flags so the UI can stay usable without leaking raw credentials

**Expected behavior:**
- `/v1/admin/accounts/safe` → masked list view
- `/v1/admin/accounts/export` → masked export by default
- `/v1/admin/accounts` → explicit raw list view
- `/v1/admin/accounts/{account_id}` → explicit raw single-account view for edit flows
- `/v1/admin/accounts/export?raw=true` → explicit raw export

**Audit behavior:**
- Raw list reads and raw exports are logged into admin operation history
- Operation log payloads expose metadata like export mode so operators can tell whether a recent export was `safe` or `raw`

**How to tell what you received:**
- Check `view_mode`, `export_mode`, `redaction_mode`, or `response_mode` in the response body
- Safe account/config/report responses should identify themselves as `safe`
- Utility endpoints such as alerts/operations/templates now describe their payload type via `response_mode`

## More Issues Coming Soon...

---

*Last updated: 2026-03-13*
