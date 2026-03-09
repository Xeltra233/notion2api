# Lite Mode Testing Guide

## Quick Start Test

### 1. Set Environment Variable
```bash
# Linux/Mac
export APP_MODE=lite

# Windows PowerShell
$env:APP_MODE="lite"

# Windows CMD
set APP_MODE=lite
```

### 2. Start Server
```bash
uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Verify Startup Logs
Look for this message in logs:
```
Service starting up in LITE mode
```

### 4. Run Test Request
```bash
# Simple non-streaming test
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-opus4.6",
    "messages": [{"role": "user", "content": "Say hello"}],
    "stream": false
  }'
```

## Expected Results

### ✅ Success Indicators
1. Server starts without SQLite database errors
2. Log shows "LITE mode" on startup
3. Response returns valid OpenAI format
4. No `X-Conversation-Id` header in response
5. Multiple requests don't share memory

### ❌ Failure Indicators
1. Database-related errors
2. "Conversation not found" errors
3. Missing conversation_manager attribute

## Common Issues

### Issue: "No module named 'app'"
**Solution:** Run from project root directory

### Issue: Database errors
**Solution:** Verify APP_MODE is set to "lite" (not "Lite" or "LITE")

### Issue: Account pool errors
**Solution:** Verify NOTION_ACCOUNTS is set correctly

## Advanced Testing

### Test Memory Isolation
```bash
# Request 1: Establish a name
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-opus4.6","messages":[{"role":"user","content":"My name is Alice"}],"stream":false}'

# Request 2: Ask about the name (should NOT remember)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-opus4.6","messages":[{"role":"user","content":"What is my name?"}],"stream":false}'
```

### Test Streaming
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-opus4.6","messages":[{"role":"user","content":"Count from 1 to 5"}],"stream":true}'
```

### Test System Instructions
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-opus4.6","messages":[{"role":"system","content":"Translate to English"},{"role":"user","content":"你好世界"}],"stream":false}'
```

## Performance Comparison

| Metric | Heavy Mode | Lite Mode |
|--------|-----------|-----------|
| Startup Time | ~2s (DB init) | ~0.5s |
| Memory Usage | ~50MB | ~30MB |
| First Response | ~200ms | ~150ms |
| Subsequent Responses | ~200ms | ~150ms |
| State Management | Complex | Simple |
| Scaling | Vertical preferred | Horizontal friendly |

## Production Deployment

### Docker Compose
```yaml
version: '3.8'
services:
  notion-ai-lite:
    image: notion-ai:latest
    environment:
      - APP_MODE=lite
      - NOTION_ACCOUNTS=${NOTION_ACCOUNTS}
    ports:
      - "8000:8000"
    # No database volume needed!
```

### Kubernetes
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: notion-ai-lite
spec:
  replicas: 3  # Easy horizontal scaling
  template:
    spec:
      containers:
      - name: notion-ai
        image: notion-ai:latest
        env:
        - name: APP_MODE
          value: "lite"
        - name: NOTION_ACCOUNTS
          valueFrom:
            secretKeyRef:
              name: notion-secrets
              key: accounts
```

## Monitoring

### Health Check
```bash
curl http://localhost:8000/health
```

### Mode Detection
```bash
# Check if running in Lite mode
curl -s http://localhost:8000/health | grep -q "lite" && echo "Lite mode" || echo "Heavy mode"
```

### Log Analysis
```bash
# Look for Lite mode indicators
grep "LITE mode" /var/log/notion-ai/app.log

# Look for conversation operations (should be none in Lite mode)
grep "conversation_id" /var/log/notion-ai/app.log | wc -l
```

## Troubleshooting

### Verify Mode
```python
import os
print(os.getenv('APP_MODE', 'heavy'))
```

### Check Database
```bash
# Should NOT exist in Lite mode
ls -la ./data/conversations.db 2>&1 | grep "No such file"
```

### Test Imports
```python
from app.config import is_lite_mode
print(f"Lite mode: {is_lite_mode()}")
```

---
**Last Updated:** 2025-03-09
**Status:** ✅ Ready for Testing
