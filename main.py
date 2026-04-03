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
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ==========================================
# ⚙️ 全局配置区
# ==========================================

WEBHOOK_URL = os.getenv("WEBHOOK_URL") 

# ------------------------------------------
# ⏰ 时间表 (美东时间 ET)
# ------------------------------------------

# 1. FedWatch (美联储观察) 时间点
FED_SCHEDULE_TIMES = ["08:31", "10:31", "15:01"]

# 2. 市场广度 (Market Breadth) 时间点 (收盘后)
BREADTH_SCHEDULE_TIME = "16:30"

# 3. Reddit 热度榜 时间点 (盘前)
REDDIT_SCHEDULE_TIME = "16:42"

# 4. CNN恐慌贪婪指数 时间点 (美东盘中开盘时段每小时)
FEAR_SCHEDULE_TIMES = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]

# ------------------------------------------
# 🤖 机器人信息配置
# ------------------------------------------

# FedWatch Bot
ENABLE_FED_BOT = False  # 如果不需要跑Fed，保持False
FED_BOT_NAME = "CME FedWatch Bot"
FED_BOT_AVATAR = "https://i.imgur.com/d8KLt6Z.png"

# 市场广度 Bot
BREADTH_BOT_NAME = "标普500 广度日报" 
BREADTH_BOT_AVATAR = "https://i.imgur.com/Segc5PF.jpeg"

# Reddit 热度 Bot (新)
REDDIT_BOT_NAME = "Stocksera 舆情热度"
REDDIT_BOT_AVATAR = "https://i.imgur.com/8Qj5X9A.png" # 这里的头像可以使用Reddit Logo

# Fear & Greed Bot (新)
FEAR_BOT_NAME = "CNN 恐慌贪婪指数"
FEAR_BOT_AVATAR = "https://i.imgur.com/K3Vw8Y7.png" 
PREV_FEAR_VALUE = None

PREV_CUT_PROB = None

# 【保底策略】万一爬虫抓不到日期/利率
BACKUP_SCHEDULE = [
    "2025-12-10", "2026-01-28", "2026-03-18", "2026-05-06", 
    "2026-06-17", "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16"
]
DEFAULT_BACKUP_RATE = 3.50 

# ==========================================
# 🛠️ 辅助函数
# ==========================================

def parse_date_string(date_text):
    if not date_text: return None
    try:
        clean_text = re.sub(r'[^\w\s,]', '', date_text).strip()
        try:
            dt = datetime.strptime(clean_text, "%b %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except: pass
        try:
            dt = datetime.strptime(clean_text, "%B %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except: pass
        return None
    except:
        return None

def get_backup_meeting_date():
    tz = pytz.timezone('US/Eastern')
    today_str = datetime.now(tz).strftime("%Y-%m-%d")
    for meeting_date in BACKUP_SCHEDULE:
        if meeting_date >= today_str:
            return meeting_date
        return "TBD"

def is_market_holiday(now_et):
    if now_et.weekday() >= 5: return True, "周末休市"
    us_holidays = holidays.US(years=now_et.year) 
    if now_et.date() in us_holidays: return True, f"假期: {us_holidays.get(now_et.date())}"
    return False, None

def get_bar(p):
    length = 15
    filled = int(p / 100 * length)
    return "█" * filled + "░" * (length - filled)

def format_target_label(target_str, current_rate_base):
    try:
        lower_bound = float(target_str.split('-')[0].strip())
        if abs(lower_bound - current_rate_base) <= 0.05:
            return f"{target_str} (维持)"
        elif lower_bound < current_rate_base:
            return f"{target_str} (降息)"
        else:
            return f"{target_str} (加息)"
    except:
        return target_str

# ==========================================
# 🟢 模块 1: 降息概率 (FedWatch)
# ==========================================

def scrape_header_info(driver, page_text):
    rate = None
    meeting_date = None
    try:
        rate_match = re.search(r"Current.*?Rate.*?(\d+\.\d+)", page_text, re.IGNORECASE)
        if rate_match:
            val = float(rate_match.group(1))
            if 0.0 <= val <= 10.0: rate = val
    except: pass

    try:
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        date_pattern = re.compile(r'(?:' + '|'.join(months) + r')\.?\s+\d{1,2},?\s+\d{4}', re.IGNORECASE)
        top_text = page_text[:2000]
        dates_found = date_pattern.findall(top_text)
        tz = pytz.timezone('US/Eastern')
        today = datetime.now(tz).date()

        for d_str in dates_found:
            parsed = parse_date_string(d_str)
            if parsed:
                p_date = datetime.strptime(parsed, "%Y-%m-%d").date()
                if p_date >= today:
                    meeting_date = parsed
                    break 
    except: pass
    return rate, meeting_date

def get_fed_data():
    if not ENABLE_FED_BOT:
        print("⏸️ [系统] FedWatch Bot 已禁用，跳过抓取...")
        return None

    print(f"⚡ 启动 Chromium...")
    options = Options()
    options.binary_location = "/usr/bin/chromium" 
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

    driver = None
    result = {"current_base_rate": None, "next_meeting": None, "data": []}
      
    try:
        service = Service("/usr/bin/chromedriver") 
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.get("https://www.investing.com/central-banks/fed-rate-monitor")
        time.sleep(5) 
        
        page_text = driver.find_element(By.TAG_NAME, "body").text
        scraped_rate, scraped_date = scrape_header_info(driver, page_text)
        
        if scraped_rate: result["current_base_rate"] = scraped_rate
        else: result["current_base_rate"] = DEFAULT_BACKUP_RATE
            
        if scraped_date: result["next_meeting"] = scraped_date
        else: result["next_meeting"] = get_backup_meeting_date()

        data_points = []
        try:
            tables = driver.find_elements(By.TAG_NAME, "table")
            target_table = None
            for tbl in tables:
                if "%" in tbl.text and len(tbl.find_elements(By.TAG_NAME, "tr")) < 15:
                    target_table = tbl
                    break
            if not target_table and tables: target_table = tables[0]

            if target_table:
                rows = target_table.find_elements(By.TAG_NAME, "tr")
                for row in rows:
                    cols = row.find_elements(By.TAG_NAME, "td")
                    if len(cols) >= 2:
                        txt0, txt1 = cols[0].text.strip(), cols[1].text.strip()
                        try:
                            if "%" in txt0: prob, target = float(txt0.replace("%", "")), txt1
                            elif "%" in txt1: prob, target = float(txt1.replace("%", "")), txt0
                            else: continue
                            data_points.append({"prob": prob, "target": target})
                        except: continue
        except: pass

        if not data_points: return None
        result["data"] = data_points
        return result

    except Exception as e:
        print(f"❌ Error: {e}")
        return None
    finally:
        if driver:
            try: driver.quit()
            except: pass

def send_fed_embed(data):
    global PREV_CUT_PROB
    if not data or not data['data']: return
    
    base_rate = data.get("current_base_rate")
    next_meeting_date = data.get("next_meeting")
    
    current_cut_prob = 0.0
    target_cut_lower = base_rate - 0.25
    
    cut_item = None
    rest_items = []
    
    for item in data['data']:
        try:
            lower = float(item['target'].split('-')[0].strip())
            if abs(lower - target_cut_lower) <= 0.05:
                cut_item = item
                current_cut_prob = item['prob']
            else:
                rest_items.append(item)
        except:
            rest_items.append(item)
    
    delta = 0.0
    if PREV_CUT_PROB is not None:
        delta = current_cut_prob - PREV_CUT_PROB
    PREV_CUT_PROB = current_cut_prob
    
    trend_title = "📉 降息趋势变动"
    if not cut_item and current_cut_prob == 0: trend_text = "无降息预期"
    elif delta > 0.1: trend_text = f"概率上升 +{delta:.1f}% 🔥"
    elif delta < -0.1: trend_text = f"概率下降 {delta:.1f}% ❄️"
    else: trend_text = "稳定"

    display_list = []
    if cut_item: display_list.append(cut_item)
    if rest_items:
        rest_items.sort(key=lambda x: x['prob'], reverse=True)
        display_list.append(rest_items[0])
    
    if not display_list:
        data['data'].sort(key=lambda x: x['prob'], reverse=True)
        display_list = data['data'][:2]

    all_sorted = sorted(data['data'], key=lambda x: x['prob'], reverse=True)
    top1_real = all_sorted[0]
    
    label1_raw = format_target_label(top1_real['target'], base_rate)
    if "(维持)" in label1_raw: consensus_short = "⏸️ 维持利率 (Hold)"
    elif "(降息)" in label1_raw: consensus_short = "📉 降息 (Cut)"
    else: consensus_short = "📈 加息 (Hike)"

    desc_lines = [f"**🗓️ 下次会议:** `{next_meeting_date}`\n"]
    
    for item in display_list:
        label = format_target_label(item['target'], base_rate)
        desc_lines.append(f"**目标: {label}**")
        desc_lines.append(f"`{get_bar(item['prob'])}` **{item['prob']}%**\n")
    
    desc_lines.append("\n------------------------")

    payload = {
        "username": FED_BOT_NAME,
        "avatar_url": FED_BOT_AVATAR,
        "embeds": [{
            "title": "🏛️ CME FedWatch™",
            "description": "\n".join(desc_lines),
            "color": 0x3498DB,
            "fields": [
                {"name": trend_title, "value": trend_text, "inline": True},
                {"name": "💡 华尔街共识", "value": consensus_short, "inline": True},
                {"name": "📊 当前基准利率", "value": f"{base_rate}%", "inline": False}
            ],
            "footer": {"text": f"Updated at {datetime.now().strftime('%H:%M')} ET | Auto-Scraped"}
        }]
    }
    try: requests.post(WEBHOOK_URL, json=payload)
    except Exception as e: print(f"❌ 推送失败: {e}")

# ==========================================
# 🔵 模块 2: 市场广度 (Market Breadth)
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

def get_market_sentiment(p):
    if p > 80: return "🔥🔥 **深度火热**"
    if p > 60: return "🔥 **火热**"      
    if p < 20: return "❄️❄️ **深度寒冷**"
    if p < 40: return "❄️ **寒冷**"      
    return "🍃 **稳定**"      

def run_breadth_task():
    print("📊 启动市场广度统计 (极速省钱+对齐修复版)...")
    
    # 结果累加器
    total_above_20 = None
    total_above_50 = None
    total_stocks_count = None
    
    chart_buffer = None
    
    try:
        # 1. 获取标普500列表
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

        # 批次处理
        batch_size = 100 
        total_batches = (len(tickers) + batch_size - 1) // batch_size
        print(f"📦 共有 {len(tickers)} 只股票，分为 {total_batches} 批处理...")

        for i in range(0, len(tickers), batch_size):
            batch_tickers = tickers[i:i + batch_size]
            print(f"   🚀 处理第 {i//batch_size + 1}/{total_batches} 批...")
            
            try:
                # auto_adjust=True, threads=True
                df_batch = yf.download(batch_tickers, period="2y", auto_adjust=True, threads=True, progress=False)
                
                # 🛠️ 数据清洗与对齐
                if isinstance(df_batch.columns, pd.MultiIndex):
                    try: closes = df_batch['Close']
                    except KeyError: 
                        try: closes = df_batch['Adj Close']
                        except: closes = df_batch
                elif 'Close' in df_batch.columns:
                    closes = df_batch['Close']
                else:
                    closes = df_batch

                # 强制 float32
                closes = closes.astype('float32')

                # 计算均线
                sma20 = closes.rolling(window=20).mean()
                sma50 = closes.rolling(window=50).mean()
                
                is_above_20 = (closes > sma20)
                is_above_50 = (closes > sma50)
                is_valid = closes.notna() 
                
                # ⚠️ 【关键修复】确保索引是 DatetimeIndex 并且时区一致
                # 有些时候 yfinance 返回的索引可能带时区，也可能不带，导致相加报错
                if closes.index.tz is not None:
                    # 统一移除时区信息，只保留日期
                    is_above_20.index = is_above_20.index.tz_localize(None)
                    is_above_50.index = is_above_50.index.tz_localize(None)
                    is_valid.index = is_valid.index.tz_localize(None)

                batch_sum_20 = is_above_20.sum(axis=1)
                batch_sum_50 = is_above_50.sum(axis=1)
                batch_count = is_valid.sum(axis=1)
                
                # 累加 (使用 add 自动对齐日期)
                if total_above_20 is None:
                    total_above_20 = batch_sum_20
                    total_above_50 = batch_sum_50
                    total_stocks_count = batch_count
                else:
                    # fill_value=0 非常重要，防止日期错位产生 NaN
                    total_above_20 = total_above_20.add(batch_sum_20, fill_value=0)
                    total_above_50 = total_above_50.add(batch_sum_50, fill_value=0)
                    total_stocks_count = total_stocks_count.add(batch_count, fill_value=0)

            except Exception as e:
                print(f"⚠️ 批次跳过: {e}")
            
            # 内存清理
            del df_batch
            try: del closes; del sma20; del sma50
            except: pass
            gc.collect() 
            
        # 3. 计算最终百分比
        print("🧮 合并计算中...")
        total_stocks_count = total_stocks_count.replace(0, 1) 
        
        daily_breadth_20 = (total_above_20 / total_stocks_count) * 100
        daily_breadth_50 = (total_above_50 / total_stocks_count) * 100

        # 排序索引，防止画图连线混乱
        daily_breadth_20 = daily_breadth_20.sort_index()
        daily_breadth_50 = daily_breadth_50.sort_index()

        # 4. 生成图表
        chart_buffer = generate_breadth_chart(daily_breadth_20.tail(252), daily_breadth_50.tail(252))
        
        current_p20 = daily_breadth_20.iloc[-1]
        current_p50 = daily_breadth_50.iloc[-1]
        
        sentiment_20 = get_market_sentiment(current_p20)
        sentiment_50 = get_market_sentiment(current_p50)

        # 5. 推送
        payload_data = {
            "username": BREADTH_BOT_NAME,
            "avatar_url": BREADTH_BOT_AVATAR,
            "embeds": [{
                "title": "S&P 500 Market Breadth", # 标题改英文防止乱码
                "description": f"**Date:** `{datetime.now().strftime('%Y-%m-%d')}`\n\n"
                               f"**Stocks > SMA20:** **{current_p20:.1f}%**\n"
                               f"{sentiment_20}\n\n"
                               f"**Stocks > SMA50:** **{current_p50:.1f}%**\n"
                               f"{sentiment_50}",
                "color": 0xF1C40F,
                "image": {"url": "attachment://chart.png"},
                "footer": {
                    "text": f"S&P 500 stocks above 20/50 day moving average.\n(Sample size: {len(tickers)})"
                }
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
    """
    计算排名变化并返回图标
    """
    if not old_rank or old_rank == 0:
        return "new" # 新上榜
    
    diff = old_rank - current_rank
    
    if diff > 0:
        return f"🔺{diff}" # 排名上升
    elif diff < 0:
        return f"🔻{abs(diff)}" # 排名下降
    else:
        return "➖" # 持平

# ==========================================
# 🔴 模块 3: Reddit 热度榜 (完整修复+完美对齐版)
# ==========================================

def get_apewisdom_data():
    """
    使用 ApeWisdom API 获取 Reddit (WSB/Stocks) 热门股票
    """
    print("📡 正在从 ApeWisdom 获取数据...")
    url = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1"
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=20)
        
        if response.status_code == 200:
            data = response.json()
            results = data.get('results', [])
            return results[:30] # Top 30
        else:
            print(f"⚠️ ApeWisdom API 错误: {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ 获取 ApeWisdom 数据失败: {e}")
        return None

def calculate_rank_change_reddit(current_rank, old_rank):
    """
    计算排名变化图标
    """
    if not old_rank or old_rank == 0:
        return "🆕"
    
    diff = old_rank - current_rank
    if diff > 0: return f"🔺{diff}"
    elif diff < 0: return f"🔻{abs(diff)}"
    else: return "➖"

def run_reddit_task():
    # 1. 获取数据
    data = get_apewisdom_data()
    if not data:
        return

    desc_lines = []
    
    for item in data:
        rank = item.get('rank', 0)
        ticker = item.get('ticker', 'Unknown')
        name = item.get('name', '')
        mentions = item.get('mentions', 0)
        rank_24h = item.get('rank_24h_ago', 0)
        
        # 2. 获取变动字符
        change_raw = calculate_rank_change_reddit(rank, rank_24h)
        
        # 3. 名字处理
        name = name.replace("&amp;", "&").replace("\n", " ").strip()
        if len(name) > 8: name = name[:8] + "."
        
        # 4. 头部排版 (保持黑底以维持对齐)
        header_block = f"` {change_raw:<5} {rank:02d}. `"
        
        # 5. 拼接 (修改点：只给 mentions 数字加了反引号 ` `)
        # 效果: `🔺5    02.` **$NVDA** (NVIDIA.) 提及 `1024`次
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
# 🟠 模块 4: CNN 恐慌贪婪指数
# ==========================================
def run_fear_greed_task():
    global PREV_FEAR_VALUE
    print("📊 启动恐慌贪婪指数抓取...")

    try:
        fg = fear_and_greed.get()
        current_value = round(fg.value, 1)
        
        # 💡 修复点：将API获取的描述强制转为小写并去除空格，确保100%命中翻译
        stage_desc = str(fg.description).strip().lower()

        # 翻译阶段描述 (字典键值全部改为小写)
        stage_map = {
            "extreme greed": "极度贪婪",
            "greed": "贪婪",
            "neutral": "中性",
            "fear": "恐慌",
            "extreme fear": "极度恐慌"
        }
        stage_cn = stage_map.get(stage_desc, stage_desc)

        # 计算并格式化变动 (使用文本箭头，无emoji)
        change_text = "初始化 (无对比数据)"
        if PREV_FEAR_VALUE is not None:
            diff = current_value - PREV_FEAR_VALUE
            if diff > 0:
                change_text = f"→ 升高了 {diff:.1f}"
            elif diff < 0:
                change_text = f"→ 降低了 {abs(diff):.1f}"
            else:
                change_text = "→ 保持不变"

        # 更新上一次的数据记录
        PREV_FEAR_VALUE = current_value

        # 构建Embed排版 (纯文本排版，无图表)
        payload = {
            "username": FEAR_BOT_NAME,
            "avatar_url": FEAR_BOT_AVATAR,
            "embeds": [{
                "title": "CNN 市场情绪监测",
                "description": f"**当前情绪:** {stage_cn}\n"
                               f"**当前数值:** `{current_value}`\n"
                               f"**环比上小时:** {change_text}",
                "color": 0x9B59B6
            }]
        }

        requests.post(WEBHOOK_URL, json=payload)
        print("✅ 恐慌贪婪指数推送成功")

    except Exception as e:
        print(f"❌ 获取恐慌贪婪指数失败: {e}")

# ==========================================
# 🚀 主程序
# ==========================================
if __name__ == "__main__":
    print("🚀 监控服务已启动")
    
    # --- 启动自检 (测试模式) ---
    print("-------------- 系统自检 --------------")
    
    if ENABLE_FED_BOT:
        print("🧪 [测试] FedWatch...")
        fed_data = get_fed_data()
        if fed_data: send_fed_embed(fed_data)
    else:
        print("⏸️ [测试] FedWatch 已禁用")

    print("🧪 [测试] 市场广度...")
    run_breadth_task()
    
    print("🧪 [测试] Reddit 热度榜...")
    run_reddit_task()
    
    print("🧪 [测试] 恐慌贪婪指数...")
    run_fear_greed_task()
    
    print("✅ 自检结束，进入定时监听模式...")
    print("--------------------------------------")

    last_run_time_str = ""
    
    while True:
        try:
            tz = pytz.timezone('US/Eastern')
            now_et = datetime.now(tz)
            current_str = now_et.strftime("%H:%M")
            is_holiday, holiday_name = is_market_holiday(now_et)

            if current_str != last_run_time_str:
                print(f"⏰ {current_str} ET (Market Open: {not is_holiday})")
                
                # 只有在非假期/非周末时才推送
                if not is_holiday:
                    # 1. FedWatch
                    if current_str in FED_SCHEDULE_TIMES:
                        if ENABLE_FED_BOT:
                            print(f"🔔 触发 FedWatch: {current_str}")
                            data = get_fed_data()
                            if data: send_fed_embed(data)
                        else:
                            print(f"⏸️ 时间到，但 FedBot 禁用")
                    
                    # 2. Market Breadth
                    if current_str == BREADTH_SCHEDULE_TIME:
                        print(f"🔔 触发 市场广度: {current_str}")
                        run_breadth_task()
                        
                    # 3. Reddit Trending
                    if current_str == REDDIT_SCHEDULE_TIME:
                        print(f"🔔 触发 Reddit 热度榜: {current_str}")
                        run_reddit_task()
                        
                    # 4. CNN Fear & Greed (新增)
                    if current_str in FEAR_SCHEDULE_TIMES:
                        print(f"🔔 触发 恐慌贪婪指数: {current_str}")
                        run_fear_greed_task()
                        
                else:
                    # 假期/周末时，只打印心跳
                    all_times = FED_SCHEDULE_TIMES + [BREADTH_SCHEDULE_TIME, REDDIT_SCHEDULE_TIME] + FEAR_SCHEDULE_TIMES
                    if current_str in all_times:
                        print(f"😴 今日休市 ({holiday_name})，跳过推送")

                last_run_time_str = current_str
        
        except Exception as e:
            print(f"⚠️ 主循环报错: {e}")
            time.sleep(5)
            
        time.sleep(30)
