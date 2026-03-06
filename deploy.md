# Notion-Opus 部署指南

本项目原生支持直接在本地 Python 环境中运行，也支持通过 Docker 进行容器化部署。无论采用哪种方式，前端自带界面均会被挂载于根路径 `/`，提供即开即用的类 Claude 聊天体验。

## 方法一：本地直接启动 (开发环境)

1. 克隆或下载代码后，配置好 Python 3.11+ 环境。
2. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
3. 复制配置模板并填写您的 Notion 账号：
   ```bash
   cp .env.example .env
   ```
4. 运行服务：
   ```bash
   uvicorn app.server:app --host 0.0.0.0 --port 8000
   ```
5. 访问浏览器：打开 `http://127.0.0.1:8000` 即可开始聊天。由于聊天界面与后端现已完全融为一体，无需单独启动前端服务。

---

## 方法二：Docker Compose 容器化 (生产推荐)

借助 Docker，您可以更轻松地保证运行环境的一致性，且内置了 SQLite 数据库的本地卷挂载 (`./data` 目录) 用于持久化保存所有对话记录。

1. 确保服务器已安装 `docker` 和 `docker-compose`。
2. 配置您的账号：
   ```bash
   cp .env.example .env
   # 在 .env 文件中填入真正的 NOTION_ACCOUNTS、API_KEY 等...
   ```
3. 启动容器（守护进程模式）：
   ```bash
   docker-compose up -d
   ```
4. 如果需要停止服务：
   ```bash
   docker-compose down
   ```

---

## 多账号配置轮询 (防风控)

为了防止单个 Notion 账号请求频率过高而被限制，系统内建了 **Account Pool** 轮询机制。
在 `.env` 中，以 JSON 数组格式配置多组账号：

```env
NOTION_ACCOUNTS='[
  {"token_v2":"tokenA","space_id":"spaceA","user_id":"userA","space_view_id":"viewA","user_name":"A","user_email":"a@example.com"},
  {"token_v2":"tokenB","space_id":"spaceB","user_id":"userB","space_view_id":"viewB","user_name":"B","user_email":"b@example.com"}
]'
```

每次请求时，系统会自动分配最空闲/当前能用的账号发起请求，如遇阻断则自动切换并冷却该账号 60 秒。

---

## 配合 Nginx + HTTPS 部署

为了安全起见，我们强烈推荐在公网以 Nginx 作为前置反向代理层并配置 SSL。在 `docker-compose.yml` 中已经附带了相关的 `nginx.conf` 配置说明。核心需要注意的是**不能阻断流式加载（SSE）**:

```nginx
# 关键指令：支持流式输出引擎，保证打字机效果流畅
proxy_buffering off;
proxy_cache off;
chunked_transfer_encoding on;
```

---

## 一键无服务器部署与托管推荐
本项目纯粹轻量，对内存占用极低（约 60-80MB），因此非常适合托管在国外的容器 PaaS 平台上（自动解决 Notion API 的区域网络阻断问题）：

- **Zeabur**：对 Dockerfile 支持极其友好。只需 Fork 仓库后在控制台连接 Github 授权，导入项目即可自动构建。然后在 Zeabur 后台配置 `.env` 变量，并挂载一个持久卷(`Volume`) 到 `/app/data`。
- **Render** / **Railway**：同理，可以直接选择 "Deploy from Dockerfile"，只需将 `DB_PATH=/app/data/conversations.db` 对应的目录挂载为持久存储卷（Persistent Disk）防止重启丢失聊天记录即可。
