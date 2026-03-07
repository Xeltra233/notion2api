# GEMINI Project Context: notion2api

## 1. 项目定位 (Core Mission)

- **目标**：将订阅了 Notion AI 的账户价值最大化，通过反代转换为标准 OpenAI Chat Completions 协议。
- **卖点**：兼容性（支持思考链、联网搜索展示）、内置 Claude 风格 Demo 前端“开箱即用”、多账号池管理，特有记忆功能。

## 2. 当前技术状态 (Current Status)

- **协议兼容性**：
    - 已支持 `choices[0].delta.reasoning_content` (标准思考链，兼容 Cherry Studio)。
    - 已支持 `search_metadata` 注入（搜索结果转为 Markdown 引用块，在正文前输出）。
    - 解决了 `o: "p"` 路径替换导致的流输出中断问题。
- **错误处理**：
    - 503 报错已标准化，会明确提示用户等待恢复的秒数（基于后端 60s 冷却逻辑）。
- **前端状态**：
    - `index.html` 已同步支持 `reasoning_content` 渲染。
- **仓库状态**：
    - GitHub: `https://github.com/maverickxone/notion2api` (已同步)。
    - `.gitignore` 已配置，严防 `.env` 和 `data/` 泄露。

## 3. 开发规范 (Mandates)

- **API 兼容**：所有 SSE 流必须包含严格的 `choices[0].delta` 结构。
- **安全性**：禁止在代码中硬编码 API Key，必须通过环境变量读取。
- **部署**：保持 Dockerfile 和 docker-compose.yml 随时可用。

## 4. 下一步目标 (Next Steps)

- **P0: Opus记忆缺失**：Opus4.6模型实测没有上下文记忆，修复
- **P1: 修复某些模型bug**：GPT5.2，Opus4.6的正文被识别为思考区块，需改正。
- **P2: Vision Support**：实现多模态图片/PDF 上传与分析逻辑。
- **P3: UI 优化**：优化内置 demo 的前端。