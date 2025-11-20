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
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ==========================================
# ⚙️ 全局配置区
# ==========================================

WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
NEXT_MEETING_DATE = "2025-12-10"

# ⏰ 时间表 (美东时间 ET)
FED_SCHEDULE_TIMES = ["08:31", "09:31", "11:31", "13:31", "15:31"]
BREADTH_SCHEDULE_TIME = "16:30"

# 🎭 机器人配置
FED_BOT_NAME = "🏛️ 美联储利率观察"
FED_BOT_AVATAR = "https://cdn-icons-png.flaticon.com/512/2156/2156009.png" 

BREADTH_BOT_NAME = "📊 标普500 广度日报"
BREADTH_BOT_AVATAR = "https://cdn-icons-png.flaticon.com/512/3310/3310665.png" 

PREV_CUT_PROB = None

# ==========================================
# 🛠️ 辅助函数
# ==========================================
def is_market_holiday(now_et):
    if now_et.weekday() >= 5: return True, "周末休市"
    us_holidays = holidays.US(years=now_et.year) 
    if now_et.date() in us_holidays: return True, f"假期: {us_holidays.get(now_et.date())}"
    return False, None

def get_bar(p):
    return "█" * int(p//10) + "░" * (10 - int(p//10))

def get_market_sentiment(p):
    """
    根据百分比判断市场情绪 (5级分类)
    """
    if p > 80: return "🔥🔥 **深度火热**"
    if p > 60: return "🔥 **市场火热**"
    if p < 20: return "❄️❄️ **深度寒冷**"
    if p < 40: return "❄️ **市场寒冷**"
    return "🍃 **市场稳定**"

# ==========================================
# 🟢 模块 1: 降息概率 (Selenium)
# ==========================================
def get_fed_data():
    print(f"⚡ 启动 Chromium 抓取 FedWatch...")
    options = Options()
    options.binary_location = "/usr/bin/chromium"
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

    driver = None
    try:
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(45)
        
        driver.get("https://www.investing.com/central-banks/fed-rate-monitor")
        time.sleep(8)
        
        data_points = []
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
        
        if not data_points: return None
        data_points.sort(key=lambda x: x['prob'], reverse=True)
        return {"current": "Unknown", "data": data_points[:2]}

    except Exception as e:
        print(f"❌ Selenium 抓取错误: {e}")
        return None
    finally:
        if driver:
            try: driver.quit()
            except: pass

def send_fed_embed(data):
    global PREV_CUT_PROB
    if not data or not data['data']: return
    
    top1 = data['data'][0]
    cut_prob_value = top1['prob'] 
    
    delta = 0.0
    if PREV_CUT_PROB is not None: delta = cut_prob_value - PREV_CUT_PROB
    PREV_CUT_PROB = cut_prob_value
    
    trend_str = "稳定"
    if delta > 0.1: trend_str = f"概率上升 +{delta:.1f}% 🔥"
    elif delta < -0.1: trend_str = f"概率下降 {delta:.1f}% ❄️"

    desc = [
        f"**🗓️ 下次会议:** `{NEXT_MEETING_DATE}`",
        "",
        f"**目标: {top1['target']}**", 
        f"{get_bar(top1['prob'])} **{top1['prob']}%**",
        ""
    ]
    
    payload = {
        "username": FED_BOT_NAME,
        "avatar_url": FED_BOT_AVATAR,
        "embeds": [{
            "title": "🏛️ CME FedWatch™ (降息预期)",
            "description": "\n".join(desc),
            "color": 0x3498DB,
            "fields": [{"name": "📊 趋势变动", "value": trend_str, "inline": True}],
            "footer": {"text": f"Updated at {datetime.now().strftime('%H:%M')} ET"}
        }]
    }
    try: requests.post(WEBHOOK_URL, json=payload)
    except Exception as e: print(f"❌ 推送失败: {e}")

# ==========================================
# 🔵 模块 2: 市场广度 (布局调整版)
# ==========================================
def generate_breadth_chart(breadth_20_series, breadth_50_series):
    """生成市场广度折线图，同时显示 20日和 50日线"""
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    
    # 绘制 20日线 (黄色)
    ax.plot(breadth_20_series.index, breadth_20_series.values, 
            color='#f1c40f', linewidth=2, label='Stocks > 20 Day SMA %')
    
    # 绘制 50日线 (红色)
    ax.plot(breadth_50_series.index, breadth_50_series.values, 
            color='#e74c3c', linewidth=2, label='Stocks > 50 Day SMA %')
    
    ax.fill_between(breadth_20_series.index, breadth_20_series.values, alpha=0.1, color='#f1c40f')
    
    # 绘制阈值线
    ax.axhline(y=80, color='#ff5252', linestyle='--', linewidth=1, alpha=0.8) 
    ax.text(breadth_20_series.index[0], 81, 'Overbought (80%)', color='#ff5252', fontsize=8)
    
    ax.axhline(y=20, color='#448aff', linestyle='--', linewidth=1, alpha=0.8) 
    ax.text(breadth_20_series.index[0], 21, 'Oversold (20%)', color='#448aff', fontsize=8)
    
    ax.set_title('S&P 500 Market Breadth (20 & 50 Day SMA)', fontsize=12, color='white', pad=15)
    ax.set_ylim(0, 100)
    ax.grid(True, linestyle=':', alpha=0.3)
    
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    plt.xticks(rotation=0)
    ax.legend(loc='upper left', frameon=True, facecolor='#2f3136', edgecolor='#2f3136', labelcolor='white')
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=100, facecolor='#2b2d31')
    buf.seek(0)
    plt.close()
    return buf

def run_breadth_task():
    print("📊 启动市场广度统计 (布局调整版)...")
    
    try:
        # 1. 获取名单
        print("📥 获取成分股名单...")
        try:
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            
            tables = pd.read_html(io.StringIO(resp.text))
            df_tickers = None
            for df in tables:
                if 'Symbol' in df.columns:
                    df_tickers = df
                    break
            
            if df_tickers is None: raise ValueError("未找到表格")
            tickers = [t.replace('.', '-') for t in df_tickers['Symbol'].tolist()] 
            print(f"✅ 获取到 {len(tickers)} 只成分股")
        except:
            tickers = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'BRK-B', 'LLY', 'AVGO']

        # 2. 下载历史数据
        warnings.simplefilter(action='ignore', category=FutureWarning)
        print(f"📥 下载数据...")
        data = yf.download(tickers, period="1y", progress=False) 
        if 'Close' in data.columns: closes = data['Close']
        else: closes = data

        # 3. 计算历史广度
        sma20_df = closes.rolling(window=20).mean()
        sma50_df = closes.rolling(window=50).mean()
        
        above20_matrix = closes > sma20_df
        above50_matrix = closes > sma50_df
        
        daily_breadth_20 = (above20_matrix.sum(axis=1) / closes.notna().sum(axis=1)) * 100
        daily_breadth_50 = (above50_matrix.sum(axis=1) / closes.notna().sum(axis=1)) * 100
        
        # 画图数据
        recent_breadth_20 = daily_breadth_20.tail(252).dropna()
        recent_breadth_50 = daily_breadth_50.tail(252).dropna()
        
        # 最新值
        current_p20 = daily_breadth_20.iloc[-1]
        current_p50 = daily_breadth_50.iloc[-1]
        
        # 4. 生成图片
        chart_buffer = generate_breadth_chart(recent_breadth_20, recent_breadth_50)
        
        # 5. 发送 (Footer调整)
        sentiment_20 = get_market_sentiment(current_p20)
        sentiment_50 = get_market_sentiment(current_p50)

        payload_data = {
            "username": BREADTH_BOT_NAME,
            "avatar_url": BREADTH_BOT_AVATAR,
            "embeds": [{
                "title": "📊 S&P 500 市场广度",
                "description": f"**日期:** `{datetime.now().strftime('%Y-%m-%d')}`\n\n"
                               f"**股价 > 20日均线:** **{current_p20:.1f}%**\n"  # 改回正常加粗 **
                               f"{sentiment_20}\n"
                               f"*(短期趋势)*\n\n"
                               f"**股价 > 50日均线:** **{current_p50:.1f}%**\n"
                               f"{sentiment_50}\n"
                               f"*(中期趋势)*",
                "color": 0xF1C40F,
                "image": {"url": "attachment://chart.png"},
                "footer": {
                    # 这里是原来的统计样本位置，替换为小白说明
                    "text": "💡 比例越高，说明普涨；比例越低，说明大部分在跌。\n>80% 警惕回调，<20% 孕育反弹。"
                }
            }]
        }
        
        files = {
            'file': ('chart.png', chart_buffer, 'image/png')
        }
        
        requests.post(WEBHOOK_URL, data={'payload_json': json.dumps(payload_data)}, files=files)
        print(f"✅ 广度报告已推送: 20MA={current_p20:.1f}%, 50MA={current_p50:.1f}%")

    except Exception as e:
        print(f"❌ 广度任务异常: {e}")

# ==========================================
# 🚀 主程序
# ==========================================
if __name__ == "__main__":
    print("🚀 机器人启动 (Footer说明版)")
    
    print("🧪 启动测试：立即发送...")
    run_breadth_task()
    print("✅ 测试结束，进入监听...")

    last_run_time_str = ""
    while True:
        tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(tz)
        current_str = now_et.strftime("%H:%M")
        is_holiday, _ = is_market_holiday(now_et)

        if current_str != last_run_time_str:
            print(f"⏰ {current_str} ET (Holiday: {is_holiday})")
            
            if not is_holiday and current_str in FED_SCHEDULE_TIMES:
                data = get_fed_data()
                if data: send_fed_embed(data)
            
            if not is_holiday and current_str == BREADTH_SCHEDULE_TIME:
                run_breadth_task()
            
            last_run_time_str = current_str
        time.sleep(30)
