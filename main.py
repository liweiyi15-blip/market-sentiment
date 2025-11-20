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

def get_market_status(p):
    if p > 80: return "🔥 **市场火热**"
    if p < 20: return "❄️ **市场冰冷**"
    return ""

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
# 🔵 模块 2: 市场广度 (折线图版)
# ==========================================
def generate_breadth_chart(breadth_series):
    """生成市场广度折线图"""
    # 设置绘图风格 (类似 Discord 深色模式)
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    
    # 绘制数据线
    ax.plot(breadth_series.index, breadth_series.values, color='#f1c40f', linewidth=2, label='Stocks > 50SMA %')
    
    # 填充颜色 (下方淡黄)
    ax.fill_between(breadth_series.index, breadth_series.values, alpha=0.1, color='#f1c40f')
    
    # 绘制阈值线
    ax.axhline(y=80, color='#ff5252', linestyle='--', linewidth=1, alpha=0.8) # 80% 火热
    ax.text(breadth_series.index[0], 81, 'Overbought (80%)', color='#ff5252', fontsize=8)
    
    ax.axhline(y=20, color='#448aff', linestyle='--', linewidth=1, alpha=0.8) # 20% 冰冷
    ax.text(breadth_series.index[0], 21, 'Oversold (20%)', color='#448aff', fontsize=8)
    
    # 格式化
    ax.set_title('S&P 500 Market Breadth (Stocks > 50 Day SMA)', fontsize=12, color='white', pad=15)
    ax.set_ylim(0, 100)
    ax.grid(True, linestyle=':', alpha=0.3)
    
    # 日期格式
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    plt.xticks(rotation=0)
    
    # 保存到内存
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=100, facecolor='#2b2d31')
    buf.seek(0)
    plt.close()
    return buf

def run_breadth_task():
    print("📊 启动市场广度统计 (含历史回溯)...")
    
    try:
        # 1. 获取名单
        print("📥 获取成分股名单...")
        try:
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            resp = requests.get(url, headers=headers, timeout=10)
            tables = pd.read_html(io.StringIO(resp.text))
            
            df_tickers = None
            for df in tables:
                if 'Symbol' in df.columns:
                    df_tickers = df
                    break
            if df_tickers is None: raise ValueError("No Symbol table found")
            
            tickers = [t.replace('.', '-') for t in df_tickers['Symbol'].tolist()]
            print(f"✅ 获取到 {len(tickers)} 只成分股")
        except:
            tickers = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'BRK-B', 'LLY', 'AVGO']

        # 2. 下载历史数据 (2年数据，确保能算出过去1年的200日均线)
        warnings.simplefilter(action='ignore', category=FutureWarning)
        print("📥 下载历史数据 (这可能需要 30-60 秒)...")
        
        # 下载 Close 价格
        data = yf.download(tickers, period="2y", progress=False)
        if 'Close' in data.columns: closes = data['Close']
        else: closes = data
            
        print("✅ 数据下载完成，开始全量回测计算...")

        # 3. 计算历史广度 (矩阵运算)
        # 计算所有股票每一天的 50日均线 & 200日均线
        sma50_df = closes.rolling(window=50).mean()
        sma200_df = closes.rolling(window=200).mean()
        
        # 比较：收盘价 > 均线 (得到 True/False 矩阵)
        above50_matrix = closes > sma50_df
        above200_matrix = closes > sma200_df
        
        # 按行求和 (每天有多少个True) / 有效列数
        # count(axis=1) 计算每天有多少只股票有数据 (排除停牌/未上市)
        daily_breadth_50 = (above50_matrix.sum(axis=1) / closes.notna().sum(axis=1)) * 100
        daily_breadth_200 = (above200_matrix.sum(axis=1) / closes.notna().sum(axis=1)) * 100
        
        # 取最近一年的数据用于画图，取最新一天的数据用于报告
        recent_breadth_50 = daily_breadth_50.tail(252) # 约1年交易日
        
        current_p50 = daily_breadth_50.iloc[-1]
        current_p200 = daily_breadth_200.iloc[-1]
        
        # 4. 生成图片
        chart_buffer = generate_breadth_chart(recent_breadth_50)
        
        # 5. 发送 (带附件的复杂请求)
        status_50 = get_market_status(current_p50)
        status_200 = get_market_status(current_p200)

        # 构造 multipart/form-data
        payload_data = {
            "username": BREADTH_BOT_NAME,
            "avatar_url": BREADTH_BOT_AVATAR,
            "embeds": [{
                "title": "📊 S&P 500 市场广度",
                "description": f"**日期:** `{datetime.now().strftime('%Y-%m-%d')}`\n\n"
                               f"**股价 > 50日均线:** **{current_p50:.1f}%** {status_50}\n"
                               f"*(中期趋势)*\n\n"
                               f"**股价 > 200日均线:** **{current_p200:.1f}%** {status_200}\n"
                               f"*(长期牛熊)*",
                "color": 0xF1C40F,
                "image": {"url": "attachment://chart.png"}, # 引用附件
                "footer": {"text": f"统计样本: {len(tickers)} 只成分股"}
            }]
        }
        
        files = {
            'file': ('chart.png', chart_buffer, 'image/png')
        }
        
        # Discord Webhook 发附件需要把 JSON 放在 'payload_json' 字段里
        requests.post(WEBHOOK_URL, data={'payload_json': json.dumps(payload_data)}, files=files)
        print(f"✅ 广度报告(含图表)已推送: >50MA={current_p50:.1f}%")

    except Exception as e:
        print(f"❌ 广度任务异常: {e}")

# ==========================================
# 🚀 主程序
# ==========================================
if __name__ == "__main__":
    print("🚀 机器人启动 (历史折线图版)")
    
    print("🧪 启动测试：生成并发送图表...")
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
