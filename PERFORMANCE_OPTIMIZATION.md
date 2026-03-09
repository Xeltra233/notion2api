# 性能优化与使用建议

## 🎯 最新优化 (2025-03-09)

### ✅ 已完成的优化

1. **Lite 模式速率限制提升**
   - 从 10次/分钟 → **30次/分钟**
   - 适合单轮问答场景（3-5秒/次）

2. **默认模型优化**
   - 默认模型: `claude-opus4.6` → **`claude-sonnet4.6`**
   - 理由: 速度与质量的最佳平衡

3. **新增模型支持**
   - `claude-sonnet4.6` - 推荐默认 ⭐⭐⭐⭐⭐
   - `claude-opus4.6` - 高质量推理 ⭐⭐⭐⭐
   - `claude-haiku4.5` - 快速响应（如果 Notion 支持）
   - `gemini-2.5flash` - 超快速（如果 Notion 支持）

4. **使用场景指南**
   - ✅ Cherry Studio - 完美支持
   - ✅ Zotero 翻译 - 完美支持
   - ❌ 沉浸式翻译 - 不推荐（速度慢）

---

## 📊 性能测试结果

### 实际测试数据

| 场景 | 模型 | 响应时间 | 体验 | 推荐 |
|------|------|---------|------|------|
| Cherry Studio 对话 | sonnet4.6 | 3-4秒 | ⭐⭐⭐⭐⭐ | ✅ |
| Zotero 段落翻译 | sonnet4.6 | 2-3秒 | ⭐⭐⭐⭐⭐ | ✅ |
| 沉浸式翻译（小页面） | sonnet4.6 | 30-60秒 | ⭐⭐ | ⚠️ |
| 沉浸式翻译（大页面） | sonnet4.6 | 2-4分钟 | ⭐ | ❌ |

### 推荐配置

#### Cherry Studio / ChatBox
```json
{
  "api_base": "http://localhost:8000/v1",
  "model": "claude-sonnet4.6",
  "temperature": 0.7
}
```

#### Zotero 翻译插件
```json
{
  "api_url": "http://localhost:8000/v1/chat/completions",
  "model": "claude-sonnet4.6",
  "max_length": 2000
}
```

---

## ⚠️ 不推荐使用场景

### 沉浸式翻译（全页面翻译）

**为什么不推荐？**
1. **速度问题**: Notion AI 响应时间 3-5秒/次，整页翻译需要1-4分钟
2. **限流问题**: 即使 Lite 模式 30次/分钟，大页面仍会触发限流
3. **体验问题**: 等待时间过长，容易超时

**推荐替代方案：**
| 需求 | 推荐方案 | API |
|------|---------|-----|
| 全页面翻译 | DeepL | 有免费API |
| 技术文档 | 沉浸式翻译 + DeepL | 快速+准确 |
| 论文翻译 | Zotero + Notion AI | 段落级，体验好 |

---

## 🚀 性能优化建议

### 1. 模型选择策略

```
┌─────────────────────────────────────────────────────────┐
│ 日常聊天、翻译 → claude-sonnet4.6（推荐默认）            │
│ 复杂推理、长文本 → claude-opus4.6                        │
│ 超快响应、简单任务 → claude-haiku4.5 或 gemini-2.5flash │
└─────────────────────────────────────────────────────────┘
```

### 2. 模式选择策略

```
Lite 模式 (30次/分钟):
  ✅ 翻译服务（段落级）
  ✅ 单轮问答 API
  ✅ 水平扩展部署

Heavy 模式 (20次/分钟):
  ✅ 长对话聊天
  ✅ 需要上下文记忆
  ✅ 多轮对话任务
```

### 3. 自定义速率限制

编辑 `app/limiter.py`:

```python
# 更激进的限制
if is_lite_mode():
    default_limit = "60/minute"  # 60次/分钟

# 更保守的限制
if is_lite_mode():
    default_limit = "15/minute"  # 15次/分钟
```

---

## 🧪 性能测试

### 运行测试脚本

```bash
# 确保服务器正在运行
uvicorn app.server:app --host 0.0.0.0 --port 8000

# 运行测试（另开一个终端）
python test_model_performance.py
```

测试脚本会：
- 测试所有可用模型
- 测试不同场景（问答、翻译、代码生成）
- 提供性能对比和建议

---

## 💡 最佳实践

### 1. 翻译场景决策树

```
文本长度 < 500字
   → Notion AI ✅

500字 < 文本长度 < 2000字
   → Notion AI ✅

文本长度 > 2000字
   → DeepL API ✅

全页面翻译
   → Google 翻译 / DeepL ✅
   → Notion AI ❌ (太慢)
```

### 2. 聊天场景选择

```
日常聊天
   → claude-sonnet4.6 + Heavy 模式 ✅

代码助手
   → claude-sonnet4.6 + Lite 模式 ✅

复杂推理
   → claude-opus4.6 + Heavy 模式 ✅
```

### 3. API 调用优化

```python
# ✅ 好的做法
response = requests.post(
    "http://localhost:8000/v1/chat/completions",
    json={
        "model": "claude-sonnet4.6",  # 使用平衡的模型
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,  # 简单任务用非流式
    }
)

# ❌ 不推荐
response = requests.post(
    "http://localhost:8000/v1/chat/completions",
    json={
        "model": "claude-opus4.6",  # 简单任务用 Opus 太慢
        "messages": [...],  # 过长的上下文
        "stream": True,  # 简单任务不需要流式
    }
)
```

---

## 🔍 故障排查

### 问题：响应慢
**可能原因：**
1. 使用了 `claude-opus4.6`（慢但质量高）
2. Prompt 过长
3. 网络延迟

**解决方案：**
```python
# 1. 切换到更快的模型
"model": "claude-sonnet4.6"

# 2. 缩短 prompt
"messages": [{"role": "user", "content": prompt[:1000]}]

# 3. 检查网络
ping notion.so
```

### 问题：频繁限流
**解决方案：**
```python
# 1. 编辑 app/limiter.py 提高限制
default_limit = "60/minute"

# 2. 添加客户端缓存
import functools
@functools.lru_cache(maxsize=100)
def cached_translate(text):
    return translate(text)
```

### 问题：沉浸式翻译超时
**解决方案：**
```bash
# 1. 改用 DeepL（强烈推荐）
# 2. 如果坚持用 Notion AI：
#    - 降低并发度
#    - 分段翻译
#    - 增加超时时间
```

---

## 📈 未来改进计划

### 短期（已实现 ✅）
- ✅ Lite 模式速率限制提升到 30次/分钟
- ✅ 默认模型改为 claude-sonnet4.6
- ✅ 添加更多模型支持
- ✅ 创建使用场景指南

### 中期（计划中）
- 🔄 添加请求去重缓存
- 🔄 批处理优化
- 🔄 添加 Gemini Flash 支持
- 🔄 创建性能监控面板

### 长期（探索中）
- 📋 Redis 缓存层
- 📋 CDN 缓存（翻译场景）
- 📋 多区域部署
- 📋 负载均衡优化

---

## 📚 相关文档

- [Lite Mode Summary](LITE_MODE_SUMMARY.md) - Lite 模式完整文档
- [Usage Guide](USAGE_GUIDE.md) - 使用场景详细指南
- [Testing Guide](LITE_MODE_TESTING.md) - 测试指南

---

## 🎯 快速参考

### 推荐配置速查表

| 使用场景 | 模型 | 模式 | 速率限制 | 推荐度 |
|---------|------|------|---------|--------|
| Cherry Studio | sonnet4.6 | Heavy | 20/min | ⭐⭐⭐⭐⭐ |
| Zotero 翻译 | sonnet4.6 | Lite | 30/min | ⭐⭐⭐⭐⭐ |
| 沉浸式翻译 | - | - | - | ⭐ (不推荐) |
| 单轮翻译 API | sonnet4.6 | Lite | 30/min | ⭐⭐⭐⭐ |
| 代码助手 | sonnet4.6 | Lite | 30/min | ⭐⭐⭐⭐ |
| 复杂推理 | opus4.6 | Heavy | 20/min | ⭐⭐⭐⭐ |

---

**最后更新**: 2025-03-09
**测试环境**: Windows 11, Python 3.10+
**主要模型**: Claude Sonnet 4.6 (默认推荐)
