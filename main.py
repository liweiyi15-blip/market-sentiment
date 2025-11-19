import discord
import os
import requests
import asyncio
from discord.ext import commands, tasks
from bs4 import BeautifulSoup
from datetime import datetime
import pytz

# --- 配置部分 ---
TOKEN = os.getenv("DISCORD_TOKEN")
# 目标频道ID (在Discord开启开发者模式，右键频道复制ID)
TARGET_CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0")) 

# 设置 Bot
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# --- 抓取 FEDwatch 数据 (示例逻辑) ---
def get_fed_data():
    try:
        # 注意：CME 官网通常有反爬或动态加载，这里仅作演示结构。
        # 实际生产中建议抓取特定的 API 端点或使用 Selenium
        url = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        # 模拟请求 (实际需解析 CME 的 JSON API)
        # response = requests.get(url, headers=headers)
        # soup = BeautifulSoup(response.text, 'html.parser')
        
        # ⚠️ 这里返回模拟数据，因为CME数据需要复杂的动态解析
        return "📊 **FEDwatch 预测更新**\n当前降息概率: (需接入具体API)\n数据来源: CME Group"
    except Exception as e:
        return f"数据获取失败: {e}"

# --- 定时任务 ---
# 设置每 24 小时发送一次，或者使用 @tasks.loop(hours=4)
@tasks.loop(seconds=10) 
async def scheduled_task():
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        msg = get_fed_data()
        current_time = datetime.now(pytz.timezone('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M")
        await channel.send(f"{msg}\nUpdate: {current_time}")
    else:
        print("找不到频道 ID")

@scheduled_task.before_loop
async def before_task():
    await bot.wait_until_ready()

# --- Bot 事件 ---
@bot.event
async def on_ready():
    print(f'已登录为 {bot.user}')
    if not scheduled_task.is_running():
        scheduled_task.start()

if __name__ == "__main__":
    bot.run(TOKEN)
