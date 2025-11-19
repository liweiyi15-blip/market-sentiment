# 使用 Python 3.9 Slim 版本
FROM python:3.9-slim

# 🔥 关键修改：强制 Python 实时打印日志，不要缓存！
ENV PYTHONUNBUFFERED=1
# 防止 Python 生成 .pyc 文件
ENV PYTHONDONTWRITEBYTECODE=1

# 1. 安装系统依赖
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    unzip \
    gnupg \
    --no-install-recommends

# 2. 安装 Google Chrome (直接下载 .deb 包安装)
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 3. 设置工作目录
WORKDIR /app

# 4. 复制并安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 复制主程序
COPY . .

# 6. 启动命令
CMD ["python", "main.py"]
