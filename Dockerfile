# 使用 Python 基础镜像
FROM python:3.9-slim

# 1. 安装基础工具
RUN apt-get update && apt-get install -y wget gnupg unzip curl

# 2. 安装 Google Chrome
# 添加 Google 的签名密钥和软件源
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable

# 3. 设置工作目录
WORKDIR /app

# 4. 复制文件并安装 Python 库
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 5. 启动命令 (确保你的主程序叫 main.py)
CMD ["python", "main.py"]
