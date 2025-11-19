# 使用 Python 3.9 Slim
FROM python:3.9-slim

# 强制实时日志
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 1. 安装系统依赖 + Chromium + Chromium Driver
# 这一步会自动处理所有依赖，比手动安装 Chrome 稳定得多
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    wget \
    curl \
    unzip \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# 2. 设置工作目录
WORKDIR /app

# 3. 安装 Python 库
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 复制主程序
COPY . .

# 5. 启动
CMD ["python", "main.py"]
