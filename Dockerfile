# 使用 Python 3.9 Slim 版本 (基于 Debian)
FROM python:3.9-slim

# 1. 保持系统更新并安装必要工具 (wget 用于下载)
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    unzip \
    gnupg \
    --no-install-recommends

# 2. 安装 Google Chrome (直接下载 .deb 包安装，避开 apt-key 问题)
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
