import discord
from discord.ext import commands, tasks
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
from datetime import datetime, timezone, timedelta
from pytz import timezone as pytz_timezone
import asyncio  # 新增：异步支持

# 配置（用环境变量）
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Railway Variables中设置
CHANNEL_ID = 1234567890123456789  # 目标频道ID（数字），替换为你的频道ID
FMP_API_KEY = "your_fmp_api_key_here"  #Railway Variables中设置
HISTORY_DAYS = 30

# Bot设置
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'{bot.user} 已上线！')
    send_update.start()  # 启动定时任务

@tasks.loop(hours=1)  # 每小时运行
async def send_update():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("频道未找到！")
        return

    now = datetime.now(pytz_timezone('US/Eastern'))
    print(f"执行时间: {now}")

    # 数据获取（同之前）
    fg_series = get_fear_greed_history()
    tickers = get_sp500_tickers()
    print(f"处理 {len(tickers)} 只股票的历史...")
    part20, part50 = calculate_market_participation_history(tickers)

    if len(fg_series) > 0 and len(part20) > 0:
        image_buf = create_charts(fg_series, part20, part50)
        # 发送embed消息+附件
        embed = discord.Embed(title=f"每小时市场更新 - {datetime.now().strftime('%Y-%m-%d %H:%M')} (美东时间)", color=0x00ff00)
        embed.add_field(name="数据来源", value="CNN & FMP", inline=False)
        file = discord.File(image_buf, filename='market_update.png')
        await channel.send(embed=embed, file=file)
        print("图表发送成功")
    else:
        print("数据不足，跳过")

# 其他函数（复制之前脚本的get_fear_greed_history, get_sp500_tickers, get_historical_prices, calculate_market_participation_history, create_charts）
# ...（这里省略，粘贴之前main.py的这些函数；只改create_charts返回buf）

# 运行bot
bot.run(BOT_TOKEN)
