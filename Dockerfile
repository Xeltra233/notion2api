# 使用官方 Python 3.11 slim 镜像作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# 安装系统依赖（如需编译额外库，但当前需求多为纯 Python，暂留作为好习惯）
RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata && \
    rm -rf /var/lib/apt/lists/*

# 将 requirements.txt 复制到工作目录并安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 将项目的源代码和前端文件复制到容器内
COPY app /app/app
COPY frontend /app/frontend
COPY main.py /app/main.py

# 创建数据目录以用于 SQLite 持久化
RUN mkdir -p /app/data

# 暴露 FastAPI 运行端口（由变量决定或默认 8000）
EXPOSE 8000

# 启动命令
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
