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
@app_commands.command(name="update", description="Manually update Fear & Greed chart (immediate test)")
async def update(interaction: discord.Interaction):
    print(f"/update triggered: {interaction.user} in {interaction.channel.name} (server: {interaction.guild.name})")
    await interaction.response.defer(ephemeral=True)  # Defer response
    
    global last_message
    if not channel:
        await interaction.followup.send("No available channel!", ephemeral=True)
        return
    
    now = datetime.now(pytz_timezone('US/Eastern'))
    print(f"Manual update executing: {now} (by {interaction.user})")
    
    # Data fetching (only Fear & Greed)
    fg_series = get_fear_greed_history()
    
    # Always send text summary, even if data insufficient
    embed = discord.Embed(title=f"Fear & Greed Update - {now.strftime('%Y-%m-%d %H:%M')} ET", color=0x00ff00)
    embed.add_field(name="Source", value="CNN", inline=False)
    
    if len(fg_series) > 0:
        latest_fg = fg_series.iloc[-1]
        embed.add_field(name="Current Index", value=f"{latest_fg:.0f}", inline=True)
    else:
        embed.add_field(name="Fear & Greed Index", value="Data unavailable", inline=True)
    
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
            print("Chart + text sent successfully")
        else:
            # Text only
            if last_message:
                await last_message.edit(embed=embed)
            else:
                new_msg = await channel.send(embed=embed)
                last_message = new_msg
                with open(MESSAGE_FILE, 'w') as f:
                    json.dump({'message_id': new_msg.id}, f)
            print("Text summary sent successfully")
        await interaction.followup.send("Update complete! Check channel.", ephemeral=True)
    except Exception as e:
        print(f"Sending failed: {e}")
        await interaction.followup.send(f"Update failed: {e} (check bot permissions)", ephemeral=True)

# Register slash command to tree (key)
bot.tree.add_command(update)

@bot.event
async def on_ready():
    global channel, last_message
    print(f'{bot.user} logged in! ID: {bot.user.id}')
    print(f"Bot in {len(bot.guilds)} servers: {[g.name for g in bot.guilds]}")
    
    # Get channel
    guild = bot.guilds[0]
    channel = discord.utils.get(guild.text_channels, name='general')
    if not channel:
        channel = guild.text_channels[0]
    print(f"Target channel: {channel.name} (ID: {channel.id})")
    
    # Load old message
    try:
        if os.path.exists(MESSAGE_FILE):
            with open(MESSAGE_FILE, 'r') as f:
                data = json.load(f)
                msg_id = int(data['message_id'])
                last_message = await channel.fetch_message(msg_id)
                print(f"Loaded old message: {msg_id}")
    except Exception as e:
        print(f"Loading old message failed: {e}")
        last_message = None
    
    # Global sync slash commands
    for attempt in range(3):
        try:
            synced = await bot.tree.sync()
            print(f"Slash commands synced globally: {len(synced)} (attempt {attempt+1})")
            break
        except Exception as e:
            print(f"Global sync error (attempt {attempt+1}): {e}")
            await asyncio.sleep(5)
    
    # Start timed task
    send_update.start()
    print("Timed task started: Update hourly during trading hours.")

# Traditional commands
@bot.command(name='ping')
async def ping(ctx):
    await ctx.send('Pong! Bot running normally.')

@bot.command(name='reset')
async def reset(ctx):
    global last_message
    last_message = None
    await ctx.send('Message reset, next update will send new.')

# Timed task: Check hourly if in trading hours for update
@tasks.loop(hours=1)
async def send_update():
    global last_message
    if not channel:
        return
    
    now = datetime.now(pytz_timezone('US/Eastern'))
    weekday = now.weekday()  # 0=Mon, 4=Fri, 5/6=weekend
    
    # Check if trading day
    if weekday > 4:  # Weekend
        print(f"Non-trading day ({now.strftime('%A')}), skipping update")
        return
    
    # Check if in open-close hours (9:30-16:00 ET)
    current_time = now.strftime("%H:%M")
    if current_time < MARKET_OPEN or current_time > MARKET_CLOSE:
        print(f"Non-trading time ({current_time} ET), pause update until open")
        return
    
    # In trading hours, execute update
    print(f"Auto update in trading hours: {now}")
    
    # Data fetching (only Fear & Greed)
    fg_series = get_fear_greed_history()
    
    # Always send text summary, even if data insufficient
    embed = discord.Embed(title=f"Fear & Greed Update - {now.strftime('%Y-%m-%d %H:%M')} ET", color=0x00ff00)
    embed.add_field(name="Source", value="CNN", inline=False)
    
    if len(fg_series) > 0:
        latest_fg = fg_series.iloc[-1]
        embed.add_field(name="Current Index", value=f"{latest_fg:.0f}", inline=True)
    else:
        embed.add_field(name="Fear & Greed Index", value="Data unavailable", inline=True)
    
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
            print("Chart + text sent successfully")
        else:
            # Text only
            if last_message:
                await last_message.edit(embed=embed)
            else:
                new_msg = await channel.send(embed=embed)
                last_message = new_msg
                with open(MESSAGE_FILE, 'w') as f:
                    json.dump({'message_id': new_msg.id}, f)
            print("Text summary sent successfully")
    except Exception as e:
        print(f"Auto update failed: {e}")

def get_fear_greed_history(days=HISTORY_DAYS):
    # Back up max 30 days for data (avoid future/weekends)
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
                        print(f"F&G data success, {len(df)} days (date: {today})")
                        return df['y']
            else:
                print(f"CNN attempt {attempt+1} failed: empty response or non-200")
        except Exception as e:
            print(f"F&G attempt {attempt+1} error: {e}")
    print("All F&G attempts failed")
    return pd.Series()

def create_charts(fg_series):
    plt.style.use('dark_background')
    fig, ax = plt.subplots(1, 1, figsize=(10, 4))
    
    ax.plot(fg_series.index, fg_series.values, 'orange', label='CNN Fear & Greed Index', linewidth=1.5)
    ax.set_title('CNN Fear & Greed Index (Last 30 Days)')
    ax.set_ylabel('Index (0-100)')
    ax.set_xlabel('Date')
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
