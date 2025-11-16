import discord
from discord.ext import commands, tasks
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
from datetime import datetime, timezone, timedelta
from pytz import timezone as pytz_timezone
import os
import json
import time
import asyncio
import warnings  # 抑制FutureWarning
warnings.filterwarnings("ignore", category=FutureWarning)

# 配置（从环境变量读）
BOT_TOKEN = os.getenv('BOT_TOKEN')
HISTORY_DAYS = 30
MESSAGE_FILE = 'last_message_id.json'

# 美股交易时间（美东时间）
MARKET_OPEN = "09:30"  # 开盘
MARKET_CLOSE = "16:00"  # 收盘

# CNN User-Agent header（绕418 bot检测）
CNN_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# 调试token
print(f"Debug: BOT_TOKEN length: {len(BOT_TOKEN) if BOT_TOKEN else 0}, starts with: {BOT_TOKEN[:5] if BOT_TOKEN else 'EMPTY'}")
if not BOT_TOKEN or len(BOT_TOKEN) < 50:
    print("ERROR: BOT_TOKEN无效！检查Railway Variables并重置Discord token。")
    exit(1)

# Bot设置
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Slash commands 支持
from discord import app_commands

# 全局
last_message = None
channel = None

# 定义slash命令
@app_commands.command(name="update", description="手动更新贪婪恐慌指数图表（立即测试）")
async def update(interaction: discord.Interaction):
    print(f"/update 触发: {interaction.user} 在 {interaction.channel.name} (服务器: {interaction.guild.name})")
    await interaction.response.defer(ephemeral=True)  # 延迟响应
    
    global last_message
    if not channel:
        await interaction.followup.send("无可用频道！", ephemeral=True)
        return
    
    now = datetime.now(pytz_timezone('US/Eastern'))
    print(f"手动更新执行: {now} (由 {interaction.user} 触发)")
    
    # 数据获取（只Fear & Greed）
    fg_series = get_fear_greed_history()
    
    # 即使数据不足，也发文本摘要
    embed = discord.Embed(title=f"贪婪恐慌指数更新 - {now.strftime('%Y-%m-%d %H:%M')} ET", color=0x00ff00)
    embed.add_field(name="来源", value="CNN", inline=False)
    
    if len(fg_series) > 0:
        latest_fg = fg_series.iloc[-1]
        embed.add_field(name="当前指数", value=f"{latest_fg:.0f}", inline=True)
    else:
        embed.add_field(name="贪婪恐慌指数", value="数据暂不可用", inline=True)
    
    try:
        if len(fg_series) > 0:
            image_buf = create_charts(fg_series)
            file = discord.File(image_buf, filename='fear_greed_update.png')
            if last_message:
                await last_message.edit(embed=embed, attachments=[file])
            else:
                new_msg = await channel.send(embed=embed, file=file)
                last_message = new_msg
                with open(MESSAGE_FILE, 'w') as f:
                    json.dump({'message_id': new_msg.id}, f)
            print("图表+文本发送成功")
        else:
            # 只发文本
            if last_message:
                await last_message.edit(embed=embed)
            else:
                new_msg = await channel.send(embed=embed)
                last_message = new_msg
                with open(MESSAGE_FILE, 'w') as f:
                    json.dump({'message_id': new_msg.id}, f)
            print("文本摘要发送成功")
        await interaction.followup.send("更新完成！查看频道。", ephemeral=True)
    except Exception as e:
        print(f"发送失败: {e}")
        await interaction.followup.send(f"更新失败: {e} (检查bot权限)", ephemeral=True)

# 注册slash命令到tree（关键）
bot.tree.add_command(update)

@bot.event
async def on_ready():
    global channel, last_message
    print(f'{bot.user} 已上线！ID: {bot.user.id}')
    print(f"Bot在 {len(bot.guilds)} 个服务器: {[g.name for g in bot.guilds]}")
    
    # 获取频道
    guild = bot.guilds[0]
    channel = discord.utils.get(guild.text_channels, name='general')
    if not channel:
        channel = guild.text_channels[0]
    print(f"目标频道: {channel.name} (ID: {channel.id})")
    
    # 加载旧消息
    try:
        if os.path.exists(MESSAGE_FILE):
            with open(MESSAGE_FILE, 'r') as f:
                data = json.load(f)
                msg_id = int(data['message_id'])
                last_message = await channel.fetch_message(msg_id)
                print(f"加载旧消息: {msg_id}")
    except Exception as e:
        print(f"加载旧消息失败: {e}")
        last_message = None
    
    # 全局同步slash commands
    for attempt in range(3):
        try:
            synced = await bot.tree.sync()
            print(f"Slash commands synced globally: {len(synced)} (尝试 {attempt+1})")
            break
        except Exception as e:
            print(f"Global sync error (尝试 {attempt+1}): {e}")
            await asyncio.sleep(5)
    
    # 启动定时任务
    send_update.start()
    print("定时任务启动：交易时间内每小时更新。")

# 传统命令
@bot.command(name='ping')
async def ping(ctx):
    await ctx.send('Pong! Bot运行正常。')

@bot.command(name='reset')
async def reset(ctx):
    global last_message
    last_message = None
    await ctx.send('消息重置，下次更新发新消息。')

# 定时任务：每小时检查是否在交易时间内更新
@tasks.loop(hours=1)
async def send_update():
    global last_message
    if not channel:
        return
    
    now = datetime.now(pytz_timezone('US/Eastern'))
    weekday = now.weekday()  # 0=周一, 4=周五, 5/6=周末
    
    # 检查是否交易日
    if weekday > 4:  # 周末
        print(f"非交易日 ({now.strftime('%A')})，跳过更新")
        return
    
    # 检查当前时间是否在开盘-收盘内 (9:30-16:00 ET)
    current_time = now.strftime("%H:%M")
    if current_time < MARKET_OPEN or current_time > MARKET_CLOSE:
        print(f"非交易时间 ({current_time} ET)，暂停更新直到开盘")
        return
    
    # 在交易时间内，执行更新
    print(f"交易时间内自动更新: {now}")
    
    # 数据获取（只Fear & Greed）
    fg_series = get_fear_greed_history()
    
    # 即使数据不足，也发文本摘要
    embed = discord.Embed(title=f"贪婪恐慌指数更新 - {now.strftime('%Y-%m-%d %H:%M')} ET", color=0x00ff00)
    embed.add_field(name="来源", value="CNN", inline=False)
    
    if len(fg_series) > 0:
        latest_fg = fg_series.iloc[-1]
        embed.add_field(name="当前指数", value=f"{latest_fg:.0f}", inline=True)
    else:
        embed.add_field(name="贪婪恐慌指数", value="数据暂不可用", inline=True)
    
    try:
        if len(fg_series) > 0:
            image_buf = create_charts(fg_series)
            file = discord.File(image_buf, filename='fear_greed_update.png')
            if last_message:
                await last_message.edit(embed=embed, attachments=[file])
            else:
                new_msg = await channel.send(embed=embed, file=file)
                last_message = new_msg
                with open(MESSAGE_FILE, 'w') as f:
                    json.dump({'message_id': new_msg.id}, f)
            print("图表+文本发送成功")
        else:
            # 只发文本
            if last_message:
                await last_message.edit(embed=embed)
            else:
                new_msg = await channel.send(embed=embed)
                last_message = new_msg
                with open(MESSAGE_FILE, 'w') as f:
                    json.dump({'message_id': new_msg.id}, f)
            print("文本摘要发送成功")
    except Exception as e:
        print(f"自动更新失败: {e}")

def get_fear_greed_history(days=HISTORY_DAYS):
    # 回退最多30天找数据（避未来/周末）
    for attempt in range(30):
        today = datetime.now(timezone.utc).date() - timedelta(days=attempt)
        start_date = today - timedelta(days=days*2)
        url = f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{today}"
        try:
            response = requests.get(url, headers=CNN_HEADERS)
            print(f"CNN API status: {response.status_code}, text preview: {response.text[:100]}")
            if response.status_code == 200 and response.text.strip():
                data = response.json()
                if 'fear_and_greed_historical' in data and 'data' in data['fear_and_greed_historical']:
                    historical = data['fear_and_greed_historical']['data']
                    df = pd.DataFrame(historical)
                    df['date'] = pd.to_datetime(df['x'], unit='ms').dt.date
                    df = df[df['date'] >= start_date].tail(days)
                    df = df.sort_values('date').set_index('date')
                    if len(df) > 0:
                        print(f"F&G数据成功，{len(df)}天 (日期: {today})")
                        return df['y']
            else:
                print(f"CNN尝试 {attempt+1} 失败: 空响应或非200")
        except Exception as e:
            print(f"F&G尝试 {attempt+1} 错误: {e}")
    print("F&G所有尝试失败")
    return pd.Series()

def create_charts(fg_series):
    plt.style.use('dark_background')
    fig, ax = plt.subplots(1, 1, figsize=(10, 4))
    
    ax.plot(fg_series.index, fg_series.values, 'orange', label='CNN Fear & Greed Index', linewidth=1.5)
    ax.set_title('CNN 恐慌与贪婪指数 (最近30天)')
    ax.set_ylabel('指数 (0-100)')
    ax.set_xlabel('日期')
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax.tick_params(axis='x', rotation=45)
    
    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    buf.seek(0)
    plt.close()
    return buf

# 运行
try:
    bot.run(BOT_TOKEN)
except Exception as e:
    print(f"Bot运行错误: {e}")
