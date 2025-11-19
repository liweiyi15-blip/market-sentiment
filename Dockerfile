# 使用轻量级 Python 镜像
FROM python:3.9-slim

# 1. 安装系统依赖 (wget, curl, unzip 等)
RUN apt-get update && apt-get install -y wget gnupg unzip curl

# 2. 安装 Google Chrome (稳定版)
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable

# 3. 设置工作目录
WORKDIR /app

# 4. 安装 Python 库
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 复制主程序
COPY . .

# 6. 启动
CMD ["python", "main.py"]
