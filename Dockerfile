# 使用 Python 3.10 Slim 版本 (基于 Debian)
FROM python:3.10-slim

# 🔥 关键：强制 Python 实时打印日志 (解决日志卡顿)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 1. 安装系统依赖 + Chromium (轻量化浏览器)
# 这一步会自动安装代码运行所需的浏览器及其驱动
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

# 3. 复制并安装 Python 库 (这一步是安装 pytz 和 holidays 的关键!)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 复制主程序
COPY . .

# 5. 启动命令
CMD ["python", "main.py"]
