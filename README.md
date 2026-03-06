# Notion AI API 调用项目

这是一个用 Python 调用 Notion AI（Opus 4.6）的独立项目，通过逆向工程 Notion Web API 实现。

## 项目结构

```
notion/
├── .venv/              # 虚拟环境（隔离依赖）
├── opus.py             # 主要代码
├── requirements.txt    # 依赖列表
└── README.md           # 本文件
```

## 快速开始

### 1. 激活虚拟环境

**Windows PowerShell：**
```powershell
cd notion
.\.venv\Scripts\activate.ps1
```

**Windows CMD：**
```cmd
cd notion
.venv\Scripts\activate.bat
```

**Mac/Linux：**
```bash
cd notion
source .venv/bin/activate
```

成功激活后，终端左边会显示 `(.venv)`

### 2. 运行代码

```bash
python opus.py
```

### 3. 退出虚拟环境

```bash
deactivate
```

## 重新安装依赖

如果虚拟环境损坏或需要重建，运行：

```bash
# 删除旧虚拟环境
rmdir /s /q .venv  # Windows
rm -rf .venv       # Mac/Linux

# 创建新虚拟环境
python -m venv .venv

# 激活并安装依赖
.venv\Scripts\activate.ps1  # Windows
pip install -r requirements.txt
```

## 使用说明

修改 `__main__` 块中的参数，替换成你自己的 Notion 账户信息：

```python
TOKEN_V2 = "你的 token_v2"
SPACE_ID = "你的 space_id"
USER_ID = "你的 user_id"
SPACE_VIEW_ID = "你的 space_view_id"
USER_NAME = "你的用户名"
USER_EMAIL = "你的邮箱"

api = NotionOpusAPI(TOKEN_V2, SPACE_ID, USER_ID, SPACE_VIEW_ID, USER_NAME, USER_EMAIL)
api.generate_response("你的问题")
```

## 获取账户信息

1. 打开 https://www.notion.so/ai
2. 按 `F12` 打开开发者工具
3. 进入 **Network** 标签
4. 刷新页面或发送一条消息
5. 在请求中查找 `runInferenceTranscript` 的 POST 请求
6. 在 Request Headers 和 Cookies 中找到对应信息

## 注意事项

⚠️ **不要分享你的 TOKEN_V2** - 它等同于你的账号密码！
