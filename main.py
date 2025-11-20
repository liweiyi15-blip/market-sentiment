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

# ------------------------------------------
# 🏛️ FedWatch 配置
# ------------------------------------------
FED_BOT_NAME = "CME FedWatch Bot"
# 使用 imgur 直链 (.png) 确保 Discord 能正确加载预览
FED_BOT_AVATAR = "https://i.imgur.com/E9KAPsn.png"

# ------------------------------------------
# 📊 市场广度 配置
# ------------------------------------------
BREADTH_BOT_NAME = "标普500 广度日报" 
BREADTH_BOT_AVATAR = "https://i.imgur.com/Segc5PF.png" 

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
    # 长进度条样式
    length = 15
    filled = int(p / 100 * length)
    return "█" * filled + "░" * (length - filled)

def get_market_sentiment(p):
    if p > 80: return "🔥🔥 **深度火热**"
    if p > 60: return "🔥 **火热**"      
    if p < 20: return "❄️❄️ **深度寒冷**"
    if p < 40: return "❄️ **寒冷**"      
    return "🍃 **稳定**"             

def format_target_label(target_str):
    """
    逻辑修正：
    当前利率为 3.75 - 4.00。
    因此 Lower Bound (下限) 为 3.75。
    """
    # 【关键修正】将基准线改为 3.75
    CURRENT_RATE_LOWER_BOUND = 3.75 
    
    try:
        # 获取目标区间的下限 (例如 "3.75 - 4.00" -> 3.75)
        lower_bound = float(target_str.split('-')[0].strip())
        
        if lower_bound < CURRENT_RATE_LOWER_BOUND:
            return f"{target_str} (降息)"
        elif lower_bound == CURRENT_RATE_LOWER_BOUND:
            return f"{target_str} (维持)" # 3.75 == 3.75 -> 维持
        else:
            return f"{target_str} (加息)"
    except:
        return target_str

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
    options.add_argument("user-agent=Mozilla/5.0")

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
    top2 = data['data'][1] if len(data['data']) > 1 else None
    
    current_prob = top1['prob']
    delta = 0.0
    if PREV_CUT_PROB is not None: delta = current_prob - PREV_CUT_PROB
    PREV_CUT_PROB = current_prob
    
    # 1. 趋势变动
    trend_text = "稳定"
    trend_icon = "⚖️"
    if delta > 1.0: 
        trend_text = f"概率上升 +{delta:.1f}%"
        trend_icon = "🔥"
    elif delta < -1.0: 
        trend_text = f"概率下降 {abs(delta):.1f}%"
        trend_icon = "❄️"
    
    # 2. 华尔街共识 (逻辑修正)
    # 重新调用 label 函数判断是 维持 还是 降息
    label1_raw = format_target_label(top1['target'])
    
    if "(维持)" in label1_raw:
        consensus_short = "⏸️ 维持利率 (Hold)"
    elif "(降息)" in label1_raw:
        consensus_short = "📉 降息 (Cut)"
    else:
        consensus_short = "📈 加息 (Hike)"

    # 3. 构建内容
    desc_lines = [f"**🗓️ 下次会议:** `{NEXT_MEETING_DATE}`\n"]
    
    desc_lines.append(f"**目标: {label1_raw}**")
    desc_lines.append(f"`{get_bar(top1['prob'])}` **{top1['prob']}%**\n")
    
    if top2:
        label2_raw = format_target_label(top2['target'])
        desc_lines.append(f"**目标: {label2_raw}**")
        desc_lines.append(f"`{get_bar(top2['prob'])}` **{top2['prob']}%**")
    
    desc_lines.append("\n------------------------")

    payload = {
        "username": FED_BOT_NAME,
        "avatar_url": FED_BOT_AVATAR,
        "embeds": [{
            "title": "🏛️ CME FedWatch™ (降息预期)",
            "description": "\n".join(desc_lines),
            "color": 0x3498DB,
            "fields": [
                {"name": f"{trend_icon} 趋势变动", "value": trend_text, "inline": True},
                {"name": "💡 华尔街共识", "value": consensus_short, "inline": True}
            ],
            "footer": {"text": f"Updated at {datetime.now().strftime('%H:%M')} ET"}
        }]
    }
    try: requests.post(WEBHOOK_URL, json=payload)
    except Exception as e: print(f"❌ 推送失败: {e}")

# ==========================================
# 🔵 模块 2: 市场广度 (已修复文案)
# ==========================================
def generate_breadth_chart(breadth_20_series, breadth_50_series):
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    
    ax.plot(breadth_20_series.index, breadth_20_series.values, 
            color='#f1c40f', linewidth=2, label='Stocks > 20 Day SMA %')
    
    ax.plot(breadth_50_series.index, breadth_50_series.values, 
            color='#e74c3c', linewidth=2, label='Stocks > 50 Day SMA %')
    
    ax.fill_between(breadth_20_series.index, breadth_20_series.values, alpha=0.1, color='#f1c40f')
    
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
    print("📊 启动市场广度统计...")
    try:
        try:
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=10)
            tables = pd.read_html(io.StringIO(resp.text))
            df_tickers = next((df for df in tables if 'Symbol' in df.columns), None)
            if df_tickers is None: raise ValueError("未找到表格")
            tickers = [t.replace('.', '-') for t in df_tickers['Symbol'].tolist()] 
        except:
            print("⚠️ Wikipedia 获取失败，使用备用列表")
            tickers = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'BRK-B', 'LLY', 'AVGO']

        warnings.simplefilter(action='ignore', category=FutureWarning)
        data = yf.download(tickers, period="1y", progress=False) 
        if 'Close' in data.columns: closes = data['Close']
        else: closes = data

        sma20_df = closes.rolling(window=20).mean()
        sma50_df = closes.rolling(window=50).mean()
        
        daily_breadth_20 = ((closes > sma20_df).sum(axis=1) / closes.notna().sum(axis=1)) * 100
        daily_breadth_50 = ((closes > sma50_df).sum(axis=1) / closes.notna().sum(axis=1)) * 100
        
        chart_buffer = generate_breadth_chart(daily_breadth_20.tail(252), daily_breadth_50.tail(252))
        
        current_p20 = daily_breadth_20.iloc[-1]
        current_p50 = daily_breadth_50.iloc[-1]
        
        sentiment_20 = get_market_sentiment(current_p20)
        sentiment_50 = get_market_sentiment(current_p50)

        payload_data = {
            "username": BREADTH_BOT_NAME,
            "avatar_url": BREADTH_BOT_AVATAR,
            "embeds": [{
                "title": "S&P 500 市场广度",
                "description": f"**日期:** `{datetime.now().strftime('%Y-%m-%d')}`\n\n"
                               f"**股价 > 20日均线:** **{current_p20:.1f}%**\n"
                               f"{sentiment_20}\n\n"
                               f"**股价 > 50日均线:** **{current_p50:.1f}%**\n"
                               f"{sentiment_50}",
                "color": 0xF1C40F,
                "image": {"url": "attachment://chart.png"},
                "footer": {
                    # 【修正】加上了 "只" 字
                    "text": f"💡 标普500大于20日、50日均的数量\n💡 >80% 警惕回调，<20% 孕育反弹。\n（统计样本: {len(tickers)}只成分股）"
                }
            }]
        }
        
        files = {'file': ('chart.png', chart_buffer, 'image/png')}
        requests.post(WEBHOOK_URL, data={'payload_json': json.dumps(payload_data)}, files=files)
        print(f"✅ 广度报告已推送")

    except Exception as e:
        print(f"❌ 广度任务异常: {e}")

# ==========================================
# 🚀 主程序 (含启动自测)
# ==========================================
if __name__ == "__main__":
    print("🚀 监控服务已启动")
    
    print("🧪 [测试] 正在发送 FedWatch (检查头像 + 3.75维持逻辑)...")
    fed_data = get_fed_data()
    if fed_data: 
        send_fed_embed(fed_data)
        print("✅ FedWatch 测试完成")
    else:
        print("⚠️ FedWatch 获取失败")

    print("🧪 [测试] 正在发送 市场广度 (检查'只'字)...")
    run_breadth_task()
    
    print("✅ 所有测试结束，进入定时监听模式...")

    last_run_time_str = ""
    while True:
        tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(tz)
        current_str = now_et.strftime("%H:%M")
        is_holiday, _ = is_market_holiday(now_et)

        if current_str != last_run_time_str:
            print(f"⏰ {current_str} ET")
            
            if not is_holiday and current_str in FED_SCHEDULE_TIMES:
                data = get_fed_data()
                if data: send_fed_embed(data)
            
            if not is_holiday and current_str == BREADTH_SCHEDULE_TIME:
                run_breadth_task()
            
            last_run_time_str = current_str
        
        time.sleep(30)
