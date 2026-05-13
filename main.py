import time
import requests
import os
import pytz
import holidays
import pandas as pd
import yfinance as yf
import io
import json
import warnings
import re
import shutil 
import fear_and_greed
import matplotlib
# ⚠️【优化点1】强制使用非交互式后端，大幅节省内存
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
# ⚠️【优化点2】引入垃圾回收机制
import gc 
from datetime import datetime, timedelta

# ---------- 新增依赖 ----------
import discord
from discord.ext import commands, tasks
import google.generativeai as genai
from bs4 import BeautifulSoup
import asyncio
# ------------------------------

# ==========================================
# ⚙️ 全局配置区
# ==========================================

WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # 新增：Discord Bot Token
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")    # 新增：Gemini API Key

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# 新增：频道ID配置
SOURCE_CH_ID = 1468226877159379070
SUMMARY_CH_ID = 1504129510226923542
RAW_LINK_CH_ID = 1436730809749864652

# ------------------------------------------
# ⏰ 时间表 (美东时间 ET)
# ------------------------------------------

# 1. 市场广度 (Market Breadth) 时间点 (收盘后)
BREADTH_SCHEDULE_TIME = "16:30"

# 2. Reddit 热度榜 时间点 (盘前)
REDDIT_SCHEDULE_TIME = "16:42"

# 3. CNN恐慌贪婪指数 时间点 (美东盘中开盘时段每2小时)
FEAR_SCHEDULE_TIMES = ["09:45", "11:45", "13:45", "15:45"]

# ------------------------------------------
# 🤖 机器人信息配置
# ------------------------------------------

# 市场广度 Bot
BREADTH_BOT_NAME = "标普500 广度日报" 
BREADTH_BOT_AVATAR = "https://i.imgur.com/Segc5PF.jpeg"

# Reddit 热度 Bot
REDDIT_BOT_NAME = "Stocksera 舆情热度"
REDDIT_BOT_AVATAR = "https://i.imgur.com/8Qj5X9A.png"

# Fear & Greed Bot
FEAR_BOT_NAME = "CNN 恐慌贪婪指数"
FEAR_BOT_AVATAR = "https://i.imgur.com/Segc5PF.jpeg" 
PREV_FEAR_VALUE = None

# ==========================================
# 🛠️ 辅助函数
# ==========================================

def is_market_holiday(now_et):
    if now_et.weekday() >= 5: return True, "周末休市"
    us_holidays = holidays.US(years=now_et.year) 
    if now_et.date() in us_holidays: return True, f"假期: {us_holidays.get(now_et.date())}"
    return False, None

# ==========================================
# 🔵 模块 1: 市场广度 (Market Breadth)
# ==========================================
def generate_breadth_chart(breadth_20_series, breadth_50_series):
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    
    ax.plot(breadth_20_series.index, breadth_20_series.values, color='#f1c40f', linewidth=2, label='Stocks > 20 Day SMA %')
    ax.plot(breadth_50_series.index, breadth_50_series.values, color='#e74c3c', linewidth=2, label='Stocks > 50 Day SMA %')
    ax.fill_between(breadth_20_series.index, breadth_20_series.values, alpha=0.1, color='#f1c40f')
    
    ax.axhline(y=80, color='#ff5252', linestyle='--', linewidth=1, alpha=0.8)
    ax.text(breadth_20_series.index[0], 81, 'Overbought (80%)', color='#ff5252', fontsize=8)
    ax.axhline(y=20, color='#448aff', linestyle='--', linewidth=1, alpha=0.8)
    ax.text(breadth_20_series.index[0], 21, 'Oversold (20%)', color='#448aff', fontsize=8)

    ax.set_xlim(left=breadth_20_series.index[0], right=breadth_20_series.index[-1])

    last_date = breadth_20_series.index[-1]
    last_val_20 = breadth_20_series.iloc[-1]
    last_val_50 = breadth_50_series.iloc[-1]

    ax.annotate(f'{last_val_20:.1f}%', 
                xy=(last_date, last_val_20), 
                xytext=(-10, 10), textcoords='offset points',
                color='#f1c40f', fontsize=11, fontweight='bold', 
                ha='right', bbox=dict(boxstyle="round,pad=0.3", fc="#2f3136", ec="#f1c40f", alpha=0.8))

    ax.annotate(f'{last_val_50:.1f}%', 
                xy=(last_date, last_val_50), 
                xytext=(-10, -20), textcoords='offset points',
                color='#e74c3c', fontsize=11, fontweight='bold', 
                ha='right', bbox=dict(boxstyle="round,pad=0.3", fc="#2f3136", ec="#e74c3c", alpha=0.8))

    ax.set_title('S&P 500 Market Breadth (20 & 50 Day SMA)', fontsize=12, color='white', pad=15)
    ax.set_ylim(0, 100)
    ax.grid(True, linestyle=':', alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    plt.xticks(rotation=0)
    ax.legend(loc='upper left', frameon=True, facecolor='#2f3136', edgecolor='#2f3136', labelcolor='white')
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=100, facecolor='#2b2d31')
    buf.seek(0)
    plt.close('all') 
    return buf

def run_breadth_task():
    print("📊 启动市场广度统计 (极速省钱+对齐修复版)...")
    
    total_above_20 = None
    total_above_50 = None
    total_stocks_count = None
    chart_buffer = None
    
    try:
        try:
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=10)
            tables = pd.read_html(io.StringIO(resp.text))
            df_tickers = next((df for df in tables if 'Symbol' in df.columns), None)
            tickers = [t.replace('.', '-') for t in df_tickers['Symbol'].tolist()] 
        except:
            print("⚠️ 无法获取完整列表，使用备选名单")
            tickers = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'BRK-B', 'LLY', 'AVGO']

        warnings.simplefilter(action='ignore', category=FutureWarning)
        try:
            if os.path.exists('yfinance.cache'): shutil.rmtree('yfinance.cache')
        except: pass

        batch_size = 100 
        total_batches = (len(tickers) + batch_size - 1) // batch_size
        print(f"📦 共有 {len(tickers)} 只股票，分为 {total_batches} 批处理...")

        for i in range(0, len(tickers), batch_size):
            batch_tickers = tickers[i:i + batch_size]
            print(f"   🚀 处理第 {i//batch_size + 1}/{total_batches} 批...")
            
            try:
                df_batch = yf.download(batch_tickers, period="2y", auto_adjust=True, threads=True, progress=False)
                
                if isinstance(df_batch.columns, pd.MultiIndex):
                    try: closes = df_batch['Close']
                    except KeyError: 
                        try: closes = df_batch['Adj Close']
                        except: closes = df_batch
                elif 'Close' in df_batch.columns:
                    closes = df_batch['Close']
                else:
                    closes = df_batch

                closes = closes.astype('float32')

                sma20 = closes.rolling(window=20).mean()
                sma50 = closes.rolling(window=50).mean()
                
                is_above_20 = (closes > sma20)
                is_above_50 = (closes > sma50)
                is_valid = closes.notna() 
                
                if closes.index.tz is not None:
                    is_above_20.index = is_above_20.index.tz_localize(None)
                    is_above_50.index = is_above_50.index.tz_localize(None)
                    is_valid.index = is_valid.index.tz_localize(None)

                batch_sum_20 = is_above_20.sum(axis=1)
                batch_sum_50 = is_above_50.sum(axis=1)
                batch_count = is_valid.sum(axis=1)
                
                if total_above_20 is None:
                    total_above_20 = batch_sum_20
                    total_above_50 = batch_sum_50
                    total_stocks_count = batch_count
                else:
                    total_above_20 = total_above_20.add(batch_sum_20, fill_value=0)
                    total_above_50 = total_above_50.add(batch_sum_50, fill_value=0)
                    total_stocks_count = total_stocks_count.add(batch_count, fill_value=0)

            except Exception as e:
                print(f"⚠️ 批次跳过: {e}")
            
            del df_batch
            try: del closes; del sma20; del sma50
            except: pass
            gc.collect() 
            
        print("🧮 合并计算中...")
        total_stocks_count = total_stocks_count.replace(0, 1) 
        
        daily_breadth_20 = (total_above_20 / total_stocks_count) * 100
        daily_breadth_50 = (total_above_50 / total_stocks_count) * 100

        daily_breadth_20 = daily_breadth_20.sort_index()
        daily_breadth_50 = daily_breadth_50.sort_index()

        chart_buffer = generate_breadth_chart(daily_breadth_20.tail(252), daily_breadth_50.tail(252))

        last_val_20 = daily_breadth_20.iloc[-1]
        prev_val_20 = daily_breadth_20.iloc[-2] if len(daily_breadth_20) > 1 else last_val_20
        diff_20 = last_val_20 - prev_val_20
        trend_20 = f"升高 {abs(diff_20):.1f}%" if diff_20 >= 0 else f"降低 {abs(diff_20):.1f}%"

        last_val_50 = daily_breadth_50.iloc[-1]
        prev_val_50 = daily_breadth_50.iloc[-2] if len(daily_breadth_50) > 1 else last_val_50
        diff_50 = last_val_50 - prev_val_50
        trend_50 = f"升高 {abs(diff_50):.1f}%" if diff_50 >= 0 else f"降低 {abs(diff_50):.1f}%"

        tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(tz)
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        date_str = f"{now_et.month}月{now_et.day}日{weekdays[now_et.weekday()]}"

        desc_text = (
            f"**20日参与度:** `{last_val_20:.1f}%` (比昨日{trend_20})\n"
            f"**50日参与度:** `{last_val_50:.1f}%` (比昨日{trend_50})"
        )

        payload_data = {
            "username": BREADTH_BOT_NAME,
            "avatar_url": BREADTH_BOT_AVATAR,
            "embeds": [{
                "title": f"市场参与度（{date_str}）",
                "description": desc_text,
                "color": 0xF1C40F,
                "image": {"url": "attachment://chart.png"}
            }]
        }
        
        files = {'file': ('chart.png', chart_buffer, 'image/png')}
        requests.post(WEBHOOK_URL, data={'payload_json': json.dumps(payload_data)}, files=files)
        print(f"✅ 广度报告已推送")

    except Exception as e:
        print(f"❌ 广度任务异常: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        print("🧹 最终清理...")
        if chart_buffer:
            try: chart_buffer.close()
            except: pass
        try: del total_above_20; del total_above_50; del daily_breadth_20;
        except: pass
        gc.collect()

# ==========================================
# 🛠️ 辅助函数: 计算排名变化
# ==========================================
def calculate_rank_change(current_rank, old_rank):
    if not old_rank or old_rank == 0: return "new"
    diff = old_rank - current_rank
    if diff > 0: return f"🔺{diff}"
    elif diff < 0: return f"🔻{abs(diff)}"
    else: return "➖"

# ==========================================
# 🔴 模块 2: Reddit 热度榜 (完整修复+完美对齐版)
# ==========================================

def get_apewisdom_data():
    print("📡 正在从 ApeWisdom 获取数据...")
    url = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1"
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code == 200:
            data = response.json()
            return data.get('results', [])[:30]
        else:
            print(f"⚠️ ApeWisdom API 错误: {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ 获取 ApeWisdom 数据失败: {e}")
        return None

def calculate_rank_change_reddit(current_rank, old_rank):
    if not old_rank or old_rank == 0: return "🆕"
    diff = old_rank - current_rank
    if diff > 0: return f"🔺{diff}"
    elif diff < 0: return f"🔻{abs(diff)}"
    else: return "➖"

def run_reddit_task():
    data = get_apewisdom_data()
    if not data: return

    desc_lines = []
    for item in data:
        rank = item.get('rank', 0)
        ticker = item.get('ticker', 'Unknown')
        name = item.get('name', '')
        mentions = item.get('mentions', 0)
        rank_24h = item.get('rank_24h_ago', 0)
        
        change_raw = calculate_rank_change_reddit(rank, rank_24h)
        name = name.replace("&amp;", "&").replace("\n", " ").strip()
        if len(name) > 8: name = name[:8] + "."
        
        header_block = f"` {change_raw:<5} {rank:02d}. `"
        line = f"{header_block} **${ticker}** ({name}) 提及 `{mentions}`次"
        desc_lines.append(line)

    date_str = datetime.now().strftime('%m月%d日') 
    payload = {
        "username": "散户买什么？", 
        "avatar_url": "https://i.imgur.com/iXlOzKP.png", 
        "embeds": [{
            "title": f"Reddit 24H 热度榜（{date_str}）",
            "description": "\n".join(desc_lines),
            "color": 0xFF4500, 
        }]
    }
    
    try:
        requests.post(WEBHOOK_URL, json=payload)
        print("✅ ApeWisdom Top30 推送成功 (数字独立高亮版)")
    except Exception as e:
        print(f"❌ 推送失败: {e}")
        
    gc.collect()

# ==========================================
# 🟠 模块 3: CNN 恐慌贪婪指数
# ==========================================
def run_fear_greed_task():
    global PREV_FEAR_VALUE
    print("📊 启动恐慌贪婪指数抓取...")

    try:
        fg = fear_and_greed.get()
        current_value = round(fg.value, 1)
        stage_desc = str(fg.description).strip().lower()

        stage_map = {
            "extreme greed": "极度贪婪 (76-100)",
            "greed": "贪婪 (56-75)",
            "neutral": "中性 (45-55)",
            "fear": "恐慌 (25-44)",
            "extreme fear": "极度恐慌 (0-24)"
        }
        stage_cn = stage_map.get(stage_desc, stage_desc)

        change_text = "初始化 (无对比数据)"
        if PREV_FEAR_VALUE is not None:
            diff = current_value - PREV_FEAR_VALUE
            if diff > 0: change_text = f"→ 升高了 {diff:.1f}"
            elif diff < 0: change_text = f"→ 降低了 {abs(diff):.1f}"
            else: change_text = "→ 保持不变"

        PREV_FEAR_VALUE = current_value

        payload = {
            "username": FEAR_BOT_NAME,
            "avatar_url": FEAR_BOT_AVATAR,
            "embeds": [{
                "title": "CNN 市场情绪监测",
                "description": f"**当前情绪:** {stage_cn}\n"
                               f"**当前数值:** `{current_value}`\n"
                               f"**环比上一期:** {change_text}",
                "color": 0x9B59B6
            }]
        }

        requests.post(WEBHOOK_URL, json=payload)
        print("✅ 恐慌贪婪指数推送成功")

    except Exception as e:
        print(f"❌ 获取恐慌贪婪指数失败: {e}")

# ==========================================
# 🚀 新增模块: Discord 机器人与期权大单监控
# ==========================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

async def process_option_message(message: discord.Message) -> bool:
    """处理捕获的期权大单消息"""
    text_to_check = message.content
    if message.embeds:
        for embed in message.embeds:
            if embed.title: text_to_check += f" {embed.title}"
            if embed.description: text_to_check += f" {embed.description}"

    # 兼容多种常见的符号输入
    if "期权大单" not in text_to_check:
        return False

    # 提取URL
    urls = re.findall(r'http[s]?://[^\s<>"]+|www\.[^\s<>"]+', text_to_check)
    if not urls: 
        return False
    target_url = urls[0]

    # 爬取并请求Gemini提取摘要
    try:
        resp = await asyncio.to_thread(requests.get, target_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        article_text = soup.get_text(separator='\n', strip=True)[:3000] # 截断防止超出Token
        
        if not GEMINI_API_KEY:
            summary = "⚠️ 缺失 Gemini API Key"
        else:
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = f"请简明扼要地总结以下期权大单文章的核心信息，提炼最重要的交易数据：\n\n{article_text}"
            # 异步调用模型
            genai_resp = await asyncio.to_thread(model.generate_content, prompt)
            summary = genai_resp.text
    except Exception as e:
        summary = f"网页获取或解析失败: {e}"

    # 分发至指定频道
    summary_ch = bot.get_channel(SUMMARY_CH_ID)
    raw_link_ch = bot.get_channel(RAW_LINK_CH_ID)

    if summary_ch:
        embed = discord.Embed(title="📊 期权大单总结", description=summary, color=0x3498db)
        embed.add_field(name="原文链接", value=target_url, inline=False)
        await summary_ch.send(embed=embed)
    
    if raw_link_ch:
        await raw_link_ch.send(target_url)

    # 删除原消息
    try:
        await message.delete()
    except Exception as e:
        print(f"⚠️ 删除原消息失败 (检查Bot权限): {e}")

    return True

@bot.event
async def on_message(message):
    # 如果发出消息的是机器人自己，忽略
    if message.author == bot.user:
        return
        
    # 监听特定频道
    if message.channel.id == SOURCE_CH_ID:
        await process_option_message(message)

    await bot.process_commands(message)

@bot.command(name="测试")
async def test_cmd(ctx):
    """测试命令：从源频道扫描最近10条进行处理验证"""
    if ctx.channel.id != SOURCE_CH_ID:
        await ctx.send("请在源频道中发送 `/测试` 命令以进行验证。")
        return
        
    await ctx.send("🔍 正在扫描此频道最近 10 条消息测试匹配逻辑...")
    count = 0
    async for msg in ctx.channel.history(limit=10):
        if msg.id == ctx.message.id: 
            continue
        if await process_option_message(msg):
            count += 1
            
    await ctx.send(f"✅ 测试结束，已成功匹配并处理了 {count} 条包含期权大单的消息。")

# ==========================================
# 🚀 整合原有任务循环 (转换为 Discord 异步任务)
# ==========================================

last_run_time_str = ""

@tasks.loop(seconds=30)
async def scheduled_legacy_tasks():
    global last_run_time_str
    try:
        tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(tz)
        current_str = now_et.strftime("%H:%M")
        is_holiday, holiday_name = is_market_holiday(now_et)

        if current_str != last_run_time_str:
            print(f"⏰ {current_str} ET (Market Open: {not is_holiday})")
            
            if not is_holiday:
                # 使用 to_thread 避免原本同步的 requests 阻塞 Discord Bot 的心跳
                if current_str == BREADTH_SCHEDULE_TIME:
                    print(f"🔔 触发 市场广度: {current_str}")
                    await asyncio.to_thread(run_breadth_task)
                    
                if current_str == REDDIT_SCHEDULE_TIME:
                    print(f"🔔 触发 Reddit 热度榜: {current_str}")
                    await asyncio.to_thread(run_reddit_task)
                    
                if current_str in FEAR_SCHEDULE_TIMES:
                    print(f"🔔 触发 恐慌贪婪指数: {current_str}")
                    await asyncio.to_thread(run_fear_greed_task)
                    
            else:
                all_times = [BREADTH_SCHEDULE_TIME, REDDIT_SCHEDULE_TIME] + FEAR_SCHEDULE_TIMES
                if current_str in all_times:
                    print(f"😴 今日休市 ({holiday_name})，跳过推送")

            last_run_time_str = current_str

    except Exception as e:
        print(f"⚠️ 调度循环报错: {e}")

@bot.event
async def on_ready():
    print(f"🤖 Bot 已登录: {bot.user}")
    print("-------------- 系统自检 --------------")
    # 为了防止启动时自检卡住Bot登录流程，可选择不自检或放入to_thread
    print("✅ 系统正常运行，定时监听任务开始...")
    print("--------------------------------------")
    scheduled_legacy_tasks.start()

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ 缺少 DISCORD_BOT_TOKEN 环境变量，无法启动 Bot 模式。")
    else:
        bot.run(DISCORD_TOKEN)
