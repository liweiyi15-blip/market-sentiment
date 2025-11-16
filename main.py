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

# 配置（从环境变量读）
BOT_TOKEN = os.getenv('BOT_TOKEN')
FMP_API_KEY = os.getenv('FMP_API_KEY')
HISTORY_DAYS = 30
MESSAGE_FILE = 'last_message_id.json'

# 美股交易时间（美东时间）
MARKET_OPEN = "09:30"  # 开盘
MARKET_CLOSE = "16:00"  # 收盘

# 调试token
print(f"Debug: BOT_TOKEN length: {len(BOT_TOKEN) if BOT_TOKEN else 0}, starts with: {BOT_TOKEN[:5] if BOT_TOKEN else 'EMPTY'}")
if not BOT_TOKEN or len(BOT_TOKEN) < 50:
    print("ERROR: BOT_TOKEN无效！检查Railway Variables并重置Discord token。")
    exit(1)

if not FMP_API_KEY:
    print("WARNING: FMP_API_KEY未设置，市场数据将跳过。")

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
@app_commands.command(name="update", description="手动更新市场图表（立即测试）")
async def update(interaction: discord.Interaction):
    print(f"/update 触发: {interaction.user} 在 {interaction.channel.name} (服务器: {interaction.guild.name})")
    await interaction.response.defer(ephemeral=True)  # 延迟响应
    
    global last_message
    if not channel:
        await interaction.followup.send("无可用频道！", ephemeral=True)
        return
    
    now = datetime.now(pytz_timezone('US/Eastern'))
    print(f"手动更新执行: {now} (由 {interaction.user} 触发)")
    
    # 数据获取
    fg_series = get_fear_greed_history()
    if not FMP_API_KEY:
        print("无FMP_KEY，跳过参与度")
        part20 = pd.Series()
        part50 = pd.Series()
    else:
        tickers = get_sp500_tickers()
        print(f"股票数: {len(tickers)}")
        part20, part50 = calculate_market_participation_history(tickers)
    
    # 即使数据不足，也发文本摘要
    embed = discord.Embed(title=f"市场更新 - {now.strftime('%Y-%m-%d %H:%M')} ET", color=0x00ff00)
    embed.add_field(name="来源", value="CNN & FMP", inline=False)
    
    if len(fg_series) > 0:
        latest_fg = fg_series.iloc[-1]
        embed.add_field(name="Fear & Greed 当前", value=f"{latest_fg:.0f}", inline=True)
    else:
        embed.add_field(name="Fear & Greed", value="数据暂不可用", inline=True)
    
    if len(part20) > 0:
        latest_20 = part20.iloc[-1]
        latest_50 = part50.iloc[-1]
        embed.add_field(name="参与度 (20/50日SMA)", value=f"{latest_20:.1f}% / {latest_50:.1f}%", inline=True)
    else:
        embed.add_field(name="参与度", value="数据暂不可用", inline=True)
    
    try:
        if len(fg_series) > 0 and len(part20) > 0:
            image_buf = create_charts(fg_series, part20, part50)
            file = discord.File(image_buf, filename='market_update.png')
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
        await interaction.followup.send("更新完成！查看频道（即使数据不足，也发摘要）。", ephemeral=True)
    except Exception as e:
        print(f"发送失败: {e}")
        await interaction.followup.send(f"更新失败: {e}", ephemeral=True)

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
    
    # 数据获取
    fg_series = get_fear_greed_history()
    if not FMP_API_KEY:
        part20 = pd.Series()
        part50 = pd.Series()
    else:
        tickers = get_sp500_tickers()
        part20, part50 = calculate_market_participation_history(tickers)
    
    # 即使数据不足，也发文本摘要
    embed = discord.Embed(title=f"市场更新 - {now.strftime('%Y-%m-%d %H:%M')} ET", color=0x00ff00)
    embed.add_field(name="来源", value="CNN & FMP", inline=False)
    
    if len(fg_series) > 0:
        latest_fg = fg_series.iloc[-1]
        embed.add_field(name="Fear & Greed 当前", value=f"{latest_fg:.0f}", inline=True)
    else:
        embed.add_field(name="Fear & Greed", value="数据暂不可用", inline=True)
    
    if len(part20) > 0:
        latest_20 = part20.iloc[-1]
        latest_50 = part50.iloc[-1]
        embed.add_field(name="参与度 (20/50日SMA)", value=f"{latest_20:.1f}% / {latest_50:.1f}%", inline=True)
    else:
        embed.add_field(name="参与度", value="数据暂不可用", inline=True)
    
    try:
        if len(fg_series) > 0 and len(part20) > 0:
            image_buf = create_charts(fg_series, part20, part50)
            file = discord.File(image_buf, filename='market_update.png')
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
    # 尝试当前日期，回退最多3天找数据
    for attempt in range(3):
        today = (datetime.now(timezone.utc).date() - timedelta(days=attempt)).strftime('%Y-%m-%d')
        start_date = (datetime.now(timezone.utc).date() - timedelta(days=days*2 + attempt)).strftime('%Y-%m-%d')
        url = f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{today}"
        try:
            response = requests.get(url)
            print(f"CNN API status: {response.status_code}, text preview: {response.text[:100]}")
            if response.status_code == 200 and response.text.strip():
                data = response.json()
                historical = data['fear_and_greed_historical']['data']
                df = pd.DataFrame(historical)
                df['date'] = pd.to_datetime(df['x'], unit='ms').dt.date
                df = df[df['date'] >= datetime.strptime(start_date, '%Y-%m-%d').date()].tail(days)
                df = df.sort_values('date').set_index('date')
                if len(df) > 0:
                    print(f"F&G数据成功，{len(df)}天")
                    return df['y']
            else:
                print(f"CNN尝试 {attempt+1} 失败: 空响应或非200")
        except Exception as e:
            print(f"F&G尝试 {attempt+1} 错误: {e}")
    print("F&G所有尝试失败")
    return pd.Series()

def get_sp500_tickers():
    url = f"https://financialmodelingprep.com/api/v3/sp500_constituent?apikey={FMP_API_KEY}"
    try:
        response = requests.get(url)
        print(f"FMP API status: {response.status_code}, text preview: {response.text[:200]}")
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                print(f"S&P列表成功，{len(data)}只股票")
                return [stock['symbol'] for stock in data]
            else:
                print(f"S&P响应非列表: {type(data)}")
                return []
        else:
            print(f"FMP非200: {response.status_code}")
            return []
    except Exception as e:
        print(f"S&P列表错误: {e}")
        return []

def get_historical_prices(symbol, days=HISTORY_DAYS * 2):
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=days + 50)).strftime('%Y-%m-%d')
    url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}?from={start_date}&to={end_date}&apikey={FMP_API_KEY}"
    try:
        response = requests.get(url)
        print(f"FMP价格 {symbol} status: {response.status_code}, preview: {response.text[:100]}")
        if response.status_code == 200:
            data = response.json()
            if 'historical' in data and isinstance(data['historical'], list):
                df = pd.DataFrame(data['historical'])
                df['date'] = pd.to_datetime(df['date'])
                df = df.sort_values('date').set_index('date')
                return df['close']
        return pd.Series()
    except Exception as e:
        print(f"{symbol}价格错误: {e}")
        return pd.Series()

def calculate_market_participation_history(tickers, days=HISTORY_DAYS):
    if not tickers:
        print("无股票列表，参与度为空")
        empty_series = pd.Series(index=pd.date_range(end=datetime.now().date(), periods=days, freq='D')[:-1], dtype=float)
        return empty_series, empty_series
    
    dates = pd.date_range(end=datetime.now().date(), periods=days, freq='D')[:-1]
    participation_20 = pd.Series(index=dates, dtype=float)
    participation_50 = pd.Series(index=dates, dtype=float)
    
    tickers_sample = tickers[:50]  # 限50防慢
    for date in dates:
        hist_days_needed = 50 + (datetime.now() - date).days
        above_20, above_50, total = 0, 0, 0
        for ticker in tickers_sample:
            closes = get_historical_prices(ticker, hist_days_needed)
            if len(closes) < 50 or closes.index[-1].date() < date.date():
                continue
            df_up_to_date = closes[closes.index.date <= date.date()]
            if len(df_up_to_date) < 50:
                continue
            close = df_up_to_date.iloc[-1]
            sma20 = df_up_to_date.rolling(20).mean().iloc[-1]
            sma50 = df_up_to_date.rolling(50).mean().iloc[-1]
            if pd.notna(close) and pd.notna(sma20) and pd.notna(sma50):
                total += 1
                if close > sma20:
                    above_20 += 1
                if close > sma50:
                    above_50 += 1
        if total > 0:
            participation_20[date.date()] = (above_20 / total) * 100
            participation_50[date.date()] = (above_50 / total) * 100
        time.sleep(0.05)
    
    return participation_20, participation_50

def create_charts(fg_series, part20, part50):
    plt.style.use('dark_background')
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    
    ax1.plot(part20.index, part20.values, 'r-', label='高于20日SMA', linewidth=1.5)
    ax1.plot(part50.index, part50.values, 'b-', label='高于50日SMA', linewidth=1.5)
    ax1.set_title('S&P 500 市场参与度 (最近30天)')
    ax1.set_ylabel('百分比 (%)')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax1.tick_params(axis='x', rotation=45)
    
    ax2.plot(fg_series.index, fg_series.values, 'orange', label='CNN Fear & Greed Index', linewidth=1.5)
    ax2.set_title('CNN 恐慌与贪婪指数 (最近30天)')
    ax2.set_ylabel('指数 (0-100)')
    ax2.set_xlabel('日期')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax2.tick_params(axis='x', rotation=45)
    
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
